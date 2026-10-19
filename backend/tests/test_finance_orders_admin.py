"""Admin coverage for the finance order page: /admin/v1/finance/orders."""

from __future__ import annotations

import datetime

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.billing.models import Order, Plan, PlanVersion


@pytest.fixture()
def client(session):
    return TestClient(app)


def _login(client, username, password="pass-1234"):
    response = client.post("/admin/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _admin(make, client):
    staff = make.admin(role="admin")
    return _login(client, staff.username)


def _plan_version(session, code: str = "weekly", price_cents: int = 999) -> PlanVersion:
    plan = Plan(code=code, name_en=code.title(), name_zh="周卡")
    session.add(plan)
    session.flush()
    version = PlanVersion(
        plan_id=plan.id,
        version=3,
        price_cents=price_cents,
        currency="USD",
        benefits={"items": ["Unlock all", "No ads"]},
    )
    session.add(version)
    session.commit()
    return version


def _order(
    session,
    user_id: int,
    version: PlanVersion,
    order_no: str,
    *,
    status: str = "paid",
    amount_cents: int | None = None,
    txn: str | None = None,
) -> Order:
    now = datetime.datetime.now(datetime.timezone.utc)
    order = Order(
        order_no=order_no,
        user_id=user_id,
        plan_version_id=version.id,
        amount_cents=version.price_cents if amount_cents is None else amount_cents,
        currency="USD",
        billing_mode="subscription",
        status=status,
        external_txn_id=txn,
        paid_at=now if status == "paid" else None,
    )
    session.add(order)
    session.commit()
    return order


def test_list_returns_user_plan_and_summary(make, session, client):
    headers = _admin(make, client)
    account = make.user(email="zoe@example.com")
    version = _plan_version(session)
    _order(session, account.id, version, "ORD-PAID", amount_cents=999, txn="txn_paid")
    _order(session, account.id, version, "ORD-REFUND", status="refunded", amount_cents=1999)
    _order(session, account.id, version, "ORD-PENDING", status="created", amount_cents=7999)

    data = client.get("/admin/v1/finance/orders", headers=headers).json()

    assert data["total"] == 3
    assert data["page"] == 1
    item = next(row for row in data["items"] if row["order_no"] == "ORD-PAID")
    assert item["user"]["email"] == "zoe@example.com"
    assert item["user"]["public_id"] == account.public_id
    assert item["plan"]["code"] == "weekly"
    assert item["plan"]["name_zh"] == "周卡"
    assert item["plan"]["version"] == 3
    assert item["plan"]["benefits"]["items"] == ["Unlock all", "No ads"]
    assert item["external_txn_id"] == "txn_paid"
    assert item["paid_at"] is not None

    summary = data["summary"]
    assert summary["total_orders"] == 3
    assert summary["paid_orders"] == 1
    assert summary["refunded_orders"] == 1
    assert summary["created_orders"] == 1
    assert summary["net_cents"] == 999
    assert summary["gross_cents"] == 999 + 1999
    assert summary["pending_cents"] == 7999


def test_search_status_filter_and_pagination(make, session, client):
    headers = _admin(make, client)
    zoe = make.user(email="zoe@example.com")
    other = make.user(email="other@example.com")
    version = _plan_version(session)
    _order(session, zoe.id, version, "ORD-1", txn="txn_1")
    _order(session, zoe.id, version, "ORD-2", status="created")
    _order(session, other.id, version, "ORD-3")

    found = client.get("/admin/v1/finance/orders", params={"q": "zoe"}, headers=headers).json()
    assert found["total"] == 2
    assert {row["order_no"] for row in found["items"]} == {"ORD-1", "ORD-2"}

    by_txn = client.get("/admin/v1/finance/orders", params={"q": "txn_1"}, headers=headers).json()
    assert by_txn["total"] == 1
    assert by_txn["items"][0]["order_no"] == "ORD-1"

    paid = client.get(
        "/admin/v1/finance/orders", params={"status": "paid"}, headers=headers
    ).json()
    assert paid["total"] == 2
    assert all(row["status"] == "paid" for row in paid["items"])

    paged = client.get(
        "/admin/v1/finance/orders", params={"page": 2, "page_size": 2}, headers=headers
    ).json()
    assert paged["total"] == 3
    assert len(paged["items"]) == 1
    assert paged["page"] == 2
    assert paged["page_size"] == 2


def test_detail_adds_user_stats_and_membership(make, session, client):
    headers = _admin(make, client)
    account = make.user(email="zoe@example.com")
    make.subscription(account)
    version = _plan_version(session)
    order = _order(session, account.id, version, "ORD-DETAIL")
    _order(session, account.id, version, "ORD-DETAIL-2", amount_cents=1999)

    data = client.get(f"/admin/v1/finance/orders/{order.order_no}", headers=headers).json()

    assert data["order_no"] == "ORD-DETAIL"
    assert data["user"]["email"] == "zoe@example.com"
    assert data["user_stats"]["paid_orders"] == 2
    assert data["user_stats"]["paid_cents"] == 999 + 1999
    assert data["membership"]["active"] is True
    assert data["membership"]["plan_code"].startswith("plan-")

    missing = client.get("/admin/v1/finance/orders/NO-SUCH-ORDER", headers=headers)
    assert missing.status_code == 404


def test_dashboard_recent_orders_still_serializes(make, session, client):
    """The dashboard calls the finance endpoint directly; params must not leak Query objects."""
    headers = _admin(make, client)
    account = make.user(email="zoe@example.com")
    version = _plan_version(session)
    _order(session, account.id, version, "ORD-DASH")

    data = client.get("/admin/v1/dashboard", headers=headers).json()

    assert data["recent_orders"][0]["order_no"] == "ORD-DASH"
    assert data["recent_orders"][0]["user_id"] == account.id
