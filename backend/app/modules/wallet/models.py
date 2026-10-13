"""Wallet module: coin balance, ledger entries, unlock entitlements.

ADR-002: coins use a ledger; the balance column is a materialised aggregate
kept consistent inside each transaction (reconciled by the ledger-diff
metric in later stages). Every ledger row carries a globally unique
business key so retries can never double-book.
"""

from __future__ import annotations

import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

ENTITLEMENT_TYPE_COIN = "coin"


class WalletAccount(Base):
    __tablename__ = "wallet_accounts"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    balance: Mapped[int] = mapped_column(
        Integer,
        CheckConstraint("balance >= 0", name="ck_wallet_balance_nonnegative"),
        nullable=False,
        default=0,
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class CoinLedgerEntry(Base):
    """One row per coin change. Income is positive, spend is negative."""

    __tablename__ = "coin_ledger"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    amount: Mapped[int] = mapped_column(
        Integer,
        CheckConstraint("amount <> 0", name="ck_coin_ledger_amount_nonzero"),
        nullable=False,
    )
    balance_after: Mapped[int] = mapped_column(Integer, nullable=False)
    biz_type: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # unlock / register_reward / checkin / ad_reward / adjustment / ...
    biz_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    episode_id: Mapped[int | None] = mapped_column(ForeignKey("episodes.id"), nullable=True)
    note: Mapped[str] = mapped_column(String(500), nullable=False, default="")  # e.g. adjustment reason
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_coin_ledger_user_created", "user_id", "created_at"),)


class Entitlement(Base):
    __tablename__ = "entitlements"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"), nullable=False)
    entitlement_type: Mapped[str] = mapped_column(String(16), nullable=False, default=ENTITLEMENT_TYPE_COIN)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id", "episode_id", "entitlement_type", name="uq_entitlements_user_episode_type"
        ),
    )
