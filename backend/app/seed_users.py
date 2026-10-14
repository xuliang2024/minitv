"""Seed mock end users for local development.

Usage:
    cd backend && ../.venv/bin/python -m app.seed_users
    cd backend && ../.venv/bin/python -m app.seed_users --count 20
    cd backend && ../.venv/bin/python -m app.seed_users --reset

Why a script instead of raw SQL: a user is never just one row. ADR-002 keeps a
materialised wallet balance next to a coin ledger, and a partial unique index
allows at most one active subscription per user. Seeded rows have to obey the
same bookkeeping as the real register / check-in / purchase flows, so the admin
console (`/admin/v1/users`), the finance page and the client profile screen all
show coherent data.

Guarantees:
* Idempotent - users are keyed by e-mail; re-running only creates missing rows.
* Deterministic - a fixed random seed means two runs produce the same profiles.
* Consistent - wallet.balance == sum(coin_ledger.amount) for every user; every
  user shown as a member has one active subscription and one paid order.
* Isolated - all demo accounts use the reserved `@example.com` domain, so
  `--reset` can remove exactly the seeded rows and nothing else.
"""

from __future__ import annotations

import argparse
import datetime as dt
import random
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import get_engine, make_session_factory
from app.modules.account.models import ThirdPartyIdentity, User
from app.modules.account.service import DEFAULT_AMOUNTS, DEFAULT_CHECKIN
from app.modules.billing.models import Order, Plan, PlanVersion, Subscription
from app.modules.wallet.models import CoinLedgerEntry, WalletAccount

DEMO_DOMAIN = "example.com"
SEED = 20261014
DEFAULT_COUNT = 20

FIRST_NAMES = (
    "Ava", "Liam", "Sophia", "Noah", "Mia",
    "Ethan", "Isabella", "Lucas", "Amelia", "Mason",
    "Harper", "Logan", "Evelyn", "Elijah", "Chloe",
    "James", "Layla", "Benjamin", "Zoe", "Daniel",
)

LAST_NAMES = (
    "Thompson", "Reyes", "Nguyen", "Patel", "Garcia",
    "Brooks", "Kim", "Foster", "Bennett", "Silva",
    "Hayes", "Rivera", "Coleman", "Sullivan", "Park",
    "Murphy", "Turner", "Alvarez", "Watson", "Ellis",
)

BIOS = (
    "Short drama addict. Here for the plot twists.",
    "Watching one episode before bed. Every night.",
    "Romance > everything.",
    "Collecting coins, unlocking endings.",
    "CEO by day, binge watcher by night.",
    "Subtitles on, always.",
    "Found this app last week. Already hooked.",
    "Revenge plots are my comfort food.",
    "Just here for the cliffhangers.",
    "Weekend marathoner.",
)

REGIONS = ("US", "US", "US", "US", "CA", "GB", "AU", "DE")
GENDERS = ("female", "male", "unspecified")

PLAN_CYCLE = (
    ("weekly", 7),
    ("monthly", 30),
    ("yearly", 365),
)

DEFAULT_PLAN_PRICES = {"weekly": 999, "monthly": 1999, "yearly": 7999}


