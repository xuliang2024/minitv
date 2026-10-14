"""Dashboard aggregates computed from real product events.

Every number the admin dashboard shows is derived from tables the product
itself writes:

- new users       -> users.created_at
- active users    -> distinct signed-in users with any playback event that day
- plays/completes -> playback_events kind=start / kind=complete
- revenue/orders  -> paid orders (orders.paid_at)
- coin spend      -> negative wallet ledger entries
- unlocks         -> coin entitlements

``daily_metrics`` is only a snapshot cache of those computations; the product
never writes it directly. Playback authorization does not call this module.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import Date, cast, func, select
from sqlalchemy.orm import Session

from app.modules.account.models import User
from app.modules.analytics.models import DailyMetric
from app.modules.billing.models import Order, Subscription
from app.modules.content.models import Drama, Episode
from app.modules.playback.models import PlaybackEvent
from app.modules.social.models import Comment
from app.modules.wallet.models import CoinLedgerEntry, Entitlement

LA = ZoneInfo("America/Los_Angeles")
TZ_NAME = "America/Los_Angeles"

METRIC_FIELDS = (
    "active_users",
    "new_users",
    "plays",
    "completes",
    "paid_orders",
    "revenue_cents",
    "coin_spend",
    "unlock_count",
    "completion_rate",
)
TREND_WINDOW_MAX = 90
TOP_DRAMAS_LIMIT = 5
_EPISODE_AWAITING_STATUSES = ("transcoding", "in_review")


def business_day(moment: datetime) -> date:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(LA).date()


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, datetime.min.time(), LA).astimezone(timezone.utc)
    return start, start + timedelta(days=1)


def _window_bounds(start_day: date, end_day: date) -> tuple[datetime, datetime]:
    start_utc, _ = _day_bounds(start_day)
    _, end_utc = _day_bounds(end_day)
    return start_utc, end_utc


def _local_day(column):
    """Postgres helper: a timestamptz column bucketed by the dashboard timezone."""
    return cast(func.timezone(TZ_NAME, column), Date)


def collect(session: Session, start_day: date, end_day: date) -> dict[date, dict]:
    """Aggregate every dashboard metric per business day in [start_day, end_day]."""
    start_utc, end_utc = _window_bounds(start_day, end_day)
    days: dict[date, dict] = {}
    cursor = start_day
    while cursor <= end_day:
        days[cursor] = {"day": cursor, **{field: 0 for field in METRIC_FIELDS}}
        cursor += timedelta(days=1)

    user_day = _local_day(User.created_at)
    for day, count in session.execute(
        select(user_day, func.count())
        .where(User.created_at >= start_utc, User.created_at < end_utc)
        .group_by(user_day)
    ):
        days[day]["new_users"] = int(count)

    event_day = _local_day(PlaybackEvent.created_at)
    for day, active, plays, completes in session.execute(
        select(
            event_day,
            func.count(func.distinct(PlaybackEvent.user_id)),
            func.count().filter(PlaybackEvent.kind == "start"),
            func.count().filter(PlaybackEvent.kind == "complete"),
        )
        .where(PlaybackEvent.created_at >= start_utc, PlaybackEvent.created_at < end_utc)
        .group_by(event_day)
    ):
        bucket = days[day]
        bucket["active_users"] = int(active or 0)
        bucket["plays"] = int(plays or 0)
        bucket["completes"] = int(completes or 0)

    order_day = _local_day(Order.paid_at)
    for day, orders, amount in session.execute(
        select(order_day, func.count(), func.coalesce(func.sum(Order.amount_cents), 0))
        .where(
            Order.status == "paid",
            Order.paid_at.is_not(None),
            Order.paid_at >= start_utc,
            Order.paid_at < end_utc,
        )
        .group_by(order_day)
    ):
        days[day]["paid_orders"] = int(orders)
        days[day]["revenue_cents"] = int(amount)

    ledger_day = _local_day(CoinLedgerEntry.created_at)
    for day, amount in session.execute(
        select(ledger_day, func.coalesce(func.sum(-CoinLedgerEntry.amount), 0))
        .where(
            CoinLedgerEntry.amount < 0,
            CoinLedgerEntry.created_at >= start_utc,
            CoinLedgerEntry.created_at < end_utc,
        )
        .group_by(ledger_day)
    ):
        days[day]["coin_spend"] = int(amount)

    ent_day = _local_day(Entitlement.created_at)
    for day, unlocks in session.execute(
        select(ent_day, func.count())
        .where(Entitlement.created_at >= start_utc, Entitlement.created_at < end_utc)
        .group_by(ent_day)
    ):
        days[day]["unlock_count"] = int(unlocks)

    for bucket in days.values():
        plays = bucket["plays"]
        bucket["completion_rate"] = int(bucket["completes"] * 10000 / plays) if plays else 0
    return days


def snapshot(session: Session, days: dict[date, dict]) -> None:
    """Refresh the daily_metrics cache rows for the given window."""
    existing = {
        row.business_day: row
        for row in session.scalars(select(DailyMetric).where(DailyMetric.business_day.in_(days.keys())))
    }
    for day, bucket in days.items():
        row = existing.get(day)
        if row is None:
            row = DailyMetric(business_day=day, timezone=TZ_NAME)
            session.add(row)
        row.timezone = TZ_NAME
        for field in METRIC_FIELDS:
            setattr(row, field, bucket[field])
    session.commit()


def aggregate_day(session: Session, day: date) -> DailyMetric:
    """Recompute and cache a single business day (used by tests and backfills)."""
    snapshot(session, collect(session, day, day))
    return session.scalar(select(DailyMetric).where(DailyMetric.business_day == day))


def top_dramas(session: Session, start_day: date, end_day: date, limit: int = TOP_DRAMAS_LIMIT) -> list[dict]:
    """Dramas ranked by real playback starts inside the window."""
    start_utc, end_utc = _window_bounds(start_day, end_day)
    plays = func.count().filter(PlaybackEvent.kind == "start")
    completes = func.count().filter(PlaybackEvent.kind == "complete")
    rows = session.execute(
        select(Drama.id, Drama.title, plays.label("plays"), completes.label("completes"))
        .select_from(PlaybackEvent)
        .join(Episode, Episode.id == PlaybackEvent.episode_id)
        .join(Drama, Drama.id == Episode.drama_id)
        .where(PlaybackEvent.created_at >= start_utc, PlaybackEvent.created_at < end_utc)
        .group_by(Drama.id, Drama.title)
        .order_by(plays.desc(), Drama.id)
        .limit(limit)
    )
    return [
        {
            "id": drama_id,
            "title": title,
            "plays": int(play_count),
            "completes": int(complete_count),
            "completion_rate": int(complete_count * 10000 / play_count) if play_count else 0,
        }
        for drama_id, title, play_count, complete_count in rows
    ]


def overview(session: Session) -> dict:
    """Lifetime counters and the current operations backlog (all real counts)."""
    total_users = session.scalar(select(func.count()).select_from(User)) or 0
    active_members = session.scalar(
        select(func.count())
        .select_from(Subscription)
        .where(Subscription.status == "active", Subscription.current_period_end > func.now())
    ) or 0
    published_dramas = session.scalar(
        select(func.count()).select_from(Drama).where(Drama.status == "published")
    ) or 0
    published_episodes = session.scalar(
        select(func.count()).select_from(Episode).where(Episode.status == "published")
    ) or 0
    awaiting_publish = session.scalar(
        select(func.count())
        .select_from(Episode)
        .where(Episode.status.in_(_EPISODE_AWAITING_STATUSES))
    ) or 0
    pending_comments = session.scalar(
        select(func.count()).select_from(Comment).where(Comment.status == "pending")
    ) or 0
    lifetime_revenue = session.scalar(
        select(func.coalesce(func.sum(Order.amount_cents), 0)).where(Order.status == "paid")
    ) or 0
    # Platform-recorded plays only; seeded catalog metadata is not counted.
    lifetime_plays = session.scalar(
        select(func.count()).select_from(PlaybackEvent).where(PlaybackEvent.kind == "start")
    ) or 0
    return {
        "total_users": int(total_users),
        "active_members": int(active_members),
        "published_dramas": int(published_dramas),
        "published_episodes": int(published_episodes),
        "episodes_awaiting_publish": int(awaiting_publish),
        "pending_comments": int(pending_comments),
        "lifetime_revenue_cents": int(lifetime_revenue),
        "lifetime_plays": int(lifetime_plays),
    }


def _row(bucket: dict) -> dict:
    return {"day": bucket["day"].isoformat(), **{field: bucket[field] for field in METRIC_FIELDS}}


def dashboard(session: Session, day: date, days: int = 7) -> dict:
    days = max(1, min(int(days), TREND_WINDOW_MAX))
    start_day = day - timedelta(days=days - 1)
    window = collect(session, start_day, day)
    snapshot(session, window)
    trend = [_row(window[current]) for current in sorted(window)]
    selected = trend[-1]
    previous = trend[-2] if len(trend) > 1 else {field: 0 for field in METRIC_FIELDS}
    return {
        "timezone": TZ_NAME,
        "day": selected["day"],
        "window": {"start": start_day.isoformat(), "end": day.isoformat(), "days": days},
        "trend": trend,
        "top_dramas": top_dramas(session, start_day, day),
        "overview": overview(session),
        **{field: selected[field] for field in METRIC_FIELDS},
        "previous": {field: previous.get(field, 0) for field in METRIC_FIELDS},
    }
