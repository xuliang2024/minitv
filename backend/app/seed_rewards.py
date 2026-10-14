"""Seed the baseline coin-task (reward) configuration for local development.

Usage:
    cd backend && ../.venv/bin/python -m app.seed_rewards
    cd backend && ../.venv/bin/python -m app.seed_rewards --reset

Why a script instead of raw SQL: one reward task is not one row forever. The
``reward_configs`` table is append-only per ``(code, effective_on)`` so that a
mid-day edit never rewrites history; a change saved today only takes effect
tomorrow. The preset rows therefore land **today**, which makes the values
live immediately and gives the admin console a real "current" baseline to
diff pending changes against.

Guarantees:
* Idempotent - a task that already has a row effective today or earlier is
  left untouched, so re-running never duplicates or rewrites history.
* Deterministic - the preset amounts match ``REWARD_TASKS`` in
  ``app.modules.account.service`` (and the stage-11 development plan).
* Isolated - ``--reset`` only touches the codes listed in the catalog.
"""

from __future__ import annotations

import argparse

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import get_engine, make_session_factory
from app.modules.account.service import REWARD_TASKS, la_today
from app.modules.ops.config_models import RewardConfig


def _latest_effective(db: Session, code: str, today):
    return db.scalar(
        select(RewardConfig)
        .where(RewardConfig.code == code, RewardConfig.effective_on <= today)
        .order_by(RewardConfig.effective_on.desc())
    )


def seed_rewards(session: Session, *, reset: bool = False) -> dict:
    """Write the preset baseline rows and report what happened."""
    today = la_today()
    summary = {"effective_on": today.isoformat(), "created": [], "skipped": [], "removed": 0}
    if reset:
        codes = [task["code"] for task in REWARD_TASKS]
        summary["removed"] = session.execute(
            delete(RewardConfig).where(RewardConfig.code.in_(codes))
        ).rowcount
        session.flush()
    for task in REWARD_TASKS:
        existing = _latest_effective(session, task["code"], today)
        if existing is not None:
            summary["skipped"].append(
                {"code": task["code"], "effective_on": existing.effective_on.isoformat(), "amounts": list(existing.amounts)}
            )
            continue
        session.add(RewardConfig(code=task["code"], amounts=list(task["amounts"]), effective_on=today))
        summary["created"].append({"code": task["code"], "amounts": list(task["amounts"])})
    session.commit()
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed preset coin-task reward configs.")
    parser.add_argument("--reset", action="store_true", help="delete catalog rows first, then re-seed today's baseline")
    args = parser.parse_args()
    factory = make_session_factory(get_engine())
    with factory() as session:
        summary = seed_rewards(session, reset=args.reset)
    if summary["removed"]:
        print(f"reset: removed {summary['removed']} reward_configs row(s)")
    for row in summary["created"]:
        print(f"created: {row['code']} effective {summary['effective_on']} -> {row['amounts']}")
    for row in summary["skipped"]:
        print(f"skip: {row['code']} already effective {row['effective_on']} -> {row['amounts']}")


if __name__ == "__main__":
    main()
