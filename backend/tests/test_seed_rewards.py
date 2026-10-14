"""Tests for the preset coin-task config seeder: preset shape and idempotency."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from app.modules.account.service import REWARD_TASKS, la_today
from app.modules.ops.config_models import RewardConfig
from app.seed_rewards import seed_rewards


def test_seed_writes_preset_rows_effective_today(session):
    summary = seed_rewards(session)
    today = la_today()
    assert summary["removed"] == 0
    assert [row["code"] for row in summary["created"]] == [task["code"] for task in REWARD_TASKS]
    rows = session.scalars(select(RewardConfig).order_by(RewardConfig.id)).all()
    assert len(rows) == len(REWARD_TASKS)
    by_code = {row.code: row for row in rows}
    assert list(by_code["checkin"].amounts) == [30, 30, 50, 50, 30, 60, 90]
    assert list(by_code["register"].amounts) == [80]
    assert list(by_code["bind_email"].amounts) == [30]
    assert list(by_code["bind_facebook"].amounts) == [80]
    assert list(by_code["ad"].amounts) == [30]
    assert all(row.effective_on == today for row in rows)


def test_seed_is_idempotent(session):
    seed_rewards(session)
    second = seed_rewards(session)
    assert second["created"] == []
    assert [row["code"] for row in second["skipped"]] == [task["code"] for task in REWARD_TASKS]
    assert session.scalar(select(func.count(RewardConfig.id))) == len(REWARD_TASKS)


def test_reset_replaces_everything_with_todays_baseline(session):
    today = la_today()
    session.add(RewardConfig(code="checkin", amounts=[1, 1, 1, 1, 1, 1, 1], effective_on=today + timedelta(days=1)))
    session.add(RewardConfig(code="checkin", amounts=[2, 2, 2, 2, 2, 2, 2], effective_on=today))
    session.commit()
    summary = seed_rewards(session, reset=True)
    assert summary["removed"] == 2
    rows = session.scalars(select(RewardConfig).order_by(RewardConfig.code)).all()
    assert len(rows) == len(REWARD_TASKS)
    assert all(row.effective_on == today for row in rows)
    checkin = next(row for row in rows if row.code == "checkin")
    assert list(checkin.amounts) == [30, 30, 50, 50, 30, 60, 90]
