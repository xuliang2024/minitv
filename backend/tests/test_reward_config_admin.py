"""Admin coverage for the coin-task (reward config) page: /admin/v1/rewards/config."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.modules.account import service as accounts
from app.modules.common.models import AuditLog
from app.modules.ops.config_models import RewardConfig


@pytest.fixture()
def client(session):
    return TestClient(app)


def _login(client, username, password="pass-1234"):
    response = client.post("/admin/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _ops(make, client):
    staff = make.admin(role="ops")
    return _login(client, staff.username)


def test_overview_lists_preset_defaults_when_unconfigured(make, session, client):
    headers = _ops(make, client)
    data = client.get("/admin/v1/rewards/config", headers=headers).json()
    codes = [item["code"] for item in data["items"]]
    assert codes == [
        "checkin",
        "register",
        "bind_email",
        "bind_facebook",
        "ad",
        "like_5",
        "favorite_5",
        "watch_8",
    ]
    by_code = {item["code"]: item for item in data["items"]}
    assert by_code["checkin"]["kind"] == "gradient"
    assert by_code["checkin"]["size"] == 7
    assert by_code["checkin"]["current"]["amounts"] == accounts.DEFAULT_CHECKIN
    assert by_code["checkin"]["current"]["is_default"] is True
    assert by_code["register"]["current"]["amounts"] == [80]
    assert by_code["bind_email"]["current"]["amounts"] == [30]
    assert by_code["bind_facebook"]["current"]["amounts"] == [80]
    assert by_code["ad"]["current"]["amounts"] == [30]
    assert by_code["like_5"]["current"]["amounts"] == [20]
    assert by_code["like_5"]["period"] == "daily"
    assert by_code["like_5"]["target"] == 5
    assert by_code["favorite_5"]["current"]["amounts"] == [30]
    assert by_code["watch_8"]["current"]["amounts"] == [50]
    assert by_code["watch_8"]["target"] == 8
    assert all(item["pending"] is None for item in data["items"])
    assert data["next_effective_on"] == (accounts.la_today() + timedelta(days=1)).isoformat()
    assert data["recent_changes"] == []


def test_save_is_next_day_and_same_day_resave_updates_pending_row(make, session, client):
    headers = _ops(make, client)
    today = accounts.la_today()
    gradient = [10, 20, 30, 40, 50, 60, 70]
    first = client.post("/admin/v1/rewards/config", json={"code": "checkin", "amounts": gradient}, headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["effective_on"] == (today + timedelta(days=1)).isoformat()

    again = client.post(
        "/admin/v1/rewards/config", json={"code": "checkin", "amounts": [11, 21, 31, 41, 51, 61, 71]}, headers=headers
    )
    assert again.status_code == 200, again.text
    rows = session.scalars(
        select(RewardConfig).where(
            RewardConfig.code == "checkin", RewardConfig.effective_on == today + timedelta(days=1)
        )
    ).all()
    assert len(rows) == 1
    assert list(rows[0].amounts) == [11, 21, 31, 41, 51, 61, 71]

    overview = client.get("/admin/v1/rewards/config", headers=headers).json()
    checkin = next(item for item in overview["items"] if item["code"] == "checkin")
    assert checkin["current"]["is_default"] is True  # nothing is effective yet
    assert checkin["pending"]["amounts"] == [11, 21, 31, 41, 51, 61, 71]
    assert checkin["pending"]["effective_on"] == (today + timedelta(days=1)).isoformat()
    assert [row["code"] for row in overview["recent_changes"]] == ["checkin", "checkin"]
    actor = overview["recent_changes"][0]["actor"]
    assert isinstance(actor, str) and actor and not actor.startswith("#")
    assert session.scalar(select(func.count(AuditLog.id)).where(AuditLog.action == "reward_config_update")) == 2


def test_save_rejects_bad_codes_lengths_and_values(make, session, client):
    headers = _ops(make, client)
    unknown = client.post("/admin/v1/rewards/config", json={"code": "nope", "amounts": [1]}, headers=headers)
    assert unknown.status_code == 422
    short_gradient = client.post("/admin/v1/rewards/config", json={"code": "checkin", "amounts": [1, 2, 3]}, headers=headers)
    assert short_gradient.status_code == 422
    too_many_single = client.post(
        "/admin/v1/rewards/config", json={"code": "register", "amounts": [10, 20]}, headers=headers
    )
    assert too_many_single.status_code == 422
    zero = client.post("/admin/v1/rewards/config", json={"code": "ad", "amounts": [0]}, headers=headers)
    assert zero.status_code == 422
    assert session.scalar(select(func.count(RewardConfig.id))) == 0


def test_write_requires_content_writer_role(make, session, client):
    staff = make.admin(role="finance")
    headers = _login(client, staff.username)
    forbidden = client.post("/admin/v1/rewards/config", json={"code": "ad", "amounts": [30]}, headers=headers)
    assert forbidden.status_code == 403
    # Reads stay open to every staff role.
    assert client.get("/admin/v1/rewards/config", headers=headers).status_code == 200


def test_gradient_change_applies_tomorrow_not_today(make, session, client):
    headers = _ops(make, client)
    stored = client.post(
        "/admin/v1/rewards/config", json={"code": "checkin", "amounts": [5, 5, 5, 5, 5, 5, 5]}, headers=headers
    )
    assert stored.status_code == 200, stored.text
    today = accounts.la_today()
    user = make.user(email="gradient@example.com", coins=0)
    todays = accounts.check_in(session, user, today=today)
    assert todays["amount"] == accounts.DEFAULT_CHECKIN[0]
    tomorrows = accounts.check_in(session, user, today=today + timedelta(days=1))
    assert tomorrows["amount"] == 5
