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

from app.modules.billing.models import BillingSettings, Order, Plan, PlanVersion, Subscription
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
    mode = billing_mode(session)
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
                "billing_mode": mode,
            }
        )
    return out


BILLING_MODES = ("subscription", "one_time")


def get_billing_settings(session: Session) -> BillingSettings:
    row = session.get(BillingSettings, 1)
    if row is None:
        row = BillingSettings(id=1, billing_mode="one_time")
        session.add(row)
        session.commit()
    return row


def billing_mode(session: Session) -> str:
    """How *new* purchases are billed. Global, one switch for every plan."""
    return get_billing_settings(session).billing_mode


def set_billing_mode(session: Session, mode: str, actor_id: int) -> BillingSettings:
    if mode not in BILLING_MODES:
        raise BillingError(400, "unknown billing mode")
    row = get_billing_settings(session)
    row.billing_mode = mode
    row.updated_by = actor_id
    session.commit()
    return row


def provision_prices_for_mode(session: Session, mode: str) -> list[dict]:
    """Best effort: make sure every plan has a Stripe price of that type."""
    out = []
    plans = session.scalars(select(Plan).where(Plan.status == "active")).all()
    for plan in plans:
        version = _latest_version(session, plan)
        try:
            price_id = ensure_stripe_price(session, plan, version, mode)
            out.append({"code": plan.code, "price_id": price_id, "ok": True})
        except BillingError as exc:
            out.append({"code": plan.code, "ok": False, "error": exc.detail})
    return out


