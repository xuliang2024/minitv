"""Dashboard aggregates. Playback authorization does not call this module."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.modules.account.models import User
from app.modules.analytics.models import DailyMetric
from app.modules.billing.models import Order
from app.modules.content.models import Drama
from app.modules.playback.models import PlaybackEvent
from app.modules.wallet.models import CoinLedgerEntry

LA = ZoneInfo("America/Los_Angeles")
TZ_NAME = "America/Los_Angeles"


def business_day(moment: datetime) -> date:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(LA).date()


def aggregate_day(session: Session, day: date) -> DailyMetric:
    start = datetime.combine(day, datetime.min.time(), LA).astimezone(timezone.utc)
    end = start + timedelta(days=1)
    new_users = session.scalar(
        select(func.count()).select_from(User).where(User.created_at >= start, User.created_at < end)
    ) or 0
    active = session.scalar(
        select(func.count(func.distinct(PlaybackEvent.user_id))).where(
            PlaybackEvent.created_at >= start,
            PlaybackEvent.created_at < end,
            PlaybackEvent.user_id.is_not(None),
        )
    ) or 0
    revenue = session.scalar(
        select(func.coalesce(func.sum(Order.amount_cents), 0)).where(
            Order.status == "paid", Order.paid_at >= start, Order.paid_at < end
        )
    ) or 0
    coin_spend = session.scalar(
        select(func.coalesce(func.sum(-CoinLedgerEntry.amount), 0)).where(
            CoinLedgerEntry.amount < 0,
            CoinLedgerEntry.created_at >= start,
            CoinLedgerEntry.created_at < end,
        )
    ) or 0
    completes = session.scalar(
        select(func.count()).select_from(PlaybackEvent).where(
            PlaybackEvent.kind == "complete",
            PlaybackEvent.created_at >= start,
            PlaybackEvent.created_at < end,
        )
    ) or 0
    starts = session.scalar(
        select(func.count()).select_from(PlaybackEvent).where(
            PlaybackEvent.kind == "progress",
            PlaybackEvent.created_at >= start,
            PlaybackEvent.created_at < end,
        )
    ) or 0
    rate = int(completes * 10000 / starts) if starts else 0
    row = session.scalar(select(DailyMetric).where(DailyMetric.business_day == day))
    if row is None:
        row = DailyMetric(business_day=day, timezone=TZ_NAME)
        session.add(row)
    row.active_users = active
    row.new_users = new_users
    row.revenue_cents = int(revenue)
    row.coin_spend = int(coin_spend)
    row.completion_rate = rate
    session.commit()
    return row


def top_dramas(session: Session, limit: int = 5) -> list[Drama]:
    return list(session.scalars(select(Drama).order_by(Drama.play_count.desc(), Drama.id).limit(limit)))


def dashboard(session: Session, day: date) -> dict:
    row = aggregate_day(session, day)
    week = []
    for offset in range(6, -1, -1):
        metric = aggregate_day(session, day - timedelta(days=offset))
        week.append(
            {
                "day": metric.business_day.isoformat(),
                "active_users": metric.active_users,
                "revenue_cents": metric.revenue_cents,
            }
        )
    return {
        "timezone": TZ_NAME,
        "day": day.isoformat(),
        "active_users": row.active_users,
        "new_users": row.new_users,
        "revenue_cents": row.revenue_cents,
        "coin_spend": row.coin_spend,
        "completion_rate": row.completion_rate,
        "trend": week,
        "top_dramas": [{"id": d.id, "title": d.title, "play_count": d.play_count} for d in top_dramas(session)],
    }
