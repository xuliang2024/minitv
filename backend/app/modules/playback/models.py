"""Playback events and resume points. Aggregates are not updated here."""

from __future__ import annotations

import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class PlaybackEvent(Base):
    __tablename__ = "playback_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # progress / complete
    position_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PlaybackProgress(Base):
    __tablename__ = "playback_progress"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"), primary_key=True)
    position_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (UniqueConstraint("user_id", "episode_id", name="uq_progress_user_episode"),)
