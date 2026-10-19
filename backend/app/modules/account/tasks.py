"""Daily coin tasks: progress from real behaviour, one payout per local day.

The rule is intentionally boring: a daily task pays once per America/Los_Angeles
calendar day, progress only counts that same day, and every row is keyed by
``(user, code, day)`` so a retry can never pay twice. Amounts come from
``reward_configs`` the moment the claim is made, which keeps a mid-day config
change from rewriting what the user already earned.

Progress is counted in **distinct** objects, not events: liking and unliking the
same drama five times still counts as one, and completing the same episode
twice still counts as one episode.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.account.models import User
from app.modules.account.service import (
    DEFAULT_CHECKIN,
    LA,
    REWARD_TASK_BY_CODE,
    REWARD_TASKS,
    AccountError,
    _active_amount,
    _credit,
    la_today,
)
from app.modules.ops.config_models import Checkin, RewardConfig, TaskClaim
from app.modules.playback.models import PlaybackEvent
from app.modules.social.models import Reaction
from app.modules.wallet.models import WalletAccount

# Only tasks with an explicit daily period are served by /v1/me/tasks. The
# register / bind / ad tasks keep their own flows and are deliberately absent.
DAILY_TASKS: tuple[dict, ...] = tuple(task for task in REWARD_TASKS if task.get("period") == "daily")


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=LA)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def task_progress(session: Session, user_id: int, task: dict, day: date) -> int:
    """How many distinct objects the user touched inside the task's local day."""
    start, end = _day_bounds(day)
    if task["metric"] == "complete":
        # A complete event with no position is a client bug (or a cheap forgery);
        # both clients report the real duration, so zero never counts.
        statement = select(func.count(func.distinct(PlaybackEvent.episode_id))).where(
            PlaybackEvent.user_id == user_id,
            PlaybackEvent.kind == "complete",
            PlaybackEvent.position_seconds > 0,
            PlaybackEvent.created_at >= start,
            PlaybackEvent.created_at < end,
        )
    else:
        statement = select(func.count(func.distinct(Reaction.target_id))).where(
            Reaction.user_id == user_id,
            Reaction.kind == task["metric"],
            Reaction.target_type == "drama",
            Reaction.created_at >= start,
            Reaction.created_at < end,
        )
    return int(session.scalar(statement) or 0)


def _claim_of(session: Session, user_id: int, code: str, day: date) -> TaskClaim | None:
    return session.scalar(
        select(TaskClaim).where(
            TaskClaim.user_id == user_id,
            TaskClaim.code == code,
            TaskClaim.period_key == day.isoformat(),
        )
    )


def _balance(session: Session, user_id: int) -> int:
    wallet = session.get(WalletAccount, user_id)
    return wallet.balance if wallet is not None else 0


def _checkin_state(session: Session, user: User, day: date) -> dict:
    """What the task centre needs to paint the check-in card without guessing."""
    today_row = session.scalar(
        select(Checkin).where(Checkin.user_id == user.id, Checkin.day == day)
    )
    yesterday = session.scalar(
        select(Checkin).where(Checkin.user_id == user.id, Checkin.day == day - timedelta(days=1))
    )
    configured = session.scalar(
        select(RewardConfig)
        .where(RewardConfig.code == "checkin", RewardConfig.effective_on <= day)
        .order_by(RewardConfig.effective_on.desc())
    )
    gradient = list(configured.amounts) if configured is not None else list(DEFAULT_CHECKIN)

    if today_row is not None:
        return {
            "checked_today": True,
            "streak": today_row.streak,
            "amount": int(today_row.amount),
        }

    streak = 1 if yesterday is None else yesterday.streak + 1
    if streak > 7:
        streak = 1
    return {
        "checked_today": False,
        "streak": streak,
        "amount": int(gradient[(streak - 1) % 7]),
    }


def tasks_overview(session: Session, user: User, *, day: date | None = None) -> dict:
    day = day or la_today()
    items = []
    for task in DAILY_TASKS:
        progress = task_progress(session, user.id, task, day)
        items.append(
            {
                "code": task["code"],
                "name": task["name"],
                "description": task["description"],
                "metric": task["metric"],
                "target": int(task["target"]),
                "progress": progress,
                "amount": _active_amount(session, task["code"], day),
                "claimed": _claim_of(session, user.id, task["code"], day) is not None,
            }
        )
    return {
        "today": day.isoformat(),
        "balance": _balance(session, user.id),
        "checkin": _checkin_state(session, user, day),
        "items": items,
    }


def claim_task(
    session: Session, user: User, code: str, *, day: date | None = None
) -> dict:
    day = day or la_today()
    task = REWARD_TASK_BY_CODE.get(code)
    if task is None or task.get("period") != "daily":
        raise AccountError(404, f"unknown daily task '{code}'")

    progress = task_progress(session, user.id, task, day)
    existing = _claim_of(session, user.id, code, day)
    if existing is not None:
        return {
            "code": code,
            "amount": 0,
            "balance": _balance(session, user.id),
            "replayed": True,
            "progress": progress,
        }

    target = int(task["target"])
    if progress < target:
        raise AccountError(409, f"task not completed ({progress}/{target})")

    # Read the amount at claim time: a change saved today only applies tomorrow.
    amount = _active_amount(session, code, day)
    session.add(TaskClaim(user_id=user.id, code=code, period_key=day.isoformat(), amount=amount))
    try:
        balance = _credit(
            session, user.id, amount, code, f"task:{code}:{user.id}:{day.isoformat()}"
        )
        session.commit()
    except IntegrityError:
        # Two concurrent claims: the unique key wins, the loser replays.
        session.rollback()
        return {
            "code": code,
            "amount": 0,
            "balance": _balance(session, user.id),
            "replayed": True,
            "progress": progress,
        }

    return {"code": code, "amount": amount, "balance": balance, "replayed": False, "progress": progress}
