"""P4: partner stats, first-order attribution, and monthly settlements."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.modules.billing import service as billing
from app.modules.billing.models import Order, Plan, PlanVersion
from app.modules.content.models import Drama, Episode
from app.modules.playback.models import PlaybackEvent
from app.modules.wallet.models import CoinLedgerEntry


@pytest.fixture()
def client(session):
    return TestClient(app)


def _login(client, username, password) -> dict:
    r = client.post("/admin/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _admin(make, client) -> dict:
    user = make.admin(role="admin")
    return _login(client, user.username, "pass-1234")


def _finance(make, client) -> dict:
    user = make.admin(role="finance")
    return _login(client, user.username, "pass-1234")


def _setup(make, client, session, title="数据剧", share=8000) -> tuple[dict, dict, Drama]:
    admin = _admin(make, client)
    sup = client.post(
        "/admin/v1/suppliers",
        json={"name": "数据影视", "contact_email": "data@example.com", "revenue_share_bps": share},
        headers=admin,
    ).json()
    client.post(
        "/admin/v1/staff",
        json={
            "username": "sp-data",
            "role": "partner",
            "password": "partner-pass-1234",
            "supplier_id": sup["id"],
        },
        headers=admin,
    )
    partner = _login(client, "sp-data", "partner-pass-1234")
    drama = Drama(
        title=title,
        supplier_id=sup["id"],
        status="published",
        review_state="approved",
        synopsis="简介" * 20,
        free_episode_count=1,
    )
    session.add(drama)
    session.commit()
    episodes = []
    for number in (1, 2):
        ep = Episode(
            drama_id=drama.id,
            episode_number=number,
            title=f"第 {number} 集",
            status="published",
            duration_seconds=120,
            bunny_video_id=f"vid-{drama.id}-{number}",
            subtitle_url="",
            width=720,
            height=1280,
            size_bytes=1024,
            mime="video/mp4",
        )
        session.add(ep)
        episodes.append(ep)
    session.commit()
    return admin, partner, drama


def _plan_version(session) -> PlanVersion:
    billing.ensure_default_plans(session)
    plan = session.scalar(select(Plan).where(Plan.code == "monthly"))
    return session.scalar(
        select(PlanVersion).where(PlanVersion.plan_id == plan.id).order_by(PlanVersion.version.desc())
    )


def _paid_order(session, version, user_id, *, amount=1999, source=None, paid_at=None) -> Order:
    order = Order(
        order_no=f"t{user_id}-{amount}-{source}-{paid_at or ''}".replace(" ", "")[:60],
        user_id=user_id,
        plan_version_id=version.id,
        amount_cents=amount,
        currency="USD",
        billing_mode="one_time",
        status="paid",
        external_txn_id=f"cs-{user_id}-{amount}-{source}",
        paid_at=paid_at or datetime.now(timezone.utc),
        source_drama_id=source,
    )
    session.add(order)
    session.commit()
    return order


# ---- stats ----


def test_overview_counts_plays_unlocks_and_first_orders(make, session, client):
    admin, partner, drama = _setup(make, client, session)
    version = _plan_version(session)
    user_a = make.user(email="a@example.com")
    user_b = make.user(email="b@example.com")
    ep1, ep2 = session.scalars(
        select(Episode).where(Episode.drama_id == drama.id).order_by(Episode.episode_number)
    ).all()
    now = datetime.now(timezone.utc)
    session.add_all(
        [
            PlaybackEvent(user_id=user_a.id, episode_id=ep1.id, kind="start", position_seconds=0),
            PlaybackEvent(user_id=user_a.id, episode_id=ep1.id, kind="complete", position_seconds=120),
            PlaybackEvent(user_id=user_b.id, episode_id=ep2.id, kind="start", position_seconds=0),
        ]
    )
    session.add(
        CoinLedgerEntry(
            user_id=user_a.id,
            amount=-6,
            balance_after=94,
            biz_type="unlock",
            biz_key=f"unlock:{ep2.id}:{user_a.id}",
            episode_id=ep2.id,
        )
    )
    session.commit()
    _paid_order(session, version, user_a.id, source=drama.id, paid_at=now)
    # B's first order is organic → the later drama order must NOT be attributed.
    _paid_order(session, version, user_b.id, source=None, paid_at=now)
    _paid_order(session, version, user_b.id, source=drama.id, amount=999, paid_at=now)

    r = client.get("/partner/v1/stats/overview?days=30", headers=partner)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["plays"] == 2
    assert body["completes"] == 1
    assert body["completion_rate"] == 0.5
    assert body["viewers"] == 2
    assert body["unlocks"] == 1
    assert body["unlock_users"] == 1
    assert body["first_orders"] == 1
    assert body["gross_cents"] == 1999
    assert body["revenue_cents"] == 1599
    assert body["effective_share_bps"] == 8000


def test_stats_do_not_leak_other_supplier(make, session, client):
    admin, partner, drama = _setup(make, client, session)
    other = Drama(title="别家剧", supplier_id=None, status="published")
    session.add(other)
    session.commit()
    ep = Episode(
        drama_id=other.id,
        episode_number=1,
        title="1",
        status="published",
        duration_seconds=100,
        bunny_video_id="vid-other",
        subtitle_url="",
        width=720,
        height=1280,
        size_bytes=10,
        mime="video/mp4",
    )
    session.add(ep)
    session.commit()
    session.add(PlaybackEvent(user_id=None, episode_id=ep.id, kind="start", position_seconds=0))
    session.commit()

    body = client.get("/partner/v1/stats/dramas", headers=partner).json()
    assert [row["id"] for row in body["items"]] == [drama.id]
    assert body["items"][0]["plays"] == 0


# ---- settlements ----


def test_generate_settlement_uses_first_orders(make, session, client):
    admin, partner, drama = _setup(make, client, session)
    version = _plan_version(session)
    buyer = make.user(email="buyer@example.com")
    _paid_order(session, version, buyer.id, amount=1999, source=drama.id)
    month = datetime.now(timezone.utc).strftime("%Y-%m")

    r = client.post("/admin/v1/settlements/generate", json={"month": month}, headers=admin)
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 1

    listed = client.get(f"/admin/v1/settlements?month={month}", headers=admin).json()
    assert listed["total"] == 1
    row = listed["items"][0]
    assert row["gross_cents"] == 1999
    assert row["revenue_cents"] == 1599
    assert row["order_count"] == 1
    assert row["status"] == "draft"

    mine = client.get("/partner/v1/settlements", headers=partner).json()
    assert mine["items"][0]["month"] == month
    assert mine["items"][0]["revenue_cents"] == 1599


def test_share_at_order_time_splits_month(make, session, client):
    admin, partner, drama = _setup(make, client, session, share=8000)
    version = _plan_version(session)
    buyer = make.user(email="split@example.com")
    _paid_order(session, version, buyer.id, amount=1000, source=drama.id)
    # the supplier share drops to 70% *before* the next settlement month
    other = make.user(email="split2@example.com")
    _paid_order(session, version, other.id, amount=1000, source=drama.id)
    client.post(
        f"/admin/v1/suppliers/{drama.supplier_id}/share",
        json={"share_bps": 7000, "note": "测试"},
        headers=admin,
    )
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    client.post("/admin/v1/settlements/generate", json={"month": month}, headers=admin)
    row = client.get(f"/admin/v1/settlements?month={month}", headers=admin).json()["items"][0]
    # both orders were paid before the change → still 80%
    assert row["revenue_cents"] == 1600
    assert row["effective_share_bps"] == 8000


def test_confirmed_settlement_is_not_overwritten(make, session, client):
    admin, partner, drama = _setup(make, client, session)
    version = _plan_version(session)
    buyer = make.user(email="lock@example.com")
    _paid_order(session, version, buyer.id, amount=500, source=drama.id)
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    client.post("/admin/v1/settlements/generate", json={"month": month}, headers=admin)
    row = client.get(f"/admin/v1/settlements?month={month}", headers=admin).json()["items"][0]

    assert (
        client.post(
            f"/admin/v1/settlements/{row['id']}/status",
            json={"status": "confirmed"},
            headers=admin,
        ).status_code
        == 200
    )
    again = client.post("/admin/v1/settlements/generate", json={"month": month}, headers=admin).json()
    assert again["skipped"] == 1 and again["updated"] == 0

    paid = client.post(
        f"/admin/v1/settlements/{row['id']}/status", json={"status": "paid"}, headers=admin
    )
    assert paid.status_code == 200
    assert paid.json()["status"] == "paid"

    rollback = client.post(
        f"/admin/v1/settlements/{row['id']}/status", json={"status": "confirmed"}, headers=admin
    )
    assert rollback.status_code == 409


def test_ops_cannot_read_settlements_but_finance_can(make, session, client):
    admin, partner, drama = _setup(make, client, session)
    ops = make.admin(role="ops")
    ops_headers = _login(client, ops.username, "pass-1234")
    assert client.get("/admin/v1/settlements", headers=ops_headers).status_code == 403

    fin = _finance(make, client)
    assert client.get("/admin/v1/settlements", headers=fin).status_code == 200


def test_refunded_first_order_drops_out(make, session, client):
    admin, partner, drama = _setup(make, client, session)
    version = _plan_version(session)
    buyer = make.user(email="refund@example.com")
    order = _paid_order(session, version, buyer.id, amount=800, source=drama.id)
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    client.post("/admin/v1/settlements/generate", json={"month": month}, headers=admin)

    order.refunded_at = datetime.now(timezone.utc)
    session.commit()
    client.post("/admin/v1/settlements/generate", json={"month": month}, headers=admin)
    row = client.get(f"/admin/v1/settlements?month={month}", headers=admin).json()["items"][0]
    assert row["gross_cents"] == 0
    assert row["order_count"] == 0


def test_share_change_mid_month_splits_revenue(make, session, client):
    from datetime import timedelta

    admin, partner, drama = _setup(make, client, session, share=8000)
    version = _plan_version(session)
    first = make.user(email="first@example.com")
    second = make.user(email="second@example.com")
    now = datetime.now(timezone.utc)
    _paid_order(session, version, first.id, amount=1000, source=drama.id, paid_at=now)
    client.post(
        f"/admin/v1/suppliers/{drama.supplier_id}/share",
        json={"share_bps": 7000, "note": "月中调整"},
        headers=admin,
    )
    _paid_order(
        session, version, second.id, amount=1000, source=drama.id, paid_at=now + timedelta(seconds=1)
    )
    month = now.strftime("%Y-%m")
    client.post("/admin/v1/settlements/generate", json={"month": month}, headers=admin)
    row = client.get(f"/admin/v1/settlements?month={month}", headers=admin).json()["items"][0]
    assert row["gross_cents"] == 2000
    assert row["revenue_cents"] == 1500  # 800 + 700
    assert row["effective_share_bps"] == 7500
