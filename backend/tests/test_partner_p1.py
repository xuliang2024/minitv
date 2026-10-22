"""P1: supplier tenancy, /partner/v1 isolation, and membership attribution."""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.modules.billing import service as billing
from app.modules.billing.models import Order, Subscription
from app.modules.content.models import Drama

SECRET = "whsec_partner_p1"


@pytest.fixture()
def client(session):
    return TestClient(app)


@pytest.fixture(autouse=True)
def stripe_env(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_unit")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("STRIPE_PRICE_MONTHLY", "price_monthly")
    monkeypatch.setenv("H5_PUBLIC_BASE_URL", "https://h5.minitv.us")


def sign(body: bytes) -> str:
    stamp = int(time.time())
    digest = hmac.new(SECRET.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={stamp},v1={digest}"


def _admin_headers(make, client) -> dict:
    admin = make.admin(role="admin")
    r = client.post(
        "/admin/v1/auth/login", json={"username": admin.username, "password": "pass-1234"}
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _supplier(client, headers, name="星野传媒", share=None) -> dict:
    body = {"name": name, "contact_name": "张三", "contact_email": "ops@example.com"}
    if share is not None:
        body["revenue_share_bps"] = share
    r = client.post("/admin/v1/suppliers", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def _partner(client, headers, supplier_id, username="sp-xingye") -> dict:
    r = client.post(
        "/admin/v1/staff",
        json={
            "username": username,
            "display_name": "星野运营",
            "role": "partner",
            "password": "partner-pass-1234",
            "supplier_id": supplier_id,
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    login = client.post(
        "/admin/v1/auth/login", json={"username": username, "password": "partner-pass-1234"}
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['token']}"}


# ---- supplier CRUD / share ----


def test_supplier_defaults_to_80_percent_with_history(make, session, client):
    headers = _admin_headers(make, client)
    created = _supplier(client, headers)
    assert created["revenue_share_bps"] == 8000
    assert created["account"] is None

    detail = client.get(f"/admin/v1/suppliers/{created['id']}/share", headers=headers)
    assert detail.status_code == 200
    history = detail.json()["history"]
    assert len(history) == 1
    assert history[0]["share_bps"] == 8000
    assert history[0]["effective_to"] is None


def test_share_change_closes_previous_row(make, session, client):
    headers = _admin_headers(make, client)
    created = _supplier(client, headers)

    r = client.post(
        f"/admin/v1/suppliers/{created['id']}/share",
        json={"share_bps": 7000, "note": "续约下调"},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["revenue_share_bps"] == 7000

    history = client.get(f"/admin/v1/suppliers/{created['id']}/share", headers=headers).json()[
        "history"
    ]
    assert len(history) == 2
    assert history[0]["share_bps"] == 7000 and history[0]["effective_to"] is None
    assert history[1]["share_bps"] == 8000 and history[1]["effective_to"] is not None


def test_share_out_of_range_rejected(make, session, client):
    headers = _admin_headers(make, client)
    created = _supplier(client, headers)
    r = client.post(
        f"/admin/v1/suppliers/{created['id']}/share",
        json={"share_bps": 12000},
        headers=headers,
    )
    assert r.status_code == 422


def test_duplicate_supplier_name_conflicts(make, session, client):
    headers = _admin_headers(make, client)
    _supplier(client, headers)
    r = client.post("/admin/v1/suppliers", json={"name": "星野传媒"}, headers=headers)
    assert r.status_code == 409


# ---- one account per supplier ----


def test_partner_account_requires_supplier(make, session, client):
    headers = _admin_headers(make, client)
    r = client.post(
        "/admin/v1/staff",
        json={"username": "sp-x", "role": "partner", "password": "partner-pass-1234"},
        headers=headers,
    )
    assert r.status_code == 422
    assert "供应商" in r.json()["detail"]


def test_staff_cannot_be_bound_to_supplier(make, session, client):
    headers = _admin_headers(make, client)
    created = _supplier(client, headers)
    r = client.post(
        "/admin/v1/staff",
        json={
            "username": "ops-bound",
            "role": "ops",
            "password": "partner-pass-1234",
            "supplier_id": created["id"],
        },
        headers=headers,
    )
    assert r.status_code == 422


def test_one_account_per_supplier(make, session, client):
    headers = _admin_headers(make, client)
    created = _supplier(client, headers)
    _partner(client, headers, created["id"])
    r = client.post(
        "/admin/v1/staff",
        json={
            "username": "sp-second",
            "role": "partner",
            "password": "partner-pass-1234",
            "supplier_id": created["id"],
        },
        headers=headers,
    )
    assert r.status_code == 409


def test_partner_role_cannot_be_changed(make, session, client):
    headers = _admin_headers(make, client)
    created = _supplier(client, headers)
    _partner(client, headers, created["id"])
    partner_row = client.get("/admin/v1/staff?q=sp-xingye", headers=headers).json()["items"][0]
    r = client.patch(
        f"/admin/v1/staff/{partner_row['id']}", json={"role": "ops"}, headers=headers
    )
    assert r.status_code == 400


# ---- isolation ----


def test_partner_cannot_touch_admin_surface(make, session, client):
    headers = _admin_headers(make, client)
    created = _supplier(client, headers)
    partner = _partner(client, headers, created["id"])

    for path in ("/admin/v1/dramas", "/admin/v1/users", "/admin/v1/finance/orders"):
        r = client.get(path, headers=partner)
        assert r.status_code == 403, path
        assert r.json()["detail"]["code"] == "staff_only"


def test_staff_cannot_use_partner_surface(make, session, client):
    headers = _admin_headers(make, client)
    r = client.get("/partner/v1/me", headers=headers)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "partner_only"


def test_partner_session_returns_supplier(make, session, client):
    headers = _admin_headers(make, client)
    created = _supplier(client, headers)
    partner = _partner(client, headers, created["id"])

    r = client.get("/partner/v1/me", headers=partner)
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "partner"
    assert body["supplier"]["name"] == created["name"]
    assert body["supplier"]["revenue_share_bps"] == 8000


def test_partner_only_sees_own_dramas(make, session, client):
    headers = _admin_headers(make, client)
    mine = _supplier(client, headers, name="星野传媒")
    other = _supplier(client, headers, name="青禾影视")
    partner = _partner(client, headers, mine["id"])

    own = make.drama(title="我的剧", status="published")
    own.supplier_id = mine["id"]
    foreign = make.drama(title="别人的剧", status="published")
    foreign.supplier_id = other["id"]
    platform = make.drama(title="自营剧", status="published")
    session.commit()

    listed = client.get("/partner/v1/dramas", headers=partner)
    assert listed.status_code == 200
    ids = [row["id"] for row in listed.json()["items"]]
    assert ids == [own.id]

    assert client.get(f"/partner/v1/dramas/{foreign.id}", headers=partner).status_code == 404
    assert client.get(f"/partner/v1/dramas/{platform.id}", headers=partner).status_code == 404

    detail = client.get(f"/partner/v1/dramas/{own.id}", headers=partner)
    assert detail.status_code == 200
    assert detail.json()["review_state"] == "none"
    assert detail.json()["episodes"] == []


def test_disabling_supplier_kills_its_login(make, session, client):
    headers = _admin_headers(make, client)
    created = _supplier(client, headers)
    partner = _partner(client, headers, created["id"])

    r = client.post(
        f"/admin/v1/suppliers/{created['id']}/status",
        json={"status": "disabled", "reason": "合同到期"},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["account_status"] == "disabled"
    assert r.json()["revoked_sessions"] >= 1

    assert client.get("/partner/v1/me", headers=partner).status_code == 401
    login = client.post(
        "/admin/v1/auth/login", json={"username": "sp-xingye", "password": "partner-pass-1234"}
    )
    assert login.status_code == 401


# ---- attribution ----


def test_checkout_records_source_drama(make, session, monkeypatch):
    billing.ensure_default_plans(session)
    billing.set_billing_mode(session, "subscription", 0)
    user = make.user(email="buyer@example.com")
    drama = make.drama(title="归因剧", status="published")

    captured = {}

    def fake_request(path, data=None, method="POST"):
        captured.update(data or {})
        return {"id": "cs_p1", "url": "https://checkout.stripe.com/x"}

    monkeypatch.setattr(billing, "_stripe_request", fake_request)
    billing.create_checkout_session(
        session,
        user_id=user.id,
        email=user.email,
        plan_code="monthly",
        source_drama_id=drama.id,
    )

    assert captured["metadata[source_drama_id]"] == str(drama.id)
    order = session.scalar(select(Order).where(Order.external_txn_id == "cs_p1"))
    assert order.source_drama_id == drama.id


def test_unpublished_source_drama_rejected(make, session, monkeypatch):
    billing.ensure_default_plans(session)
    billing.set_billing_mode(session, "subscription", 0)
    user = make.user(email="buyer2@example.com")
    drama = make.drama(title="草稿剧", status="draft")

    monkeypatch.setattr(
        billing, "_stripe_request", lambda *a, **k: {"id": "x", "url": "https://x"}
    )
    with pytest.raises(billing.BillingError) as exc:
        billing.create_checkout_session(
            session,
            user_id=user.id,
            email=user.email,
            plan_code="monthly",
            source_drama_id=drama.id,
        )
    assert exc.value.status_code == 404


def test_webhook_inherits_source_drama(make, session, monkeypatch):
    billing.ensure_default_plans(session)
    billing.set_billing_mode(session, "subscription", 0)
    user = make.user(email="buyer3@example.com")
    drama = make.drama(title="归因剧二", status="published")

    monkeypatch.setattr(
        billing,
        "_stripe_request",
        lambda path, data=None, method="POST": {"id": "cs_p2", "url": "https://x"},
    )
    billing.create_checkout_session(
        session,
        user_id=user.id,
        email=user.email,
        plan_code="monthly",
        source_drama_id=drama.id,
    )

    payload = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_p2",
                "metadata": {
                    "user_id": str(user.id),
                    "plan_code": "monthly",
                    "source_drama_id": str(drama.id),
                },
                "amount_total": 1999,
                "currency": "usd",
                "payment_status": "paid",
            }
        },
    }
    body = json.dumps(payload).encode()
    billing.apply_stripe_event(session, body=body, signature=sign(body), payload=payload)

    subscription = session.scalar(select(Subscription).where(Subscription.user_id == user.id))
    assert subscription is not None
    assert subscription.source_drama_id == drama.id

    order = session.scalar(select(Order).where(Order.external_txn_id == "cs_p2"))
    assert order.status == "paid"
    assert order.source_drama_id == drama.id
