"""Content module: dramas, episodes, tags, home groups and their links.

Status machines (mirrored by database triggers, see alembic migration):
- drama: draft -> in_review -> published -> unpublished (+ re-review / re-publish)
- episode: not_uploaded -> transcoding -> {in_review, transcode_failed}; transcode_failed -> transcoding; in_review -> {published, not_uploaded}
Deletion is never a status; it is a separate irreversible operation (stage 6).
"""

from __future__ import annotations

import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

DRAMA_STATUSES = ("draft", "in_review", "published", "unpublished")
EPISODE_STATUSES = ("not_uploaded", "transcoding", "transcode_failed", "in_review", "published")


class Drama(Base):
    __tablename__ = "dramas"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    synopsis: Mapped[str] = mapped_column(Text, nullable=False, default="")
    cover_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    free_episode_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    coin_price_per_episode: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    country_allowlist: Mapped[str] = mapped_column(
        String(200), nullable=False, default=""
    )  # comma-separated ISO codes; empty means all countries
    age_rating: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)  # kept as a field; rule not frozen yet
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    published_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_dramas_status", "status"),
        UniqueConstraint("title", name="uq_dramas_title"),
    )


class Episode(Base):
    __tablename__ = "episodes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    drama_id: Mapped[int] = mapped_column(ForeignKey("dramas.id"), nullable=False)
    episode_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bunny_video_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")  # filled in stage 3
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="not_uploaded")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("drama_id", "episode_number", name="uq_episodes_drama_number"),
        Index("ix_episodes_drama_status", "drama_id", "status"),
    )


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name_en: Mapped[str] = mapped_column(String(64), nullable=False)
    name_zh: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    category: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="enabled")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (UniqueConstraint("name_en", name="uq_tags_name_en"),)


class DramaTag(Base):
    __tablename__ = "drama_tags"

    drama_id: Mapped[int] = mapped_column(ForeignKey("dramas.id"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id"), primary_key=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class HomeGroup(Base):
    """Homepage group. Capacity (max 20 dramas per group) is enforced by the
    content service from stage 6, as is position semantics."""

    __tablename__ = "home_groups"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # group order
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class DramaHomeGroup(Base):
    """Membership + in-group ordering. In-group order and group order are independent."""

    __tablename__ = "drama_home_groups"

    group_id: Mapped[int] = mapped_column(ForeignKey("home_groups.id"), primary_key=True)
    drama_id: Mapped[int] = mapped_column(ForeignKey("dramas.id"), primary_key=True)
    position_in_group: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
