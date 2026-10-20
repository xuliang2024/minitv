"""Admin membership ops: support can hand out or take back time without Stripe.

Entitlement moves through exactly two windows (`current_period_end` and
`pass_end`), so every assertion here reads `/v1/me.membership` or the users
list instead of poking at the row directly.
"""

from __future__ import annotations

import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.modules.account import service as accounts
from app.modules.billing import service as billing
from app.modules.billing.models import Order, Plan, PlanVersion, Subscription
from app.modules.common.models import AuditLog

UTC = datetime.timezone.utc


@pytest.fixture()
def client(session):
    return TestClient(app)


def _staff(client, make, role="admin"):
    staff = make.admin(role=role)
    response = client.post(
        "/admin/v1/auth/login", json={"username": staff.username, "password": "pass-1234"}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _user_headers(client, monkeypatch, email):
    monkeypatch.setattr(accounts, "generate_code", lambda: "123456")
    monkeypatch.setattr(accounts, "send_verification_code", lambda **_: "test-message")
    assert client.post("/v1/auth/email/code", json={"email": email, "purpose": "login"}).status_code == 200
    response = client.post(
        "/v1/auth/email/authenticate", json={"email": email, "code": "123456"}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _plans(session):
    billing.ensure_default_plans(session)


def _subscribe(session, user, *, plan_code="monthly", period_end=None, stripe_id="sub_test_1"):
    plan = session.scalar(select(Plan).where(Plan.code == plan_code))
    version = session.scalar(
        select(PlanVersion).where(PlanVersion.plan_id == plan.id).order_by(PlanVersion.version.desc())
    )
    now = datetime.datetime.now(UTC)
    row = Subscription(
        user_id=user.id,
        plan_id=plan.id,
        plan_version_id=version.id,
        status="active",
        current_period_start=now,
        current_period_end=period_end or now + datetime.timedelta(days=30),
        stripe_subscription_id=stripe_id,
        billing_mode="subscription",
    )
    session.add(row)
    session.commit()
    return row


def _until(payload: str | None) -> datetime.datetime:
    assert payload is not None
    return datetime.datetime.fromisoformat(payload)


def _close(actual: datetime.datetime, expected: datetime.datetime, seconds: int = 10) -> bool:
    return abs((actual - expected).total_seconds()) < seconds


def test_grant_extends_from_the_live_window(session, make, client):
    _plans(session)
    user = make.user(email="grant@example.com")
    headers = _staff(client, make)
    now = datetime.datetime.now(UTC)

    first = client.post(
        f"/admin/v1/users/{user.id}/membership",
        json={"plan_code": "monthly", "days": 30, "mode": "extend", "reason": "客诉补偿"},
        headers=headers,
    )
    assert first.status_code == 200, first.text
    assert _close(_until(first.json()["after_until"]), now + datetime.timedelta(days=30))
    assert first.json()["before_until"] is None

    # A second grant stacks on the remaining time instead of resetting it.
    second = client.post(
        f"/admin/v1/users/{user.id}/membership",
        json={"plan_code": "weekly", "days": 7, "mode": "extend", "reason": "追加补偿"},
        headers=headers,
    )
    assert second.status_code == 200, second.text
    assert _close(
        _until(second.json()["after_until"]), now + datetime.timedelta(days=37)
    )

    # A gift is not a sale: the finance ledger must stay clean.
    assert session.scalar(select(Order).where(Order.user_id == user.id)) is None

    rows = client.get("/admin/v1/users", headers=headers).json()["items"]
    row = next(item for item in rows if item["id"] == user.id)
    assert row["member"] is True
    assert _close(_until(row["member_until"]), now + datetime.timedelta(days=37))

    audit = session.scalar(
        select(AuditLog).where(AuditLog.action == "membership_grant").order_by(AuditLog.id)
    )
    assert audit is not None and audit.payload["days"] == 30
    assert audit.payload["reason"] == "客诉补偿"


def test_grant_can_restart_from_today(session, make, client):
    _plans(session)
    user = make.user(email="restart@example.com")
    headers = _staff(client, make)
    now = datetime.datetime.now(UTC)

    client.post(
        f"/admin/v1/users/{user.id}/membership",
        json={"plan_code": "yearly", "days": 365, "mode": "extend", "reason": "补偿"},
        headers=headers,
    )
    restarted = client.post(
        f"/admin/v1/users/{user.id}/membership",
        json={"plan_code": "weekly", "days": 7, "mode": "restart", "reason": "测试号重算"},
        headers=headers,
    )
    assert restarted.status_code == 200, restarted.text
    assert _close(_until(restarted.json()["after_until"]), now + datetime.timedelta(days=7))


def test_grant_on_a_stripe_subscriber_keeps_the_plan(monkeypatch, session, make, client):
    _plans(session)
    user = make.user(email="sub-grant@example.com")
    row = _subscribe(session, user, plan_code="weekly")
    original_plan_id = row.plan_id
    headers = _staff(client, make)

    def boom(*args, **kwargs):
        raise AssertionError("a gift must never call Stripe")

    monkeypatch.setattr(billing, "_stripe_request", boom)
    response = client.post(
        f"/admin/v1/users/{user.id}/membership",
        json={"plan_code": "monthly", "days": 30, "mode": "extend", "reason": "VIP 补偿"},
        headers=headers,
    )
    assert response.status_code == 200, response.text

    session.expire_all()
    refreshed = session.get(Subscription, row.id)
    # The webhook re-derives the plan from the Stripe price; churning it here
    # would make the plan name flip on the next subscription.updated event.
    assert refreshed.plan_id == original_plan_id
    assert refreshed.pass_end is not None
    assert refreshed.current_period_end == row.current_period_end


def test_restart_is_rejected_for_a_stripe_subscriber(session, make, client):
    _plans(session)
    user = make.user(email="sub-restart@example.com")
    _subscribe(session, user)
    headers = _staff(client, make)

    response = client.post(
        f"/admin/v1/users/{user.id}/membership",
        json={"plan_code": "weekly", "days": 7, "mode": "restart", "reason": "重算"},
        headers=headers,
    )
    assert response.status_code == 409


def test_period_end_stops_renewal_but_keeps_access(monkeypatch, session, make, client):
    _plans(session)
    user = make.user(email="keep@example.com")
    row = _subscribe(session, user)
    headers = _staff(client, make)
    calls = []

    def fake_request(path, data=None, method="POST"):
        calls.append((path, data, method))
        return {"id": "sub_test_1", "cancel_at_period_end": True}

    monkeypatch.setattr(billing, "_stripe_request", fake_request)
    response = client.request(
        "DELETE",
        f"/admin/v1/users/{user.id}/membership",
        json={"mode": "period_end", "reason": "用户要求停止续费"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["stripe_canceled"] is True
    assert calls == [("/subscriptions/sub_test_1", {"cancel_at_period_end": "true"}, "POST")]

    session.expire_all()
    assert session.get(Subscription, row.id).status == "active"
    me = client.get(
        "/v1/me", headers=_user_headers(client, monkeypatch, "keep@example.com")
    ).json()
    assert me["membership"]["active"] is True
    assert me["membership"]["canceling"] is True


def test_revoke_now_cancels_stripe_and_drops_access(monkeypatch, session, make, client):
    _plans(session)
    user = make.user(email="revoke@example.com")
    row = _subscribe(session, user)
    headers = _staff(client, make)
    calls = []

    def fake_request(path, data=None, method="POST"):
        calls.append((path, data, method))
        return {"id": "sub_test_1", "status": "canceled"}

    monkeypatch.setattr(billing, "_stripe_request", fake_request)
    response = client.request(
        "DELETE",
        f"/admin/v1/users/{user.id}/membership",
        json={"mode": "now", "reason": "误开通"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert calls == [("/subscriptions/sub_test_1", None, "DELETE")]

    session.expire_all()
    assert session.get(Subscription, row.id).status == "canceled"
    me = client.get(
        "/v1/me", headers=_user_headers(client, monkeypatch, "revoke@example.com")
    ).json()
    assert me["membership"]["active"] is False


def test_stripe_failure_leaves_the_local_row_alone(monkeypatch, session, make, client):
    _plans(session)
    user = make.user(email="stripe-down@example.com")
    row = _subscribe(session, user)
    headers = _staff(client, make)

    def down(path, data=None, method="POST"):
        raise billing.BillingError(502, "stripe is unreachable")

    monkeypatch.setattr(billing, "_stripe_request", down)
    response = client.request(
        "DELETE",
        f"/admin/v1/users/{user.id}/membership",
        json={"mode": "now", "reason": "测试"},
        headers=headers,
    )
    assert response.status_code == 502

    session.expire_all()
    refreshed = session.get(Subscription, row.id)
    assert refreshed.status == "active"
    assert refreshed.pass_end is None


def test_revoke_now_works_for_a_granted_pass(session, make, client):
    _plans(session)
    user = make.user(email="pass-revoke@example.com")
    headers = _staff(client, make)
    client.post(
        f"/admin/v1/users/{user.id}/membership",
        json={"plan_code": "monthly", "days": 30, "mode": "extend", "reason": "补偿"},
        headers=headers,
    )

    revoked = client.request(
        "DELETE",
        f"/admin/v1/users/{user.id}/membership",
        json={"mode": "now", "reason": "补偿发错人"},
        headers=headers,
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["stripe_canceled"] is False

    rows = client.get("/admin/v1/users", headers=headers).json()["items"]
    row = next(item for item in rows if item["id"] == user.id)
    assert row["member"] is False


def test_period_end_is_rejected_without_a_stripe_subscription(session, make, client):
    _plans(session)
    user = make.user(email="pass-period@example.com")
    headers = _staff(client, make)
    client.post(
        f"/admin/v1/users/{user.id}/membership",
        json={"plan_code": "monthly", "days": 30, "mode": "extend", "reason": "补偿"},
        headers=headers,
    )

    response = client.request(
        "DELETE",
        f"/admin/v1/users/{user.id}/membership",
        json={"mode": "period_end", "reason": "停续"},
        headers=headers,
    )
    assert response.status_code == 409


def test_revoke_without_membership_is_a_conflict(session, make, client):
    user = make.user(email="nobody@example.com")
    headers = _staff(client, make)
    response = client.request(
        "DELETE",
        f"/admin/v1/users/{user.id}/membership",
        json={"mode": "now", "reason": "清理"},
        headers=headers,
    )
    assert response.status_code == 409


def test_grant_validates_input(session, make, client):
    _plans(session)
    user = make.user(email="bad-input@example.com")
    headers = _staff(client, make)

    blank_reason = client.post(
        f"/admin/v1/users/{user.id}/membership",
        json={"plan_code": "monthly", "days": 30, "reason": "  "},
        headers=headers,
    )
    assert blank_reason.status_code == 422

    zero_days = client.post(
        f"/admin/v1/users/{user.id}/membership",
        json={"plan_code": "monthly", "days": 0, "reason": "x"},
        headers=headers,
    )
    assert zero_days.status_code == 422

    ghost_plan = client.post(
        f"/admin/v1/users/{user.id}/membership",
        json={"plan_code": "decade", "days": 30, "reason": "x"},
        headers=headers,
    )
    assert ghost_plan.status_code == 404

    missing_user = client.post(
        "/admin/v1/users/999999/membership",
        json={"plan_code": "monthly", "days": 30, "reason": "x"},
        headers=headers,
    )
    assert missing_user.status_code == 404


def test_ops_can_read_but_not_write(session, make, client):
    _plans(session)
    user = make.user(email="ops@example.com")
    headers = _staff(client, make, role="ops")

    assert client.get(f"/admin/v1/users/{user.id}/membership", headers=headers).status_code == 200
    for response in (
        client.post(
            f"/admin/v1/users/{user.id}/membership",
            json={"plan_code": "monthly", "days": 30, "reason": "x"},
            headers=headers,
        ),
        client.request(
            "DELETE",
            f"/admin/v1/users/{user.id}/membership",
            json={"mode": "now", "reason": "x"},
            headers=headers,
        ),
    ):
        assert response.status_code == 403


def test_membership_detail_reports_the_live_window(session, make, client):
    _plans(session)
    user = make.user(email="detail@example.com")
    now = datetime.datetime.now(UTC)
    # Paid subscription already lapsed, pass still running: the later window wins.
    row = _subscribe(session, user, period_end=now - datetime.timedelta(days=2))
    row.pass_end = now + datetime.timedelta(days=12)
    session.commit()
    headers = _staff(client, make)

    payload = client.get(f"/admin/v1/users/{user.id}/membership", headers=headers).json()
    assert payload["active"] is True
    assert _close(_until(payload["until"]), now + datetime.timedelta(days=12))
    assert payload["source"] == "subscription"
    assert payload["history"][0]["id"] == row.id
    assert payload["user"]["id"] == user.id


def test_users_list_uses_the_pass_expiry(session, make, client):
    """Regression: the column used to print `current_period_end` for a pass."""
    _plans(session)
    user = make.user(email="list@example.com")
    now = datetime.datetime.now(UTC)
    row = _subscribe(session, user, plan_code="weekly", period_end=now - datetime.timedelta(days=5))
    row.stripe_subscription_id = None
    row.billing_mode = "one_time"
    row.pass_end = now + datetime.timedelta(days=9)
    session.commit()

    rows = client.get("/admin/v1/users", headers=_staff(client, make)).json()["items"]
    row_payload = next(item for item in rows if item["id"] == user.id)
    assert row_payload["member"] is True
    assert _close(_until(row_payload["member_until"]), now + datetime.timedelta(days=9))
    assert row_payload["billing_mode"] == "one_time"
    assert row_payload["canceling"] is False