def _as_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def access_until(session: Session, user_id: int) -> datetime | None:
    """Latest moment this user is entitled: subscription period or pass end."""
    row = session.execute(
        select(Subscription).where(
            Subscription.user_id == user_id,
            Subscription.status == "active",
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    now = datetime.now(timezone.utc)
    candidates = [
        end
        for end in (_as_aware(row.current_period_end), _as_aware(row.pass_end))
        if end is not None and end > now
    ]
    return max(candidates) if candidates else None


def list_user_orders(session: Session, user_id: int, limit: int = 20, offset: int = 0) -> dict:
    """Purchase history for the H5 "Orders" sheet, newest first."""
    rows = session.execute(
        select(Order, Plan)
        .join(PlanVersion, PlanVersion.id == Order.plan_version_id)
        .join(Plan, Plan.id == PlanVersion.plan_id)
        # Abandoned checkouts are noise in a purchase history: only money that
        # actually moved (paid, or paid then refunded) belongs here.
        .where(Order.user_id == user_id, Order.status.in_(("paid", "refunded")))
        .order_by(Order.created_at.desc(), Order.id.desc())
        .offset(offset)
        .limit(limit + 1)
    ).all()
    has_more = len(rows) > limit
    items = []
    for order, plan in rows[:limit]:
        items.append(
            {
                "order_no": order.order_no,
                "plan_code": plan.code,
                "plan_name_en": plan.name_en,
                "plan_name_zh": plan.name_zh,
                "amount_cents": order.amount_cents,
                "currency": order.currency,
                "billing_mode": order.billing_mode,
                "status": order.status,
                "paid_at": order.paid_at.isoformat() if order.paid_at else None,
                "created_at": order.created_at.isoformat(),
            }
        )
    return {"items": items, "has_more": has_more}


def active_membership(session: Session, user_id: int) -> dict:
    """What /v1/me shows: whichever access window ends last."""
    row = session.execute(
        select(Subscription, Plan)
        .join(Plan, Plan.id == Subscription.plan_id)
        .where(
            Subscription.user_id == user_id,
            Subscription.status == "active",
        )
    ).first()
    if row is None:
        return {
            "active": False,
            "plan_code": None,
            "until": None,
            "canceling": False,
            "billing_mode": None,
            "subscription_until": None,
            "pass_until": None,
        }
    subscription, plan = row
    now = datetime.now(timezone.utc)
    sub_end = _as_aware(subscription.current_period_end)
    pass_end = _as_aware(subscription.pass_end)
    live = [end for end in (sub_end, pass_end) if end is not None and end > now]
    if not live:
        return {
            "active": False,
            "plan_code": None,
            "until": None,
            "canceling": False,
            "billing_mode": None,
            "subscription_until": sub_end.isoformat() if sub_end else None,
            "pass_until": pass_end.isoformat() if pass_end else None,
        }
    until = max(live)
    mode = "one_time" if pass_end == until and (sub_end is None or pass_end > sub_end) else "subscription"
    return {
        "active": True,
        "plan_code": plan.code,
        "until": until.isoformat(),
        # "Won't renew" only makes sense while a subscription is the live window.
        "canceling": mode == "subscription" and subscription.canceled_at is not None and sub_end is not None and sub_end > now,
        "billing_mode": mode,
        "subscription_until": sub_end.isoformat() if sub_end else None,
        "pass_until": pass_end.isoformat() if pass_end else None,
    }


def change_price(session: Session, *, code: str, price_cents: int, benefits: dict, actor_id: int) -> PlanVersion:
    plan = session.scalar(select(Plan).where(Plan.code == code))
    ensure_chargeable(billing_mode(session), price_cents, "USD")
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
    # Best effort: materialize the Stripe price now so a bad key/amount shows up
    # here instead of at the first purchase. A failure is retried on checkout.
    # Provision the *current* billing mode (a one-time shop needs one-time prices).
    try:
        ensure_stripe_price(session, plan, version, billing_mode(session))
    except BillingError:
        session.rollback()
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
    if not current.stripe_subscription_id and current.billing_mode == "one_time":
        raise BillingError(400, "one-time passes cannot be canceled")
    if current.stripe_subscription_id:
        _stripe_request(
            f"/subscriptions/{current.stripe_subscription_id}",
            {"cancel_at_period_end": "true"},
            method="POST",
        )
        # Access continues to current_period_end; canceled_at only marks "will not renew".
        current.canceled_at = datetime.now(timezone.utc)
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
PRODUCT_ENV = {
    "weekly": "STRIPE_PRODUCT_WEEKLY",
    "monthly": "STRIPE_PRODUCT_MONTHLY",
    "yearly": "STRIPE_PRODUCT_YEARLY",
}
INTERVAL_OF = {"weekly": "week", "monthly": "month", "yearly": "year"}
# Stripe refuses one-time Checkout sessions below 50 cents (USD). Subscriptions
# are exempt, which is why the $0.10 test price only worked in subscription mode.
MIN_ONE_TIME_CENTS = 50
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


def ensure_stripe_price(
    session: Session, plan: Plan, version: PlanVersion, mode: str = "subscription"
) -> str:
    """The Stripe price of the right *type* for this plan version.

    A plan version can carry two prices: recurring (subscriptions) and one-time
    (passes). Stripe prices are immutable and typed, so switching the global
    billing mode means provisioning the other variant, not editing a price.
    """
    if mode not in BILLING_MODES:
        raise BillingError(400, "unknown billing mode")
    if mode == "one_time":
        if version.stripe_one_time_price_id:
            return version.stripe_one_time_price_id
    elif version.stripe_price_id:
        return version.stripe_price_id

    env_price = os.environ.get(PRICE_ENV.get(plan.code, ""), "").strip()
    if mode == "subscription" and version.version == 1 and env_price:
        # Bootstrap: version 1 was created alongside a dashboard price.
        version.stripe_price_id = env_price
        session.commit()
        return env_price

    product = os.environ.get(PRODUCT_ENV.get(plan.code, ""), "").strip()
    if not product:
        raise BillingError(503, f"stripe product for {plan.code} is not configured")
    payload = {
        "product": product,
        "unit_amount": version.price_cents,
        "currency": (version.currency or "USD").lower(),
        "nickname": (
            f"{plan.name_en} v{version.version} ${version.price_cents / 100:.2f}"
            + (" one-time" if mode == "one_time" else "")
        ),
    }
    if mode == "subscription":
        payload["recurring[interval]"] = INTERVAL_OF.get(plan.code, "month")
    price = _stripe_request("/prices", payload)
    if mode == "one_time":
        version.stripe_one_time_price_id = price["id"]
    else:
        version.stripe_price_id = price["id"]
    session.commit()
    return price["id"]


def ensure_chargeable(mode: str, price_cents: int, currency: str | None) -> None:
    if mode != "one_time":
        return
    if (currency or "USD").upper() != "USD":
        return
    if price_cents < MIN_ONE_TIME_CENTS:
        raise BillingError(
            400,
            f"one-time price must be at least ${MIN_ONE_TIME_CENTS / 100:.2f} (Stripe minimum)",
        )


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

    mode = billing_mode(session)
    ensure_chargeable(mode, version.price_cents, version.currency)
    base = os.environ.get("H5_PUBLIC_BASE_URL", "https://h5.minitv.us").rstrip("/")
    data = {
        "mode": "payment" if mode == "one_time" else "subscription",
        "line_items[0][price]": ensure_stripe_price(session, plan, version, mode),
        "line_items[0][quantity]": "1",
        "success_url": success_url or f"{base}/?checkout=success",
        "cancel_url": cancel_url or f"{base}/?checkout=cancel",
        "client_reference_id": str(user_id),
        "allow_promotion_codes": "true",
        "metadata[user_id]": str(user_id),
        "metadata[plan_code]": plan_code,
        "metadata[billing_mode]": mode,
    }
    if mode == "one_time":
        data["payment_intent_data[metadata][user_id]"] = str(user_id)
        data["payment_intent_data[metadata][plan_code]"] = plan_code
    else:
        data["subscription_data[metadata][user_id]"] = str(user_id)
        data["subscription_data[metadata][plan_code]"] = plan_code

    # Reuse the Stripe customer we already have (from a subscription) so passes
    # and subscriptions live under one customer; otherwise let Checkout create one.
    customer_id = session.scalar(
        select(Subscription.stripe_customer_id)
        .where(Subscription.user_id == user_id, Subscription.stripe_customer_id.is_not(None))
        .order_by(Subscription.id.desc())
    )
    if mode == "one_time":
        if customer_id:
            data["customer"] = customer_id
        else:
            data["customer_creation"] = "always"
            if email:
                data["customer_email"] = email
    elif email:
        data["customer_email"] = email
    checkout = _stripe_request("/checkout/sessions", data)

    order = Order(
        order_no=secrets.token_hex(8),
        user_id=user_id,
        plan_version_id=version.id,
        amount_cents=version.price_cents,
        currency=version.currency,
        billing_mode=mode,
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


def _period_bounds(remote: dict, plan_code: str | None = None) -> tuple[datetime, datetime]:
    """Billing period of a Stripe subscription.

    Stripe moved ``current_period_start/end`` from the subscription onto its
    items (Basil API, 2025+), so the top-level fields are usually null now.
    Read both shapes before falling back to the plan length.
    """
    items = (remote.get("items") or {}).get("data") or [{}]
    item = items[0] if items else {}
    start_epoch = remote.get("current_period_start") or item.get("current_period_start")
    end_epoch = remote.get("current_period_end") or item.get("current_period_end")
    start = _as_datetime(start_epoch)
    end = _as_datetime(end_epoch, start + timedelta(days={"weekly": 7, "monthly": 30, "yearly": 365}.get(plan_code or "", 30)))
    if end <= start:  # never hand the entitlement a zero-length window
        end = start + timedelta(days={"weekly": 7, "monthly": 30, "yearly": 365}.get(plan_code or "", 30))
    return start, end


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
        customer_id = remote.get("customer") or customer_id
        plan_code = (remote.get("metadata") or {}).get("plan_code") or plan_code
        period_start, period_end = _period_bounds(remote, plan_code)
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


PLAN_DAYS = {"weekly": 7, "monthly": 30, "yearly": 365}


def _activate_pass(session: Session, checkout: dict) -> dict:
    """One-time purchase: stack the plan's days onto whatever is left."""
    metadata = checkout.get("metadata") or {}
    user_id = int(metadata.get("user_id") or checkout.get("client_reference_id") or 0)
    plan_code = metadata.get("plan_code")
    if not user_id or not plan_code:
        raise BillingError(400, "checkout session is missing our metadata")
    if checkout.get("payment_status") not in (None, "paid"):
        return {"pending": True, "session_id": checkout.get("id")}

    order = session.scalar(select(Order).where(Order.external_txn_id == checkout.get("id")))
    if order is not None and order.status == "paid":
        # Stripe retries the same event; never add days twice.
        return {"replayed": True, "order_no": order.order_no}

    plan = session.scalar(select(Plan).where(Plan.code == plan_code))
    if plan is None or plan.status != "active":
        raise BillingError(404, "plan not found")
    version = _latest_version(session, plan)
    if order is None:
        order = Order(
            order_no=secrets.token_hex(8),
            user_id=user_id,
            plan_version_id=version.id,
            amount_cents=int(checkout.get("amount_total") or version.price_cents),
            currency=(checkout.get("currency") or version.currency or "usd").upper(),
            billing_mode="one_time",
            external_txn_id=checkout.get("id"),
        )
        session.add(order)
    order.status = "paid"
    order.paid_at = datetime.now(timezone.utc)
    order.billing_mode = "one_time"

    now = datetime.now(timezone.utc)
    days = PLAN_DAYS.get(plan_code, 30)
    row = session.scalar(
        select(Subscription).where(Subscription.user_id == user_id, Subscription.status == "active")
    )
    if row is None:
        row = Subscription(
            user_id=user_id,
            plan_id=plan.id,
            plan_version_id=version.id,
            status="active",
            current_period_start=now,
            current_period_end=now,
            billing_mode="one_time",
        )
        session.add(row)
    # Stack on whatever is already paid for — a live subscription counts too,
    # otherwise buying a pass mid-subscription would burn most of its days.
    base = max(
        [now]
        + [end for end in (_as_aware(row.pass_end), _as_aware(row.current_period_end)) if end]
    )
    row.pass_end = base + timedelta(days=days)
    row.billing_mode = "one_time"
    row.plan_id = plan.id
    row.plan_version_id = version.id
    session.commit()
    return {
        "order_no": order.order_no,
        "status": "paid",
        "user_id": user_id,
        "days": days,
        "pass_until": row.pass_end.isoformat(),
    }


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

    if plan_code is None and price_id:
        # Prices created by ensure_stripe_price are only known to the DB.
        owner = session.scalar(
            select(PlanVersion).where(PlanVersion.stripe_price_id == price_id)
        )
        if owner is not None:
            plan_code = session.scalar(select(Plan.code).where(Plan.id == owner.plan_id))

    status = remote.get("status")
    local_status = "active" if status in {"active", "trialing", "past_due"} else "canceled"
    period_start, period_end = _period_bounds(remote, plan_code)
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
    if row is not None:
        if local_status == "canceled":
            row.canceled_at = row.canceled_at or datetime.now(timezone.utc)
            pass_end = _as_aware(row.pass_end)
            if pass_end is not None and pass_end > datetime.now(timezone.utc):
                row.status = "active"  # the pass is still running
        elif remote.get("cancel_at_period_end"):
            # Scheduled to stop renewing: access lasts until current_period_end.
            row.canceled_at = row.canceled_at or datetime.now(timezone.utc)
        else:
            row.canceled_at = None
    session.commit()
    return {"user_id": user_id, "status": local_status}


def apply_stripe_event(session: Session, *, body: bytes, signature: str, payload: dict) -> dict:
    verify_stripe_signature(body, signature)
    event_type = payload.get("type") or ""
    obj = (payload.get("data") or {}).get("object") or {}
    if event_type in {"checkout.session.completed", "checkout.session.async_payment_succeeded"}:
        if (obj.get("mode") or "") == "payment":
            result = _activate_pass(session, obj)
        else:
            result = _activate_from_checkout(session, obj)
    elif event_type in {"customer.subscription.created", "customer.subscription.updated"}:
        result = _sync_from_subscription(session, obj)
    elif event_type == "customer.subscription.deleted":
        row = session.scalar(
            select(Subscription).where(Subscription.stripe_subscription_id == obj.get("id"))
        )
        if row is not None:
            row.canceled_at = datetime.now(timezone.utc)
            pass_end = _as_aware(row.pass_end)
            still_paid = pass_end is not None and pass_end > datetime.now(timezone.utc)
            row.status = "active" if still_paid else "canceled"
            session.commit()
        result = {"canceled": bool(row and row.status == "canceled")}
    else:
        result = {"ignored": event_type}
    return {"received": True, "type": event_type, **result}

