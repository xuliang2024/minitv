"""Stage-1 acceptance #3-#6: entitlement uniqueness, coin charge atomicity,
member priority, and idempotent replay."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.common.models import IdempotencyRecord
from app.modules.wallet import service as wallet_service
from app.modules.wallet.models import CoinLedgerEntry, Entitlement, WalletAccount
from app.modules.wallet.service import InsufficientCoinsError


def _published_priced_episode(make, price: int = 60):
    drama = make.drama(status="published", price=price)
    episode = make.episode(drama, number=1, status="published")
    return episode


# ---- acceptance 3: duplicate entitlement rejected at database layer ----


def test_duplicate_entitlement_rejected(make, session):
    user = make.user(coins=100)
    episode = _published_priced_episode(make)

    session.add(Entitlement(user_id=user.id, episode_id=episode.id, entitlement_type="coin"))
    session.commit()

    session.add(Entitlement(user_id=user.id, episode_id=episode.id, entitlement_type="coin"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


# ---- acceptance 4: insufficient balance leaves zero partial writes ----


def test_insufficient_balance_rolls_back_fully(make, session):
    user = make.user(coins=30)
    episode = _published_priced_episode(make, price=50)

    with pytest.raises(InsufficientCoinsError) as excinfo:
        wallet_service.unlock_episode(
            session, user_id=user.id, episode_id=episode.id, idempotency_key="k-insufficient"
        )
    assert excinfo.value.need == 50 and excinfo.value.have == 30

    session.expire_all()
    assert session.get(WalletAccount, user.id).balance == 30
    assert session.query(CoinLedgerEntry).count() == 0
    assert session.query(Entitlement).count() == 0
    assert session.query(IdempotencyRecord).count() == 0


def test_negative_balance_rejected_by_database(make, session):
    user = make.user(coins=10)

    wallet = session.get(WalletAccount, user.id)
    wallet.balance = -1
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


# ---- happy path: one charge writes ledger + entitlement atomically ----


def test_unlock_success_writes_ledger_and_entitlement(make, session):
    user = make.user(coins=100)
    episode = _published_priced_episode(make, price=60)

    result = wallet_service.unlock_episode(
        session, user_id=user.id, episode_id=episode.id, idempotency_key="k-ok"
    )

    assert result.status == "unlocked"
    assert result.balance_after == 40
    assert session.get(WalletAccount, user.id).balance == 40

    entries = session.query(CoinLedgerEntry).all()
    assert len(entries) == 1
    assert entries[0].amount == -60
    assert entries[0].balance_after == 40
    assert entries[0].biz_type == "unlock"
    assert entries[0].biz_key == "k-ok"
    assert entries[0].episode_id == episode.id

    entitlement = session.query(Entitlement).one()
    assert entitlement.user_id == user.id
    assert entitlement.episode_id == episode.id


# ---- acceptance 5: active subscription never writes a coin flow ----


def test_active_subscription_unlock_writes_no_coin_flow(make, session):
    user = make.user(coins=100)
    episode = _published_priced_episode(make, price=60)
    make.subscription(user, active=True)

    result = wallet_service.unlock_episode(
        session, user_id=user.id, episode_id=episode.id, idempotency_key="k-sub"
    )

    assert result.status == "subscription"
    assert result.source == "subscription"
    assert session.get(WalletAccount, user.id).balance == 100
    assert session.query(CoinLedgerEntry).count() == 0
    assert session.query(Entitlement).count() == 0


def test_expired_subscription_still_pays_with_coins(make, session):
    user = make.user(coins=100)
    episode = _published_priced_episode(make, price=60)
    make.subscription(user, active=False)  # canceled + period already ended

    result = wallet_service.unlock_episode(
        session, user_id=user.id, episode_id=episode.id, idempotency_key="k-expired-sub"
    )

    assert result.status == "unlocked"
    assert session.get(WalletAccount, user.id).balance == 40
    assert session.query(CoinLedgerEntry).count() == 1


# ---- acceptance 6: idempotent replay returns the first outcome ----


def test_idempotent_replay_returns_first_result(make, session):
    user = make.user(coins=100)
    episode = _published_priced_episode(make, price=60)

    first = wallet_service.unlock_episode(
        session, user_id=user.id, episode_id=episode.id, idempotency_key="k-replay"
    )
    second = wallet_service.unlock_episode(
        session, user_id=user.id, episode_id=episode.id, idempotency_key="k-replay"
    )

    assert first.status == "unlocked"
    assert first.replayed is False
    assert first.balance_after == 40

    assert second.replayed is True
    assert second.status == first.status
    assert second.balance_after == first.balance_after  # first outcome, not a new charge

    assert session.get(WalletAccount, user.id).balance == 40  # charged exactly once
    assert session.query(CoinLedgerEntry).count() == 1
    assert session.query(Entitlement).count() == 1
    assert session.query(IdempotencyRecord).count() == 1


def test_different_key_on_unlocked_episode_does_not_double_charge(make, session):
    user = make.user(coins=100)
    episode = _published_priced_episode(make, price=60)

    first = wallet_service.unlock_episode(
        session, user_id=user.id, episode_id=episode.id, idempotency_key="k-first"
    )
    second = wallet_service.unlock_episode(
        session, user_id=user.id, episode_id=episode.id, idempotency_key="k-second"
    )

    assert first.status == "unlocked"
    assert second.status == "already_unlocked"
    assert session.get(WalletAccount, user.id).balance == 40
    assert session.query(CoinLedgerEntry).count() == 1


def test_idempotency_key_unique_in_database(make, session):
    payload = {"status": "unlocked", "source": "coin"}
    session.add(IdempotencyRecord(idempotency_key="dup", endpoint="unlock", response_payload=payload))
    session.commit()

    session.add(IdempotencyRecord(idempotency_key="dup", endpoint="unlock", response_payload=payload))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
