"""Wallet service: idempotent coin unlock with ledger and entitlement writes.

Order of checks (per architecture): idempotent replay first, then member
priority (active subscription => no coin flow), then existing entitlement
(covers retries with a different key), then the charge itself inside one
transaction with a row lock. Insufficient balance leaves zero partial
writes behind.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.billing import service as billing
from app.modules.common.models import IdempotencyRecord
from app.modules.content.models import Drama, Episode
from app.modules.wallet.models import (
    ENTITLEMENT_TYPE_COIN,
    CoinLedgerEntry,
    Entitlement,
    WalletAccount,
)

UNLOCK_ENDPOINT = "unlock"


class UnlockError(Exception):
    """Base class for unlock failures."""


class EpisodeNotFoundError(UnlockError):
    pass


class EpisodeNotPurchasableError(UnlockError):
    """The drama has no usable coin price (0 / negative means not for sale)."""


class InsufficientCoinsError(UnlockError):
    def __init__(self, need: int, have: int):
        self.need = need
        self.have = have
        super().__init__(f"insufficient coins: need {need}, have {have}")


@dataclass
class UnlockResult:
    status: str  # unlocked / already_unlocked / subscription
    source: str  # coin / subscription
    episode_id: int
    balance_after: int | None
    replayed: bool = False


def unlock_episode(
    session: Session, *, user_id: int, episode_id: int, idempotency_key: str
) -> UnlockResult:
    # Keys are client-generated, so scope them to the caller: a key reused by a
    # different account must never replay someone else's outcome.
    scoped_key = _scoped_key(user_id, idempotency_key)

    # 1. idempotent replay: return the first outcome untouched
    record = session.scalar(
        select(IdempotencyRecord).where(IdempotencyRecord.idempotency_key == scoped_key)
    )
    if record is not None:
        payload = dict(record.response_payload)
        payload["replayed"] = True
        return UnlockResult(**payload)

    # 2. resolve episode + price; only published content is purchasable
    episode = session.get(Episode, episode_id)
    drama = None if episode is None else session.get(Drama, episode.drama_id)
    if (
        episode is None
        or drama is None
        or episode.status != "published"
        or drama.status != "published"
    ):
        raise EpisodeNotFoundError(f"episode {episode_id} not found")
    price = drama.coin_price_per_episode
    if price <= 0:
        # A zero price would write amount=0 and violate the ledger constraint.
        # Such content is membership-only (or misconfigured); never charge it.
        raise EpisodeNotPurchasableError(f"episode {episode_id} has no coin price")

    # 3. member priority: an active subscription never creates a coin flow
    # Subscription period or one-time pass, whichever ends last.
    if billing.access_until(session, user_id) is not None:
        result = UnlockResult(
            status="subscription", source="subscription", episode_id=episode_id, balance_after=None
        )
        _record_idempotency(session, scoped_key, result)
        session.commit()
        return result

    # 4. already unlocked? (also covers a retry that arrives with a different key)
    existing = session.scalar(
        select(Entitlement).where(
            Entitlement.user_id == user_id,
            Entitlement.episode_id == episode_id,
            Entitlement.entitlement_type == ENTITLEMENT_TYPE_COIN,
        )
    )
    if existing is not None:
        result = UnlockResult(
            status="already_unlocked",
            source="coin",
            episode_id=episode_id,
            balance_after=_balance_of(session, user_id),
        )
        _record_idempotency(session, scoped_key, result)
        session.commit()
        return result

    # 5. charge inside one transaction: row lock, nonnegative guard, ledger + entitlement
    wallet = session.get(WalletAccount, user_id, with_for_update=True)
    have = 0 if wallet is None else wallet.balance
    if wallet is None or wallet.balance < price:
        session.rollback()
        raise InsufficientCoinsError(need=price, have=have)

    wallet.balance -= price
    session.add(
        CoinLedgerEntry(
            user_id=user_id,
            amount=-price,
            balance_after=wallet.balance,
            biz_type="unlock",
            biz_key=scoped_key,
            episode_id=episode_id,
        )
    )
    session.add(
        Entitlement(
            user_id=user_id, episode_id=episode_id, entitlement_type=ENTITLEMENT_TYPE_COIN
        )
    )
    result = UnlockResult(
        status="unlocked", source="coin", episode_id=episode_id, balance_after=wallet.balance
    )
    _record_idempotency(session, scoped_key, result)
    session.commit()
    return result


def _scoped_key(user_id: int, idempotency_key: str) -> str:
    """Per-user storage key: keeps client keys from leaking across accounts."""
    digest = hashlib.sha256(idempotency_key.encode()).hexdigest()
    return f"u{user_id}:{digest}"


def _balance_of(session: Session, user_id: int) -> int:
    wallet = session.get(WalletAccount, user_id)
    return wallet.balance if wallet is not None else 0


def _record_idempotency(session: Session, key: str, result: UnlockResult) -> None:
    session.add(
        IdempotencyRecord(
            idempotency_key=key,
            endpoint=UNLOCK_ENDPOINT,
            response_payload=asdict(result),
        )
    )