def _profile(index: int) -> dict[str, Any]:
    """Build one deterministic demo profile (same output on every run)."""
    rng = random.Random(SEED + index)
    first = FIRST_NAMES[index % len(FIRST_NAMES)]
    last = LAST_NAMES[(index * 7 + 3) % len(LAST_NAMES)]
    local = f"{first.lower()}.{last.lower()}"
    if index >= len(FIRST_NAMES):
        local = f"{local}{index + 1}"
    return {
        "index": index,
        "display_name": f"{first} {last}",
        "email": f"{local}@{DEMO_DOMAIN}",
        "gender": GENDERS[rng.randrange(len(GENDERS))],
        "region": REGIONS[rng.randrange(len(REGIONS))],
        "locale": "en",
        "bio": BIOS[rng.randrange(len(BIOS))],
        "birthday": dt.date(1985 + rng.randrange(0, 20), 1 + rng.randrange(0, 12), 1 + rng.randrange(0, 28)),
        "following": rng.randrange(0, 60),
        "followers": rng.randrange(0, 900),
        "likes": rng.randrange(0, 4000),
        "age_days": rng.randrange(0, 90),
        "is_member": index % 3 == 0,
        "plan_code": PLAN_CYCLE[(index // 3) % len(PLAN_CYCLE)][0],
        "ad_rewards": rng.randrange(0, 5),
        "checkin_days": rng.randrange(0, 8),
        "binds_email": index % 4 == 0,
        "binds_facebook": index % 5 == 1,
        "clawback": index % 9 == 4,
    }


def _unique_public_ids(count: int) -> list[str]:
    """Deterministic, collision-free 10-digit public ids (same style as register())."""
    used: set[str] = set()
    out: list[str] = []
    index = 0
    while len(out) < count:
        candidate = str(random.Random(SEED + 100_000 + index).randrange(1_000_000_000, 9_999_999_999))
        index += 1
        if candidate in used:
            continue
        used.add(candidate)
        out.append(candidate)
    return out


def _ensure_plan(session: Session, code: str, *, name_en: str, price_cents: int) -> PlanVersion:
    """Return the current plan version, creating a default plan if the DB is empty."""
    plan = session.scalar(select(Plan).where(Plan.code == code))
    if plan is None:
        plan = Plan(code=code, name_en=name_en, name_zh=name_en, status="active")
        session.add(plan)
        session.flush()
    version = session.scalar(
        select(PlanVersion).where(PlanVersion.plan_id == plan.id).order_by(PlanVersion.version.desc())
    )
    if version is None:
        version = PlanVersion(plan_id=plan.id, version=1, price_cents=price_cents, currency="USD", note="seed default")
        session.add(version)
        session.flush()
    return version


def _ledger_events(profile: dict[str, Any], created_at: dt.datetime) -> list[tuple[str, int, str, dt.datetime]]:
    """(biz_type, amount, note, created_at) rows for one demo user.

    Income mirrors the real reward config so the ledger looks like it came from
    the production flows; the total is what the wallet balance materialises.
    """
    rng = random.Random(SEED + 5000 + profile["index"])
    events: list[tuple[str, int, str, dt.datetime]] = [
        (
            "register_reward",
            DEFAULT_AMOUNTS["register"][0],
            "welcome bonus",
            created_at,
        )
    ]
    if profile["binds_email"]:
        events.append(("bind_email", DEFAULT_AMOUNTS["bind_email"][0], "e-mail binding bonus", created_at + dt.timedelta(hours=2)))
    if profile["binds_facebook"]:
        events.append(
            ("bind_facebook", DEFAULT_AMOUNTS["bind_facebook"][0], "Facebook binding bonus", created_at + dt.timedelta(hours=3))
        )
    for day in range(profile["checkin_days"]):
        events.append(
            (
                "checkin",
                DEFAULT_CHECKIN[day % len(DEFAULT_CHECKIN)],
                f"daily check-in day {day + 1}",
                created_at + dt.timedelta(days=day + 1, hours=9),
            )
        )
    for n in range(profile["ad_rewards"]):
        events.append(
            (
                "ad_reward",
                DEFAULT_AMOUNTS["ad"][0],
                f"rewarded ad #{n + 1}",
                created_at + dt.timedelta(days=n + 1, hours=13 + n),
            )
        )
    if profile["clawback"]:
        events.append(
            (
                "adjustment",
                -20,
                "risk-control clawback",
                created_at + dt.timedelta(days=rng.randrange(1, 6), hours=20),
            )
        )
    return sorted(events, key=lambda row: row[3])


def _create_user(session: Session, profile: dict[str, Any], now: dt.datetime, public_id: str) -> None:
    created_at = now - dt.timedelta(days=profile["age_days"], hours=profile["index"] % 24, minutes=(profile["index"] * 7) % 60)
    user = User(
        email=profile["email"],
        display_name=profile["display_name"],
        avatar_url="",
        gender=profile["gender"],
        bio=profile["bio"],
        birthday=profile["birthday"],
        region=profile["region"],
        locale=profile["locale"],
        public_id=public_id,
        following_count=profile["following"],
        follower_count=profile["followers"],
        received_likes=profile["likes"],
        created_at=created_at,
    )
    session.add(user)
    session.flush()

    balance = 0
    for n, (biz_type, amount, note, happened_at) in enumerate(_ledger_events(profile, created_at)):
        balance += amount
        session.add(
            CoinLedgerEntry(
                user_id=user.id,
                amount=amount,
                balance_after=balance,
                biz_type=biz_type,
                biz_key=f"seed:{public_id}:{n}:{biz_type}",
                note=note,
                created_at=happened_at,
            )
        )
    session.add(WalletAccount(user_id=user.id, balance=balance))

    if profile["binds_facebook"]:
        session.add(
            ThirdPartyIdentity(user_id=user.id, provider="facebook", provider_user_id=f"fb_{public_id}")
        )
    if profile["index"] % 6 == 2:
        session.add(ThirdPartyIdentity(user_id=user.id, provider="google", provider_user_id=f"gg_{public_id}"))

    if profile["is_member"]:
        plan_code = profile["plan_code"]
        days = dict(PLAN_CYCLE)[plan_code]
        version = _ensure_plan(
            session,
            plan_code,
            name_en=plan_code.capitalize(),
            price_cents=DEFAULT_PLAN_PRICES[plan_code],
        )
        period_start = now - dt.timedelta(days=1 + profile["index"] % 5, hours=profile["index"] % 12)
        paid_at = period_start
        session.add(
            Order(
                order_no=f"MOCK{public_id}",
                user_id=user.id,
                plan_version_id=version.id,
                amount_cents=version.price_cents,
                currency=version.currency,
                status="paid",
                external_txn_id=f"mock_txn_{public_id}",
                paid_at=paid_at,
                created_at=paid_at,
            )
        )
        session.add(
            Subscription(
                user_id=user.id,
                plan_id=version.plan_id,
                plan_version_id=version.id,
                status="active",
                current_period_start=period_start,
                current_period_end=period_start + dt.timedelta(days=days),
                created_at=paid_at,
            )
        )


def purge_users(session: Session, emails: list[str]) -> int:
    """Delete the demo users with these e-mails plus every row the seeder wrote."""
    user_ids = list(session.scalars(select(User.id).where(User.email.in_(emails))))
    if not user_ids:
        return 0
    for model in (CoinLedgerEntry, WalletAccount, Subscription, Order, ThirdPartyIdentity):
        session.execute(delete(model).where(model.user_id.in_(user_ids)))
    session.execute(delete(User).where(User.id.in_(user_ids)))
    return len(user_ids)


def seed_users(
    session: Session,
    *,
    count: int = DEFAULT_COUNT,
    now: dt.datetime | None = None,
    reset: bool = False,
) -> dict[str, int]:
    """Create up to `count` demo users; existing e-mails are skipped."""
    if count < 1:
        raise ValueError("count must be >= 1")
    now = now or dt.datetime.now(dt.timezone.utc)
    profiles = [_profile(index) for index in range(count)]
    public_ids = _unique_public_ids(count)

    removed = 0
    if reset:
        removed = purge_users(session, [p["email"] for p in profiles])
        session.flush()

    created = skipped = 0
    for profile, public_id in zip(profiles, public_ids):
        exists = session.scalar(select(User.id).where(User.email == profile["email"]))
        if exists is not None:
            skipped += 1
            continue
        _create_user(session, profile, now, public_id)
        created += 1
    session.commit()
    return {"created": created, "skipped": skipped, "removed": removed, "requested": count}


def demo_emails(count: int = DEFAULT_COUNT) -> list[str]:
    """The deterministic e-mail list, for `--reset` and for tests."""
    return [_profile(index)["email"] for index in range(count)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed mock MiniTV users into the local database.")
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT, help=f"how many users to seed (default {DEFAULT_COUNT})")
    parser.add_argument("--reset", action="store_true", help="delete previously seeded demo users first")
    args = parser.parse_args()

    factory = make_session_factory(get_engine())
    with factory() as session:
        summary = seed_users(session, count=args.count, reset=args.reset)
    if summary["removed"]:
        print(f"reset: removed {summary['removed']} previously seeded users")
    print(f"created: {summary['created']}  skipped (already present): {summary['skipped']}")
    for email in demo_emails(args.count):
        print(f"  {email}")


if __name__ == "__main__":
    main()
