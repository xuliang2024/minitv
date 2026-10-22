"""Operational configuration: tabs, banners, copy, marketing, rewards."""

from __future__ import annotations

import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ChannelTab(Base):
    __tablename__ = "channel_tabs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    title_en: Mapped[str] = mapped_column(String(64), nullable=False)
    title_zh: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Discover is the only fixed tab. Every other tab is a homepage group shown
    # as its own page, so it points at one home_groups row.
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("home_groups.id"), nullable=True)


class Banner(Base):
    __tablename__ = "banners"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    slot: Mapped[str] = mapped_column(String(32), nullable=False, default="home")
    drama_id: Mapped[int | None] = mapped_column(ForeignKey("dramas.id"), nullable=True)
    starts_on: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    ends_on: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="online")
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # The two lines painted over the hero still: a gold hook, then the title.
    kicker: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    headline: Mapped[str] = mapped_column(String(80), nullable=False, default="")


class HotSearch(Base):
    __tablename__ = "hot_searches"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    term: Mapped[str] = mapped_column(String(80), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class CopyEntry(Base):
    __tablename__ = "copy_entries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    en: Mapped[str] = mapped_column(Text, nullable=False)
    zh: Mapped[str] = mapped_column(Text, nullable=False, default="")


class MarketingStat(Base):
    """Configured marketing numbers. Never read from live playback counters."""

    __tablename__ = "marketing_stats"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str] = mapped_column(String(64), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Testimonial(Base):
    __tablename__ = "testimonials"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    author: Mapped[str] = mapped_column(String(64), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class StoreLink(Base):
    __tablename__ = "store_links"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False, default="")


class LegalPage(Base):
    __tablename__ = "legal_pages"

    slug: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")


class RewardConfig(Base):
    """Reward amounts. A newly saved row takes effect on effective_on (next day)."""

    __tablename__ = "reward_configs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False)  # checkin / register / bind_email / bind_facebook / ad
    amounts: Mapped[list] = mapped_column(JSONB, nullable=False)
    effective_on: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (UniqueConstraint("code", "effective_on", name="uq_reward_code_day"),)


class Checkin(Base):
    __tablename__ = "checkins"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    day: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    streak: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (UniqueConstraint("user_id", "day", name="uq_checkin_user_day"),)


class AdClaim(Base):
    __tablename__ = "ad_claims"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    day: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (UniqueConstraint("user_id", "day", name="uq_ad_claim_user_day"),)


class TaskClaim(Base):
    """One payout per user, task and local day. Daily tasks reset at midnight LA."""

    __tablename__ = "task_claims"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(32), nullable=False)  # like_5 / favorite_5 / watch_8
    period_key: Mapped[str] = mapped_column(String(16), nullable=False)  # America/Los_Angeles day
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("user_id", "code", "period_key", name="uq_task_claim_user_code_day"),
    )


class ModerationSettings(Base):
    """Single-row config for OpenAI-backed comment pre-moderation.

    Everything operations tune (model, prompt, thresholds, fallback) lives here.
    ``api_key_secret`` is the Fernet-encrypted credential edited from the admin
    page; when it is empty the key falls back to ``OPENAI_API_KEY``.
    """

    __tablename__ = "moderation_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    base_url: Mapped[str] = mapped_column(String(200), nullable=False, default="https://api.openai.com/v1")
    model: Mapped[str] = mapped_column(String(64), nullable=False, default="gpt-4o-mini")
    categories: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    approve_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=0.75)
    block_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=0.75)
    fallback_action: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=6)
    api_key_secret: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
