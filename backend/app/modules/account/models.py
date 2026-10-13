"""Account module: users, third-party identities, e-mail verification codes."""

from __future__ import annotations

import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class User(Base):
    """Registration is e-mail + verification code only; no password column."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email: Mapped[str | None] = mapped_column(String(320), unique=True, nullable=True)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    avatar_url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    gender: Mapped[str] = mapped_column(String(16), nullable=False, default="unspecified")
    bio: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    birthday: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    region: Mapped[str] = mapped_column(String(8), nullable=False, default="")
    locale: Mapped[str] = mapped_column(String(8), nullable=False, default="en")
    public_id: Mapped[str | None] = mapped_column(String(16), unique=True, nullable=True)
    following_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    follower_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    received_likes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reminder_opt_in: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ThirdPartyIdentity(Base):
    __tablename__ = "third_party_identities"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)  # facebook / google / tiktok
    provider_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("provider", "provider_user_id", name="uq_tpi_provider_uid"),
        UniqueConstraint("user_id", "provider", name="uq_tpi_user_provider"),
    )


class VerificationCode(Base):
    """One-time e-mail codes. Only the hash is stored, never the plaintext."""

    __tablename__ = "verification_codes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)  # register / bind / login
    code_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_verification_codes_email_purpose", "email", "purpose"),)
