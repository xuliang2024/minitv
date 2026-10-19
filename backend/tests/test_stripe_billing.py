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
