"""Plan catalog, Stripe Checkout and the legacy fake webhook.

Stripe owns the money: the client opens a Checkout Session, and everything
that changes entitlement comes back through a signed webhook. The local row
is the source of truth for "is this user a subscriber right now", so the
webhook has to be idempotent — every handler re-applies a full snapshot
instead of incrementing anything.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
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


def active_membership(session: Session, user_id: int) -> dict:
    """What /v1/me shows: an unexpired active subscription, if any."""
    row = session.execute(
        select(Subscription, Plan)
        .join(Plan, Plan.id == Subscription.plan_id)
        .where(
            Subscription.user_id == user_id,
            Subscription.status == "active",
            Subscription.current_period_end > datetime.now(timezone.utc),
        )
    ).first()
    if row is None:
        return {"active": False, "plan_code": None, "until": None, "canceling": False}
    subscription, plan = row
    return {
        "active": True,
        "plan_code": plan.code,
        "until": subscription.current_period_end.isoformat(),
        "canceling": subscription.canceled_at is not None,
    }


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
    """Stop renewing. With Stripe the access runs until the paid period ends."""
    current = session.scalar(
        select(Subscription).where(Subscription.user_id == user_id, Subscription.status == "active")
    )
    if current is None:
        return
    if current.stripe_subscription_id:
        _stripe_request(
            f"/subscriptions/{current.stripe_subscription_id}",
            {"cancel_at_period_end": "true"},
            method="POST",
        )
        session.commit()
        return
    current.status = "canceled"
    current.canceled_at = datetime.now(timezone.utc)
    session.commit()


# --------------------------------------------------------------- stripe


STRIPE_API = "https://api.stripe.com/v1"
STRIPE_WEBHOOK_TOLERANCE = 300
PRICE_ENV = {
    "weekly": "STRIPE_PRICE_WEEKLY",
    "monthly": "STRIPE_PRICE_MONTHLY",
    "yearly": "STRIPE_PRICE_YEARLY",
}
PLAN_BY_PRICE: dict[str, str] = {}


def stripe_secret() -> str:
    key = os.environ.get("STRIPE_SECRET_KEY", "").strip()
    if not key:
        raise BillingError(503, "stripe is not configured")
    return key


def stripe_mode() -> str:
    return "live" if stripe_secret().startswith("sk_live_") else "test"


def price_id_for(code: str) -> str:
    env_key = PRICE_ENV.get(code)
    if env_key is None:
        raise BillingError(404, "plan not found")
    price = os.environ.get(env_key, "").strip()
    if not price:
        raise BillingError(503, f"stripe price for {code} is not configured")
    return price


def plan_code_for_price(price_id: str | None, fallback: str | None = None) -> str | None:
    if fallback:
        return fallback
    if not price_id:
        return None
    for code, env_key in PRICE_ENV.items():
        if os.environ.get(env_key, "").strip() == price_id:
            return code
    return None


def _stripe_request(path: str, data: dict | None = None, method: str = "POST") -> dict:
    """Minimal Stripe REST call. Only the handful of fields we need."""
    payload = urllib.parse.urlencode(data, doseq=True).encode() if data else None
    if payload is None:
        method = "GET"
    request = urllib.request.Request(f"{STRIPE_API}{path}", data=payload, method=method)
    request.add_header("Authorization", f"Bearer {stripe_secret()}")
    request.add_header("User-Agent", "minitv-billing/1.0")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode())
            message = detail.get("error", {}).get("message", "")
        except Exception:  # noqa: BLE001 - never mask the HTTP status
            message = ""
        raise BillingError(502, f"stripe rejected the request: {message or exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise BillingError(502, "stripe is unreachable") from exc


def _latest_version(session: Session, plan: Plan) -> PlanVersion:
    version = session.scalar(
        select(PlanVersion).where(PlanVersion.plan_id == plan.id).order_by(PlanVersion.version.desc())
    )
    if version is None:
        raise BillingError(500, "plan has no price version")
    return version


def create_checkout_session(
    session: Session,
    *,
    user_id: int,
    email: str | None,
    plan_code: str,
    success_url: str | None = None,
    cancel_url: str | None = None,
) -> dict:
    plan = session.scalar(select(Plan).where(Plan.code == plan_code))
    if plan is None or plan.status != "active":
        raise BillingError(404, "plan not found")
    version = _latest_version(session, plan)

    base = os.environ.get("H5_PUBLIC_BASE_URL", "https://h5.minitv.us").rstrip("/")
    data = {
        "mode": "subscription",
        "line_items[0][price]": price_id_for(plan_code),
        "line_items[0][quantity]": "1",
        "success_url": success_url or f"{base}/?checkout=success",
        "cancel_url": cancel_url or f"{base}/?checkout=cancel",
        "client_reference_id": str(user_id),
        "allow_promotion_codes": "true",
        "metadata[user_id]": str(user_id),
        "metadata[plan_code]": plan_code,
        "subscription_data[metadata][user_id]": str(user_id),
        "subscription_data[metadata][plan_code]": plan_code,
    }
    if email:
        data["customer_email"] = email
    checkout = _stripe_request("/checkout/sessions", data)

    order = Order(
        order_no=secrets.token_hex(8),
        user_id=user_id,
        plan_version_id=version.id,
        amount_cents=version.price_cents,
        currency=version.currency,
        status="created",
        external_txn_id=checkout["id"],
    )
    session.add(order)
    session.commit()
    return {"url": checkout["url"], "session_id": checkout["id"], "order_no": order.order_no}


def verify_stripe_signature(body: bytes, header: str) -> None:
    """Stripe-Signature: t=...,v1=...,v1=... signed over "<t>.<body>"."""
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
    if not secret:
        raise BillingError(503, "stripe webhook is not configured")
    stamps: list[int] = []
    signatures: list[str] = []
    for part in (header or "").split(","):
        key, _, value = part.partition("=")
        if key == "t":
            try:
                stamps.append(int(value))
            except ValueError:
                continue
        elif key == "v1":
            signatures.append(value)
    if not stamps or not signatures:
        raise BillingError(400, "missing stripe signature")
    if abs(time.time() - max(stamps)) > STRIPE_WEBHOOK_TOLERANCE:
        raise BillingError(400, "stripe signature expired")
    expected = hmac.new(secret.encode(), f"{max(stamps)}.".encode() + body, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, candidate) for candidate in signatures):
        raise BillingError(401, "bad stripe signature")


def _upsert_subscription(
    session: Session,
    *,
    user_id: int,
    plan_code: str | None,
    stripe_subscription_id: str | None,
    stripe_customer_id: str | None,
    stripe_price_id: str | None,
    status: str,
    period_start: datetime,
    period_end: datetime,
) -> Subscription | None:
    plan = session.scalar(select(Plan).where(Plan.code == plan_code)) if plan_code else None
    if plan is None:
        return None
    version = _latest_version(session, plan)
    current = None
    if stripe_subscription_id:
        current = session.scalar(
            select(Subscription).where(Subscription.stripe_subscription_id == stripe_subscription_id)
        )
    if current is None:
        # One active subscription per user (partial unique index): retire the old row first.
        current = session.scalar(
            select(Subscription).where(Subscription.user_id == user_id, Subscription.status == "active")
        )
    if current is None:
        current = Subscription(user_id=user_id, plan_id=plan.id, plan_version_id=version.id)
        session.add(current)
    elif current.plan_id != plan.id:
        current.plan_id = plan.id
        current.plan_version_id = version.id
    current.status = status
    current.current_period_start = period_start
    current.current_period_end = period_end
    current.stripe_subscription_id = stripe_subscription_id or current.stripe_subscription_id
    current.stripe_customer_id = stripe_customer_id or current.stripe_customer_id
    current.stripe_price_id = stripe_price_id or current.stripe_price_id
    if status == "active":
        current.canceled_at = None
    return current


def _as_datetime(epoch: int | None, fallback: datetime | None = None) -> datetime:
    if epoch:
        return datetime.fromtimestamp(int(epoch), tz=timezone.utc)
    return fallback or datetime.now(timezone.utc)


def _activate_from_checkout(session: Session, checkout: dict) -> dict:
    metadata = checkout.get("metadata") or {}
    user_id = int(metadata.get("user_id") or checkout.get("client_reference_id") or 0)
    plan_code = metadata.get("plan_code")
    if not user_id or not plan_code:
        raise BillingError(400, "checkout session is missing our metadata")

    order = session.scalar(select(Order).where(Order.external_txn_id == checkout.get("id")))
    if order is None:
        plan = session.scalar(select(Plan).where(Plan.code == plan_code))
        if plan is None:
            raise BillingError(404, "plan not found")
        order = Order(
            order_no=secrets.token_hex(8),
            user_id=user_id,
            plan_version_id=_latest_version(session, plan).id,
            amount_cents=int(checkout.get("amount_total") or 0),
            currency=(checkout.get("currency") or "usd").upper(),
            external_txn_id=checkout.get("id"),
        )
        session.add(order)
    order.status = "paid"
    order.paid_at = datetime.now(timezone.utc)

    subscription_id = checkout.get("subscription")
    customer_id = checkout.get("customer")
    price_id = None
    period_start = datetime.now(timezone.utc)
    period_end = period_start + timedelta(days={"weekly": 7, "monthly": 30, "yearly": 365}.get(plan_code, 30))
    if subscription_id:
        remote = _stripe_request(f"/subscriptions/{subscription_id}")
        price_id = ((remote.get("items") or {}).get("data") or [{}])[0].get("price", {}).get("id")
        period_start = _as_datetime(remote.get("current_period_start"), period_start)
        period_end = _as_datetime(remote.get("current_period_end"), period_end)
        customer_id = remote.get("customer") or customer_id
        plan_code = (remote.get("metadata") or {}).get("plan_code") or plan_code
    _upsert_subscription(
        session,
        user_id=user_id,
        plan_code=plan_code,
        stripe_subscription_id=subscription_id,
        stripe_customer_id=customer_id,
        stripe_price_id=price_id,
        status="active",
        period_start=period_start,
        period_end=period_end,
    )
    session.commit()
    return {"order_no": order.order_no, "status": order.status, "user_id": user_id}


def _sync_from_subscription(session: Session, remote: dict) -> dict:
    metadata = remote.get("metadata") or {}
    user_id = int(metadata.get("user_id") or 0)
    price_id = ((remote.get("items") or {}).get("data") or [{}])[0].get("price", {}).get("id")
    plan_code = metadata.get("plan_code") or plan_code_for_price(price_id)
    existing = session.scalar(
        select(Subscription).where(Subscription.stripe_subscription_id == remote.get("id"))
    )
    if existing is not None:
        user_id = existing.user_id
    if not user_id:
        raise BillingError(400, "subscription is missing our user id")

    status = remote.get("status")
    local_status = "active" if status in {"active", "trialing", "past_due"} else "canceled"
    period_start = _as_datetime(remote.get("current_period_start"))
    period_end = _as_datetime(remote.get("current_period_end"))
    row = _upsert_subscription(
        session,
        user_id=user_id,
        plan_code=plan_code,
        stripe_subscription_id=remote.get("id"),
        stripe_customer_id=remote.get("customer"),
        stripe_price_id=price_id,
        status=local_status,
        period_start=period_start,
        period_end=period_end,
    )
    if row is not None and local_status == "canceled":
        row.canceled_at = row.canceled_at or datetime.now(timezone.utc)
    session.commit()
    return {"user_id": user_id, "status": local_status}


def apply_stripe_event(session: Session, *, body: bytes, signature: str, payload: dict) -> dict:
    verify_stripe_signature(body, signature)
    event_type = payload.get("type") or ""
    obj = (payload.get("data") or {}).get("object") or {}
    if event_type == "checkout.session.completed":
        result = _activate_from_checkout(session, obj)
    elif event_type in {"customer.subscription.created", "customer.subscription.updated"}:
        result = _sync_from_subscription(session, obj)
    elif event_type == "customer.subscription.deleted":
        row = session.scalar(
            select(Subscription).where(Subscription.stripe_subscription_id == obj.get("id"))
        )
        if row is not None:
            row.status = "canceled"
            row.canceled_at = datetime.now(timezone.utc)
            session.commit()
        result = {"canceled": bool(row)}
    else:
        result = {"ignored": event_type}
    return {"received": True, "type": event_type, **result}

