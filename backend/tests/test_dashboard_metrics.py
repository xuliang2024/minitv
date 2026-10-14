"""The dashboard must aggregate real product events, not hand-written numbers."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.modules.account.service import USER_SESSION_PREFIX, get_redis, la_today
from app.modules.billing.models import Order, Plan, PlanVersion
from app.modules.content.models import Drama
from app.modules.social.models import Comment
from app.modules.wallet.models import CoinLedgerEntry, Entitlement


@pytest.fixture()
def client(session):
    return TestClient(app)


def _admin_headers(make, client, role: str = "ops"):
    staff = make.admin(role=role)
    response = client.post(
        "/admin/v1/auth/login", json={"username": staff.username, "password": "pass-1234"}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _user_headers(user):
    token = f"dashboard-test-token-{user.id}"
    get_redis().set(USER_SESSION_PREFIX + token, str(user.id), ex=600)
    return {"Authorization": f"Bearer {token}"}


def test_playback_events_drive_active_users_plays_and_completion(make, client, session):
    headers = _admin_headers(make, client)
    drama = make.drama(title="Played Drama", status="published", free=1, price=60)
    episode = make.episode(drama, number=1, status="published")
    user = make.user(email="player@example.com")
    user_headers = _user_headers(user)

    for path, position in (
        ("/v1/playback/start", 0),
        ("/v1/playback/progress", 30),
        ("/v1/playback/complete", 60),
    ):
        response = client.post(
            path, json={"episode_id": episode.id, "position_seconds": position}, headers=user_headers
        )
        assert response.status_code == 200, response.text

    session.expire_all()
    # Lifetime counter is bumped by start events only.
    assert session.get(Drama, drama.id).play_count == 1

    board = client.get("/admin/v1/dashboard", headers=headers).json()
    assert board["timezone"] == "America/Los_Angeles"
    assert board["day"] == la_today().isoformat()
    assert board["active_users"] == 1
    assert board["plays"] == 1
    assert board["completes"] == 1
    assert board["completion_rate"] == 10000
    assert board["trend"][-1]["completion_rate"] == 10000
    assert board["top_dramas"][0] == {
        "id": drama.id,
        "title": "Played Drama",
        "plays": 1,
        "completes": 1,
        "completion_rate": 10000,
    }
    # Anonymous playback still counts as plays, never as active users.
    response = client.post(
        "/v1/playback/start", json={"episode_id": episode.id, "position_seconds": 0}
    )
    assert response.status_code == 200
    board = client.get("/admin/v1/dashboard", headers=headers).json()
    assert board["plays"] == 2
    assert board["active_users"] == 1
    assert board["completes"] == 1
    assert board["completion_rate"] == 5000


def test_dashboard_revenue_and_trend_use_paid_orders_by_local_day(make, client, session):
    headers = _admin_headers(make, client)
    user = make.user(email="buyer@example.com")
    plan = Plan(code="monthly-dash", name_en="Monthly")
    session.add(plan)
    session.flush()
    version = PlanVersion(plan_id=plan.id, version=1, price_cents=999)
    session.add(version)
    session.flush()
    # 05:30 UTC on the 13th is 22:30 on the 12th in Los Angeles.
    paid_at = dt.datetime(2026, 10, 13, 5, 30, tzinfo=dt.timezone.utc)
    session.add(
        Order(
            order_no="DASH-1",
            user_id=user.id,
            plan_version_id=version.id,
            amount_cents=999,
            status="paid",
            paid_at=paid_at,
        )
    )
    session.add(
        Order(
            order_no="DASH-2",
            user_id=user.id,
            plan_version_id=version.id,
            amount_cents=4999,
            status="created",
        )
    )
    session.commit()

    board = client.get(
        "/admin/v1/dashboard", params={"day": "2026-10-12", "days": 7}, headers=headers
    ).json()
    assert board["revenue_cents"] == 999
    assert board["paid_orders"] == 1
    assert board["window"] == {"start": "2026-10-06", "end": "2026-10-12", "days": 7}
    assert [row["day"] for row in board["trend"]] == [
        f"2026-10-{day:02d}" for day in range(6, 13)
    ]
    assert board["trend"][-1]["revenue_cents"] == 999
    assert board["previous"]["revenue_cents"] == 0

    # The dashboard always buckets by the Los Angeles business day, never UTC.
    other = client.get(
        "/admin/v1/dashboard", params={"day": "2026-10-13", "days": 1}, headers=headers
    ).json()
    assert other["revenue_cents"] == 0
    assert len(other["trend"]) == 1


def test_dashboard_coin_spend_unlocks_and_ops_overview(make, client, session):
    headers = _admin_headers(make, client)
    drama = make.drama(title="Locked Drama", status="published", free=1, price=60)
    episode = make.episode(drama, number=4, status="published")
    user = make.user(email="spender@example.com")
    session.add(
        CoinLedgerEntry(
            user_id=user.id,
            amount=-60,
            balance_after=40,
            biz_type="unlock",
            biz_key="dash-unlock-1",
            episode_id=episode.id,
        )
    )
    session.add(Entitlement(user_id=user.id, episode_id=episode.id))
    session.add(Comment(user_id=user.id, drama_id=drama.id, body="pending please", status="pending"))
    session.add(Comment(user_id=user.id, drama_id=drama.id, body="fine", status="approved"))
    session.commit()

    board = client.get("/admin/v1/dashboard", headers=headers).json()
    assert board["coin_spend"] == 60
    assert board["unlock_count"] == 1
    assert board["overview"]["total_users"] == 1
    assert board["overview"]["published_dramas"] == 1
    assert board["overview"]["published_episodes"] == 1
    assert board["overview"]["episodes_awaiting_publish"] == 0
    assert board["overview"]["pending_comments"] == 1
    assert board["overview"]["lifetime_plays"] == 0
