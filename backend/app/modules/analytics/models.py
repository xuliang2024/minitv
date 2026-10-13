"""Read models for the admin dashboard. Playback never writes these rows."""

from __future__ import annotations

import datetime

from sqlalchemy import BigInteger, Date, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class DailyMetric(Base):
    __tablename__ = "daily_metrics"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    business_day: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="America/Los_Angeles")
    active_users: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new_users: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    revenue_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    coin_spend: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_rate: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # basis points
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (UniqueConstraint("business_day", name="uq_daily_metrics_day"),)
