"""Tests for the mock-user seeder: idempotency and cross-table consistency."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.modules.account.models import ThirdPartyIdentity, User
from app.modules.billing.models import Order, Subscription
from app.seed_users import DEFAULT_COUNT, demo_emails, purge_users, seed_users
from app.modules.wallet.models import CoinLedgerEntry, WalletAccount


@pytest.fixture()
def client(session):
    return TestClient(app)


def _ledger(session, user_id: int) -> list[CoinLedgerEntry]:
    return list(
        session.scalars(
            select(CoinLedgerEntry).where(CoinLedgerEntry.user_id == user_id).order_by(CoinLedgerEntry.id)
        )
    )


# ---- shape ----


def test_seed_creates_twenty_users_with_wallets(session):
    summary = seed_users(session)
    assert summary["created"] == DEFAULT_COUNT
    assert summary["requested"] == 20

    users = list(session.scalars(select(User).order_by(User.id)))
    assert len(users) == 20
    assert len(demo_emails()) == 20
    assert all(user.email.endswith("@example.com") for user in users)

    public_ids = [user.public_id for user in users]
    assert len(set(public_ids)) == 20
    assert all(pid is not None and pid.isdigit() and len(pid) == 10 for pid in public_ids)
    assert all(user.display_name for user in users)
    assert len({user.display_name for user in users}) == 20

    for user in users:
        wallet = session.get(WalletAccount, user.id)
        assert wallet is not None, f"user {user.id} has no wallet"
        assert wallet.balance >= 0


def test_seed_is_idempotent(session):
    first = seed_users(session)
    second = seed_users(session)

    assert first["created"] == 20
    assert second == {"created": 0, "skipped": 20, "removed": 0, "requested": 20}
    assert session.scalar(select(func.count()).select_from(User)) == 20
    assert session.scalar(select(func.count()).select_from(WalletAccount)) == 20
    # no duplicate ledger business keys were attempted
    keys = list(session.scalars(select(CoinLedgerEntry.biz_key)))
    assert len(keys) == len(set(keys))


def test_seed_is_deterministic(session):
    seed_users(session, now=dt.datetime(2026, 10, 14, 1, 0, tzinfo=dt.timezone.utc))
    first = [(u.email, u.public_id, u.display_name, u.region, u.gender) for u in session.scalars(select(User).order_by(User.id))]
    coins = [session.get(WalletAccount, u.id).balance for u in session.scalars(select(User).order_by(User.id))]

    purge_users(session, demo_emails())
    session.commit()
    seed_users(session, now=dt.datetime(2026, 10, 14, 1, 0, tzinfo=dt.timezone.utc))
    second = [(u.email, u.public_id, u.display_name, u.region, u.gender) for u in session.scalars(select(User).order_by(User.id))]
    coins_again = [session.get(WalletAccount, u.id).balance for u in session.scalars(select(User).order_by(User.id))]

    assert first == second
    assert coins == coins_again


# ---- money invariants (ADR-002) ----


def test_wallet_balance_equals_ledger_sum(session):
    seed_users(session)
    users = list(session.scalars(select(User)))
    assert users

    for user in users:
        rows = _ledger(session, user.id)
        assert rows, f"user {user.email} has no ledger entries"
        total = sum(row.amount for row in rows)
        assert total == session.get(WalletAccount, user.id).balance
        running = 0
        for row in rows:
            running += row.amount
            assert row.balance_after == running
            assert row.amount != 0
            assert row.created_at >= user.created_at - dt.timedelta(seconds=1)


def test_members_have_active_subscription_and_paid_order(session):
    now = dt.datetime.now(dt.timezone.utc)
    seed_users(session, now=now)

    members = list(session.scalars(select(Subscription).where(Subscription.status == "active")))
    assert len(members) == 7  # every third demo user subscribes

    for sub in members:
        assert sub.current_period_start < now < sub.current_period_end
        order = session.scalar(select(Order).where(Order.user_id == sub.user_id))
        assert order is not None and order.status == "paid"
        assert order.paid_at is not None
        assert order.plan_version_id == sub.plan_version_id


def test_member_flag_matches_admin_payload(session, client, make):
    seed_users(session)
    admin = make.admin(role="admin")
    token = client.post("/admin/v1/auth/login", json={"username": admin.username, "password": "pass-1234"}).json()["token"]
    r = client.get("/admin/v1/users", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text

    items = r.json()["items"]
    assert len(items) == 20
    assert sum(1 for item in items if item["member"]) == 7
    for item in items:
        assert item["display_name"]
        assert len(item["public_id"]) == 10
        assert item["coins"] == session.get(WalletAccount, item["id"]).balance
        assert item["created_at"]


def test_admin_can_adjust_coins_on_a_seeded_user(session, client, make):
    """The seeded wallet plugs into the existing admin coin-adjust flow."""
    seed_users(session)
    user = session.scalar(select(User).order_by(User.id))
    before = session.get(WalletAccount, user.id).balance

    admin = make.admin(role="admin")
    token = client.post("/admin/v1/auth/login", json={"username": admin.username, "password": "pass-1234"}).json()["token"]
    r = client.post(
        f"/admin/v1/users/{user.id}/coins",
        json={"delta": 100, "reason": "manual top-up"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"before": before, "delta": 100, "after": before + 100}

    session.expire_all()
    assert session.get(WalletAccount, user.id).balance == before + 100
    rows = _ledger(session, user.id)
    assert sum(row.amount for row in rows) == before + 100  # ledger still sums to the balance
    assert rows[-1].biz_type == "adjustment" and rows[-1].note == "manual top-up"


def test_seed_links_third_party_identities(session):
    seed_users(session)
    identities = list(session.scalars(select(ThirdPartyIdentity)))
    assert identities
    assert {row.provider for row in identities} <= {"facebook", "google"}
    user_ids = {user.id for user in session.scalars(select(User))}
    assert {row.user_id for row in identities} <= user_ids


# ---- reset ----


def test_reset_removes_only_demo_users(session, make):
    keep = make.user(email="real.person@customer.io", coins=500)
    seed_users(session)
    assert session.scalar(select(func.count()).select_from(User)) == 21

    summary = seed_users(session, reset=True)
    assert summary["removed"] == 20
    assert summary["created"] == 20

    kept = session.get(User, keep.id)
    assert kept is not None and kept.email == "real.person@customer.io"
    # the untouched user keeps its own wallet and no seeded rows point at it
    assert session.get(WalletAccount, keep.id).balance == 500
    assert session.scalar(select(func.count()).select_from(CoinLedgerEntry).where(CoinLedgerEntry.user_id == keep.id)) == 0
    assert session.scalar(select(func.count()).select_from(Order).where(Order.user_id == keep.id)) == 0
    assert session.scalar(select(func.count()).select_from(Subscription).where(Subscription.user_id == keep.id)) == 0
    assert sorted(session.scalars(select(User.email))) == sorted(demo_emails() + ["real.person@customer.io"])


def test_purge_is_noop_for_unknown_emails(session):
    seed_users(session)
    assert purge_users(session, ["nobody@example.com"]) == 0
    session.commit()
    assert session.scalar(select(func.count()).select_from(User)) == 20
