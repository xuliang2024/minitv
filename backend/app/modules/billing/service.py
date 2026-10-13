"""Plan catalog and the fake payment webhook used until a real provider is chosen."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.billing.models import Order, Plan, PlanVersion, Subscription
from app.modules.common.models import AuditLog

DEFAULT_PLANS = [
    ("weekly", "Weekly", "周卡", 999, False, ["Unlock all", "No ads", "1080p"]),
    ("monthly", "Monthly", "月卡", 1999, True, ["Weekly benefits", "Download"]),
    ("yearly", "Yearly", "年卡", 7999, False, ["Monthly benefits", "Priority support", "Save 58%"]),
]


class BillingError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def pay_secret() -> bytes:
    return os.environ.get("PAY_WEBHOOK_SECRET", "local-pay-webhook").encode()


def sign_webhook(body: bytes) -> str:
    return hmac.new(pay_secret(), body, hashlib.sha256).hexdigest()


def ensure_default_plans(session: Session) -> None:
    if session.scalar(select(Plan).limit(1)):
        return
    for code, en, zh, cents, best, benefits in DEFAULT_PLANS:
        plan = Plan(code=code, name_en=en, name_zh=zh)
        session.add(plan)
        session.flush()
        session.add(
            PlanVersion(
                plan_id=plan.id,
                version=1,
                price_cents=cents,
                benefits={"items": benefits, "best": best},
            )
        )
    session.commit()


def list_current_plans(session: Session) -> list[dict]:
    plans = session.scalars(select(Plan).where(Plan.status == "active").order_by(Plan.id)).all()
    out = []
    for plan in plans:
        version = session.scalar(
            select(PlanVersion).where(PlanVersion.plan_id == plan.id).order_by(PlanVersion.version.desc())
        )
        if version is None:
            continue
        out.append(
            {
                "code": plan.code,
                "name_en": plan.name_en,
                "name_zh": plan.name_zh,
                "price_cents": version.price_cents,
                "currency": version.currency,
                "version": version.version,
                "version_id": version.id,
                "benefits": version.benefits,
            }
        )
    return out


def change_price(session: Session, *, code: str, price_cents: int, benefits: dict, actor_id: int) -> PlanVersion:
    plan = session.scalar(select(Plan).where(Plan.code == code))
    if plan is None:
        raise BillingError(404, "plan not found")
    latest = session.scalar(
        select(PlanVersion).where(PlanVersion.plan_id == plan.id).order_by(PlanVersion.version.desc())
    )
    version = PlanVersion(
        plan_id=plan.id,
        version=(latest.version if latest else 0) + 1,
        price_cents=price_cents,
        benefits=benefits,
    )
    session.add(version)
    session.flush()
    session.add(
        AuditLog(
            actor_type="admin",
            actor_id=actor_id,
            action="plan_price_change",
            target_type="plan",
            target_id=plan.id,
            payload={"version": version.version, "price_cents": price_cents},
        )
    )
    session.commit()
    return version


def apply_webhook(session: Session, *, body: bytes, signature: str, payload: dict) -> dict:
    expected = sign_webhook(body)
    if not hmac.compare_digest(expected, signature or ""):
        raise BillingError(401, "bad signature")
    txn = payload["txn_id"]
    existing = session.scalar(select(Order).where(Order.external_txn_id == txn))
    if existing is not None:
        return {"order_no": existing.order_no, "replayed": True, "status": existing.status}
    plan = session.scalar(select(Plan).where(Plan.code == payload["plan_code"]))
    if plan is None:
        raise BillingError(404, "plan not found")
    version = session.scalar(
        select(PlanVersion).where(PlanVersion.plan_id == plan.id).order_by(PlanVersion.version.desc())
    )
    now = datetime.now(timezone.utc)
    days = {"weekly": 7, "monthly": 30, "yearly": 365}.get(plan.code, 30)
    order = Order(
        order_no=secrets.token_hex(8),
        user_id=payload["user_id"],
        plan_version_id=version.id,
        amount_cents=version.price_cents,
        status="paid",
        external_txn_id=txn,
        paid_at=now,
    )
    session.add(order)
    current = session.scalar(
        select(Subscription).where(Subscription.user_id == payload["user_id"], Subscription.status == "active")
    )
    if current is not None:
        current.status = "canceled"
        current.canceled_at = now
    session.add(
        Subscription(
            user_id=payload["user_id"],
            plan_id=plan.id,
            plan_version_id=version.id,
            status="active",
            current_period_start=now,
            current_period_end=now + timedelta(days=days),
        )
    )
    session.commit()
    return {"order_no": order.order_no, "replayed": False, "status": "paid", "plan_version_id": version.id}


def cancel_subscription(session: Session, user_id: int) -> None:
    current = session.scalar(
        select(Subscription).where(Subscription.user_id == user_id, Subscription.status == "active")
    )
    if current is None:
        return
    current.status = "canceled"
    current.canceled_at = datetime.now(timezone.utc)
    session.commit()
