"""Stripe billing: checkout hands off to Stripe, entitlement only moves on a
signed webhook. No test ever calls the real API — ``_stripe_request`` is the
single seam and it is always patched here.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.modules.billing import service as billing
from app.modules.billing.models import Order, Subscription

SECRET = "whsec_test_secret"


@pytest.fixture(autouse=True)
def stripe_env(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_unit")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("STRIPE_PRICE_WEEKLY", "price_weekly")
    monkeypatch.setenv("STRIPE_PRICE_MONTHLY", "price_monthly")
    monkeypatch.setenv("STRIPE_PRICE_YEARLY", "price_yearly")
    monkeypatch.setenv("H5_PUBLIC_BASE_URL", "https://h5.minitv.us")


def sign(body: bytes, timestamp: int | None = None) -> str:
    stamp = timestamp or int(time.time())
    digest = hmac.new(SECRET.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={stamp},v1={digest}"


def test_checkout_creates_a_pending_order(session, make, monkeypatch):
    billing.ensure_default_plans(session)
    billing.set_billing_mode(session, "subscription", 0)
    user = make.user(email="buyer@example.com")

    def fake_request(path, data=None, method="POST"):
        assert path == "/checkout/sessions"
        assert data["mode"] == "subscription"
        assert data["line_items[0][price]"] == "price_weekly"
        return {"id": "cs_test_1", "url": "https://checkout.stripe.com/c/pay/cs_test_1"}

    monkeypatch.setattr(billing, "_stripe_request", fake_request)
    result = billing.create_checkout_session(session, user_id=user.id, email=user.email, plan_code="weekly")

    assert result["url"].startswith("https://checkout.stripe.com/")
    order = session.scalar(select(Order).where(Order.external_txn_id == "cs_test_1"))
    assert order is not None and order.status == "created" and order.amount_cents == 999


def test_webhook_rejects_a_forged_signature(session):
    body = json.dumps({"type": "checkout.session.completed", "data": {"object": {}}}).encode()
    with pytest.raises(billing.BillingError) as exc:
        billing.apply_stripe_event(session, body=body, signature="t=1,v1=deadbeef", payload=json.loads(body))
    assert exc.value.status_code in {400, 401}


def test_checkout_completed_activates_membership(session, make, monkeypatch):
    billing.ensure_default_plans(session)
    billing.set_billing_mode(session, "subscription", 0)
    user = make.user(email="buyer@example.com")
    now = int(time.time())

    def fake_request(path, data=None, method="POST"):
        if path.startswith("/subscriptions/"):
            # Stripe (Basil API) puts the period on the subscription item, not on
            # the subscription itself — the shape that used to give us a zero-length
            # window and an instantly-expired entitlement.
            return {
                "id": "sub_test_1",
                "customer": "cus_test_1",
                "items": {
                    "data": [
                        {
                            "price": {"id": "price_weekly"},
                            "current_period_start": now,
                            "current_period_end": now + 7 * 86400,
                        }
                    ]
                },
                "metadata": {"user_id": str(user.id), "plan_code": "weekly"},
            }
        return {"id": "cs_test_1", "url": "https://checkout.stripe.com/x", "amount_total": 999, "currency": "usd"}

    monkeypatch.setattr(billing, "_stripe_request", fake_request)
    billing.create_checkout_session(session, user_id=user.id, email=user.email, plan_code="weekly")

    payload = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_test_1",
                "subscription": "sub_test_1",
                "customer": "cus_test_1",
                "metadata": {"user_id": str(user.id), "plan_code": "weekly"},
                "client_reference_id": str(user.id),
                "amount_total": 999,
                "currency": "usd",
            }
        },
    }
    body = json.dumps(payload).encode()
    billing.apply_stripe_event(session, body=body, signature=sign(body), payload=payload)

    subscription = session.scalar(select(Subscription).where(Subscription.user_id == user.id))
    assert subscription is not None
    assert subscription.status == "active"
    assert subscription.stripe_subscription_id == "sub_test_1"
    assert subscription.current_period_end > subscription.current_period_start
    assert billing.active_membership(session, user.id)["active"] is True

    order = session.scalar(select(Order).where(Order.external_txn_id == "cs_test_1"))
    assert order.status == "paid" and order.paid_at is not None


def test_subscription_deleted_cancels_access(session, make, monkeypatch):
    billing.ensure_default_plans(session)
    user = make.user(email="buyer@example.com")
    now = int(time.time())
    subscription = Subscription(
        user_id=user.id,
        plan_id=session.scalar(select(billing.Plan).where(billing.Plan.code == "monthly")).id,
        plan_version_id=session.scalar(select(billing.PlanVersion).limit(1)).id,
        status="active",
        current_period_start=billing._as_datetime(now),
        current_period_end=billing._as_datetime(now + 30 * 86400),
        stripe_subscription_id="sub_test_2",
        stripe_customer_id="cus_test_2",
    )
    session.add(subscription)
    session.commit()

    payload = {"type": "customer.subscription.deleted", "data": {"object": {"id": "sub_test_2"}}}
    body = json.dumps(payload).encode()
    billing.apply_stripe_event(session, body=body, signature=sign(body), payload=payload)

    session.refresh(subscription)
    assert subscription.status == "canceled"
    assert billing.active_membership(session, user.id)["active"] is False


def test_cancel_at_period_end_keeps_access_but_flags_canceling(session, make, monkeypatch):
    billing.ensure_default_plans(session)
    user = make.user(email="buyer@example.com")
    now = int(time.time())
    item = {"price": {"id": "price_weekly"}, "current_period_start": now, "current_period_end": now + 7 * 86400}
    remote = {
        "id": "sub_test_3",
        "customer": "cus_test_3",
        "status": "active",
        "cancel_at_period_end": True,
        "items": {"data": [item]},
        "metadata": {"user_id": str(user.id), "plan_code": "weekly"},
    }
    payload = {"type": "customer.subscription.updated", "data": {"object": remote}}
    body = json.dumps(payload).encode()
    billing.apply_stripe_event(session, body=body, signature=sign(body), payload=payload)

    membership = billing.active_membership(session, user.id)
    assert membership["active"] is True
    assert membership["canceling"] is True


def test_price_edit_creates_the_stripe_price_immediately(session, make, monkeypatch):
    """Admin edits the price -> a matching Stripe Price exists right away.

    No dashboard visit: the version row remembers the id, and checkouts reuse it.
    """
    billing.ensure_default_plans(session)
    billing.set_billing_mode(session, "subscription", 0)
    user = make.user(email="buyer@example.com")
    monkeypatch.setenv("STRIPE_PRODUCT_WEEKLY", "prod_weekly")

    calls: list[tuple[str, dict]] = []

    def fake_request(path, data=None, method="POST"):
        calls.append((path, data or {}))
        if path == "/prices":
            assert data["unit_amount"] == 1234
            assert data["product"] == "prod_weekly"
            assert data["recurring[interval]"] == "week"
            return {"id": "price_generated_1"}
        session_id = f"cs_test_9{len([1 for path, _ in calls if path == '/checkout/sessions'])}"
        return {"id": session_id, "url": f"https://checkout.stripe.com/c/pay/{session_id}"}

    monkeypatch.setattr(billing, "_stripe_request", fake_request)

    version = billing.change_price(
        session, code="weekly", price_cents=1234, benefits={"items": ["x"], "best": False}, actor_id=1
    )
    session.refresh(version)
    assert version.stripe_price_id == "price_generated_1"
    assert [path for path, _ in calls] == ["/prices"]

    billing.create_checkout_session(session, user_id=user.id, email=user.email, plan_code="weekly")
    assert [path for path, _ in calls] == ["/prices", "/checkout/sessions"]
    assert calls[1][1]["line_items[0][price]"] == "price_generated_1"

    # A second purchase reuses the stored price instead of creating another one.
    billing.create_checkout_session(session, user_id=user.id, email=user.email, plan_code="weekly")
    assert [path for path, _ in calls] == ["/prices", "/checkout/sessions", "/checkout/sessions"]


def test_one_time_mode_buys_without_recurring(session, make, monkeypatch):
    """Global switch: one-time prices carry no interval and checkout uses payment."""
    billing.ensure_default_plans(session)
    billing.set_billing_mode(session, "one_time", 1)
    user = make.user(email="pass@example.com")
    monkeypatch.setenv("STRIPE_PRODUCT_WEEKLY", "prod_weekly")

    calls: list[tuple[str, dict]] = []

    def fake_request(path, data=None, method="POST"):
        calls.append((path, data or {}))
        if path == "/prices":
            assert "recurring[interval]" not in data
            return {"id": "price_onetime_1"}
        return {"id": "cs_pay_1", "url": "https://checkout.stripe.com/c/pay/cs_pay_1"}

    monkeypatch.setattr(billing, "_stripe_request", fake_request)

    billing.create_checkout_session(session, user_id=user.id, email=user.email, plan_code="weekly")

    assert [path for path, _ in calls] == ["/prices", "/checkout/sessions"]
    assert calls[1][1]["mode"] == "payment"
    assert calls[1][1]["line_items[0][price]"] == "price_onetime_1"
    order = session.scalar(select(Order).where(Order.external_txn_id == "cs_pay_1"))
    assert order.billing_mode == "one_time"
    assert billing.billing_mode(session) == "one_time"


def _pass_event(user_id: int, session_id: str) -> tuple[bytes, dict]:
    payload = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": session_id,
                "mode": "payment",
                "payment_status": "paid",
                "amount_total": 10,
                "currency": "usd",
                "metadata": {"user_id": str(user_id), "plan_code": "weekly"},
            }
        },
    }
    return json.dumps(payload).encode(), payload


def test_one_time_purchases_stack_and_ignore_replays(session, make):
    billing.ensure_default_plans(session)
    user = make.user(email="pass@example.com")

    body, payload = _pass_event(user.id, "cs_pay_1")
    billing.apply_stripe_event(session, body=body, signature=sign(body), payload=payload)
    row = session.scalar(select(Subscription).where(Subscription.user_id == user.id))
    first_end = row.pass_end
    assert first_end > billing._as_datetime(int(time.time()))

    # Stripe retries the same event: no extra days.
    billing.apply_stripe_event(session, body=body, signature=sign(body), payload=payload)
    session.refresh(row)
    assert row.pass_end == first_end

    # A second purchase buys another 7 days on top of the remaining window.
    body2, payload2 = _pass_event(user.id, "cs_pay_2")
    billing.apply_stripe_event(session, body=body2, signature=sign(body2), payload=payload2)
    session.refresh(row)
    assert row.pass_end - first_end == timedelta(days=7)
    assert billing.active_membership(session, user.id)["active"] is True


def test_pass_and_subscription_use_the_later_end(session, make):
    billing.ensure_default_plans(session)
    user = make.user(email="both@example.com")
    now = datetime.now(timezone.utc)
    plan = session.scalar(select(billing.Plan).where(billing.Plan.code == "monthly"))
    version = session.scalar(select(billing.PlanVersion).where(billing.PlanVersion.plan_id == plan.id))
    session.add(
        Subscription(
            user_id=user.id,
            plan_id=plan.id,
            plan_version_id=version.id,
            status="active",
            current_period_start=now,
            current_period_end=now + timedelta(days=3),
            pass_end=now + timedelta(days=10),
        )
    )
    session.commit()

    membership = billing.active_membership(session, user.id)
    assert membership["active"] is True
    assert membership["billing_mode"] == "one_time"
    assert membership["subscription_until"] is not None
    assert membership["pass_until"] is not None
    assert billing.access_until(session, user.id) > now + timedelta(days=9)


def test_one_time_pass_cannot_be_canceled(session, make):
    billing.ensure_default_plans(session)
    user = make.user(email="pass@example.com")
    body, payload = _pass_event(user.id, "cs_pay_1")
    billing.apply_stripe_event(session, body=body, signature=sign(body), payload=payload)

    with pytest.raises(billing.BillingError) as exc:
        billing.cancel_subscription(session, user.id)
    assert exc.value.status_code == 400


def test_default_mode_is_one_time(session):
    billing.ensure_default_plans(session)
    assert billing.billing_mode(session) == "one_time"


def test_one_time_price_below_stripe_minimum_is_rejected(session, make):
    billing.ensure_default_plans(session)
    billing.set_billing_mode(session, "one_time", 1)
    user = make.user(email="cheap@example.com")

    with pytest.raises(billing.BillingError) as exc:
        billing.change_price(
            session, code="weekly", price_cents=20, benefits={"items": [], "best": False}, actor_id=1
        )
    assert exc.value.status_code == 400

    # Guard also covers a version saved before the switch (v2 is $0.10).
    version = session.scalar(
        select(billing.PlanVersion).where(billing.PlanVersion.plan_id == 1, billing.PlanVersion.version == 2)
    )
    if version is None:
        version = billing.PlanVersion(plan_id=1, version=2, price_cents=10, currency="USD", benefits={})
        session.add(version)
        session.commit()
    version.price_cents = 10
    session.commit()

    with pytest.raises(billing.BillingError) as exc:
        billing.create_checkout_session(session, user_id=user.id, email=user.email, plan_code="weekly")
    assert exc.value.status_code == 400
    assert "0.50" in exc.value.detail


def test_pass_stacks_on_the_subscription_end(session, make):
    """Buying a pass mid-subscription must not burn the remaining days."""
    billing.ensure_default_plans(session)
    user = make.user(email="both@example.com")
    now = datetime.now(timezone.utc)
    plan = session.scalar(select(billing.Plan).where(billing.Plan.code == "weekly"))
    version = session.scalar(select(billing.PlanVersion).where(billing.PlanVersion.plan_id == plan.id))
    row = Subscription(
        user_id=user.id,
        plan_id=plan.id,
        plan_version_id=version.id,
        status="active",
        current_period_start=now,
        current_period_end=now + timedelta(days=3),
    )
    session.add(row)
    session.commit()

    body, payload = _pass_event(user.id, "cs_pay_mix")
    billing.apply_stripe_event(session, body=body, signature=sign(body), payload=payload)
    session.refresh(row)
    assert row.pass_end - row.current_period_end == timedelta(days=7)


def test_subscription_deletion_keeps_a_running_pass(session, make):
    billing.ensure_default_plans(session)
    user = make.user(email="pass@example.com")
    now = datetime.now(timezone.utc)
    plan = session.scalar(select(billing.Plan).where(billing.Plan.code == "weekly"))
    version = session.scalar(select(billing.PlanVersion).where(billing.PlanVersion.plan_id == plan.id))
    row = Subscription(
        user_id=user.id,
        plan_id=plan.id,
        plan_version_id=version.id,
        status="active",
        current_period_start=now - timedelta(days=7),
        current_period_end=now - timedelta(minutes=1),
        pass_end=now + timedelta(days=5),
        stripe_subscription_id="sub_mix_1",
    )
    session.add(row)
    session.commit()

    payload = {"type": "customer.subscription.deleted", "data": {"object": {"id": "sub_mix_1"}}}
    body = json.dumps(payload).encode()
    billing.apply_stripe_event(session, body=body, signature=sign(body), payload=payload)

    session.refresh(row)
    assert row.status == "active"  # the pass is still running
    assert billing.active_membership(session, user.id)["active"] is True


def test_one_time_checkout_reuses_or_creates_a_stripe_customer(session, make, monkeypatch):
    billing.ensure_default_plans(session)
    billing.set_billing_mode(session, "one_time", 1)
    user = make.user(email="pass@example.com")
    monkeypatch.setenv("STRIPE_PRODUCT_WEEKLY", "prod_weekly")
    plan = session.scalar(select(billing.Plan).where(billing.Plan.code == "weekly"))
    version = session.scalar(select(billing.PlanVersion).where(billing.PlanVersion.plan_id == plan.id))
    version.stripe_one_time_price_id = "price_ot"
    session.add(
        Subscription(
            user_id=user.id,
            plan_id=plan.id,
            plan_version_id=version.id,
            status="canceled",
            current_period_start=datetime.now(timezone.utc) - timedelta(days=30),
            current_period_end=datetime.now(timezone.utc) - timedelta(days=1),
            stripe_customer_id="cus_existing",
        )
    )
    session.commit()

    seen: list[dict] = []

    def fake_request(path, data=None, method="POST"):
        seen.append(data or {})
        return {"id": f"cs_{len(seen)}", "url": "https://checkout.stripe.com/x"}

    monkeypatch.setattr(billing, "_stripe_request", fake_request)
    billing.create_checkout_session(session, user_id=user.id, email=user.email, plan_code="weekly")
    assert seen[0]["customer"] == "cus_existing"
    assert "customer_email" not in seen[0]
    assert "customer_creation" not in seen[0]

    # A brand new buyer has no customer yet: let Checkout create one.
    fresh = make.user(email="fresh@example.com")
    billing.create_checkout_session(session, user_id=fresh.id, email=fresh.email, plan_code="weekly")
    assert seen[1]["customer_creation"] == "always"
    assert seen[1]["customer_email"] == "fresh@example.com"
