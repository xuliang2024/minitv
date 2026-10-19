"""Daily task acceptance: distinct progress, per-day payout, no double credit."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.account import tasks as task_service
from app.modules.account.service import LA, AccountError, la_today
from app.modules.ops.config_models import RewardConfig, TaskClaim
from app.modules.playback.models import PlaybackEvent
from app.modules.social.models import Reaction
from app.modules.wallet.models import CoinLedgerEntry, WalletAccount

DAY = la_today()


@pytest.fixture()
def client(session):
    return TestClient(app)


def _at(day, hour: int = 12) -> datetime:
    return datetime.combine(day, time(hour), tzinfo=LA)


def _react(session, user, target_id: int, kind: str = "like", *, day=DAY, target_type: str = "drama"):
    session.add(
        Reaction(
            user_id=user.id,
            target_type=target_type,
            target_id=target_id,
            kind=kind,
            created_at=_at(day),
        )
    )
    session.commit()


def _complete(session, user, episode_id: int, *, day=DAY, kind: str = "complete", position: int = 120):
    session.add(
        PlaybackEvent(
            user_id=user.id,
            episode_id=episode_id,
            kind=kind,
            position_seconds=position,
            created_at=_at(day),
        )
    )
    session.commit()


def _episodes(make, count: int) -> list[int]:
    drama = make.drama(status="published")
    return [make.episode(drama, number=index + 1, status="published").id for index in range(count)]


def test_overview_lists_three_daily_tasks(make, session):
    user = make.user(coins=0)

    payload = task_service.tasks_overview(session, user, day=DAY)

    assert payload["today"] == DAY.isoformat()
    assert payload["balance"] == 0
    assert [item["code"] for item in payload["items"]] == ["like_5", "favorite_5", "watch_8"]
    assert [item["target"] for item in payload["items"]] == [5, 5, 8]
    assert [item["amount"] for item in payload["items"]] == [20, 30, 50]
    assert all(item["progress"] == 0 and item["claimed"] is False for item in payload["items"])
    assert payload["checkin"]["checked_today"] is False
    assert payload["checkin"]["streak"] == 1
    assert payload["checkin"]["amount"] == 30


def test_like_progress_counts_distinct_dramas_of_the_day(make, session):
    user = make.user()
    other = make.user(email="other@example.com")
    task = next(item for item in task_service.DAILY_TASKS if item["code"] == "like_5")

    for drama_id in range(1, 6):
        _react(session, user, drama_id)
    _react(session, user, 99, kind="favorite")
    _react(session, user, 100, target_type="episode")
    _react(session, other, 7)

    assert task_service.task_progress(session, user.id, task, DAY) == 5


def test_claim_below_target_is_rejected(make, session):
    user = make.user(coins=0)
    for drama_id in range(1, 5):
        _react(session, user, drama_id)

    with pytest.raises(AccountError) as excinfo:
        task_service.claim_task(session, user, "like_5", day=DAY)

    assert excinfo.value.status_code == 409
    session.expire_all()
    assert session.get(WalletAccount, user.id).balance == 0
    assert session.query(TaskClaim).count() == 0
    assert session.query(CoinLedgerEntry).count() == 0


def test_claim_pays_once_and_writes_ledger(make, session):
    user = make.user(coins=0)
    for drama_id in range(1, 6):
        _react(session, user, drama_id)

    first = task_service.claim_task(session, user, "like_5", day=DAY)
    assert first == {
        "code": "like_5",
        "amount": 20,
        "balance": 20,
        "replayed": False,
        "progress": 5,
    }

    session.expire_all()
    assert session.get(WalletAccount, user.id).balance == 20
    ledger = session.query(CoinLedgerEntry).one()
    assert (ledger.biz_type, ledger.amount, ledger.balance_after) == ("like_5", 20, 20)
    assert ledger.biz_key == f"task:like_5:{user.id}:{DAY.isoformat()}"
    assert session.query(TaskClaim).count() == 1

    second = task_service.claim_task(session, user, "like_5", day=DAY)
    assert second["replayed"] is True and second["amount"] == 0 and second["balance"] == 20

    session.expire_all()
    assert session.get(WalletAccount, user.id).balance == 20
    assert session.query(CoinLedgerEntry).count() == 1

    overview = task_service.tasks_overview(session, user, day=DAY)
    like_item = next(item for item in overview["items"] if item["code"] == "like_5")
    assert like_item["claimed"] is True


def test_watch_counts_distinct_episodes(make, session):
    user = make.user(coins=0)
    episodes = _episodes(make, 9)
    _complete(session, user, episodes[0])
    _complete(session, user, episodes[0])  # same episode finished twice = one
    for episode_id in episodes[1:8]:
        _complete(session, user, episode_id)
    _complete(session, user, episodes[1], kind="progress")  # heartbeats never count
    _complete(session, user, episodes[8], position=0)  # zero-position complete is ignored

    task = next(item for item in task_service.DAILY_TASKS if item["code"] == "watch_8")
    assert task_service.task_progress(session, user.id, task, DAY) == 8

    result = task_service.claim_task(session, user, "watch_8", day=DAY)
    assert result["amount"] == 50
    assert result["balance"] == 50


def test_yesterday_activity_does_not_count(make, session):
    user = make.user(coins=0)
    yesterday = DAY - timedelta(days=1)
    for drama_id in range(1, 6):
        _react(session, user, drama_id, day=yesterday)

    payload = task_service.tasks_overview(session, user, day=DAY)
    like_item = next(item for item in payload["items"] if item["code"] == "like_5")
    assert like_item["progress"] == 0

    with pytest.raises(AccountError):
        task_service.claim_task(session, user, "like_5", day=DAY)


def test_claim_uses_the_amount_effective_that_day(make, session):
    user = make.user(coins=0)
    session.add(RewardConfig(code="like_5", amounts=[99], effective_on=DAY))
    session.commit()
    for drama_id in range(1, 6):
        _react(session, user, drama_id)

    result = task_service.claim_task(session, user, "like_5", day=DAY)

    assert result["amount"] == 99
    assert result["balance"] == 99


def test_unknown_or_non_daily_task_is_rejected(make, session):
    user = make.user()

    with pytest.raises(AccountError) as unknown:
        task_service.claim_task(session, user, "nope", day=DAY)
    assert unknown.value.status_code == 404

    with pytest.raises(AccountError) as not_daily:
        task_service.claim_task(session, user, "register", day=DAY)
    assert not_daily.value.status_code == 404


def test_public_task_list_renders_without_a_session(client):
    response = client.get("/v1/tasks")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["balance"] == 0
    assert payload["checkin"] == {"checked_today": False, "streak": 1, "amount": 30}
    assert [item["progress"] for item in payload["items"]] == [0, 0, 0]
    assert [item["amount"] for item in payload["items"]] == [20, 30, 50]
    assert all(item["claimed"] is False for item in payload["items"])


def test_personal_task_list_still_requires_login(client):
    assert client.get("/v1/me/tasks").status_code == 401
