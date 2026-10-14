"""Pytest fixtures: a dedicated minitv_test database provisioned by Alembic.

The test database is created from scratch via `alembic upgrade head`, so the
tests always exercise the real migration (tables, constraints, triggers).
Every test starts from an empty database via TRUNCATE ... RESTART IDENTITY.
"""

from __future__ import annotations

import datetime
import itertools
import os
import pathlib
import sys

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

BACKEND = pathlib.Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

# Tests never talk to the real Bunny APIs, regardless of what the local .env
# contains. Set before any app module import so load_env()'s setdefault cannot
# turn real mode on.
os.environ["BUNNY_MODE"] = "fake"
# Tests must not send real email. The sender is also unset, so a missed mock
# fails closed instead of calling Resend.
os.environ["RESEND_API_KEY"] = ""
os.environ["RESEND_FROM"] = ""

from app import modules  # noqa: E402,F401  registers every ORM model
from app.db import Base, load_env  # noqa: E402

TEST_DB_NAME = os.environ.get("MINITV_TEST_DB", "minitv_test")


def _admin_url() -> str:
    load_env()
    return os.environ.get(
        "DATABASE_URL", "postgresql://minitv:minitv_local@127.0.0.1:15432/minitv"
    )


def _test_url(admin: str) -> str:
    return admin.rsplit("/", 1)[0] + "/" + TEST_DB_NAME


@pytest.fixture(scope="session")
def engine():
    admin = _admin_url()
    admin_engine = create_engine(admin, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {TEST_DB_NAME} WITH (FORCE)"))
        conn.execute(text(f"CREATE DATABASE {TEST_DB_NAME}"))
    admin_engine.dispose()

    from alembic import command
    from alembic.config import Config

    url = _test_url(admin)
    # env.py prefers ALEMBIC_DATABASE_URL over the .env DATABASE_URL, and the
    # TestClient's lazy engine follows DATABASE_URL -> both point at the test db.
    os.environ["ALEMBIC_DATABASE_URL"] = url
    os.environ["DATABASE_URL"] = url
    base_redis = os.environ.get("REDIS_URL", "redis://:minitv_local@127.0.0.1:16379/0")
    redis_db = os.environ.get("MINITV_TEST_REDIS_DB", "15")
    os.environ["REDIS_URL"] = base_redis.rsplit("/", 1)[0] + f"/{redis_db}"  # dedicated test db
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    os.environ.pop("ALEMBIC_DATABASE_URL", None)

    eng = create_engine(url, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture()
def session(engine):
    table_names = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"))
    from app.modules.ops import service as ops_service

    ops_service.reset_redis_for_tests()
    ops_service.get_redis().flushdb()  # db 15 is dedicated to tests
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    s = factory()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def make(session):
    """Tiny data factory for stage-1 tests."""

    from app.modules.account.models import User
    from app.modules.billing.models import Plan, PlanVersion, Subscription
    from app.modules.content.models import Drama, Episode
    from app.modules.wallet.models import WalletAccount

    seq = itertools.count(1)

    class Factory:
        def user(self, email: str = "u@example.com", coins: int = 0) -> User:
            user = User(email=email, display_name=email.split("@")[0])
            session.add(user)
            session.flush()
            session.add(WalletAccount(user_id=user.id, balance=coins))
            session.commit()
            return user

        def drama(
            self,
            title: str = "Drama",
            status: str = "draft",
            price: int = 60,
            free: int = 3,
        ) -> Drama:
            drama = Drama(
                title=title,
                status=status,
                coin_price_per_episode=price,
                free_episode_count=free,
            )
            session.add(drama)
            session.commit()
            return drama

        def episode(self, drama: Drama, number: int = 1, status: str = "not_uploaded") -> Episode:
            episode = Episode(drama_id=drama.id, episode_number=number, status=status)
            session.add(episode)
            session.commit()
            return episode

        def subscription(self, user: User, active: bool = True) -> Subscription:
            n = next(seq)
            plan = Plan(code=f"plan-{user.id}-{n}", name_en=f"Plan {n}")
            session.add(plan)
            session.flush()
            version = PlanVersion(plan_id=plan.id, version=1, price_cents=1999)
            session.add(version)
            session.flush()
            now = datetime.datetime.now(datetime.timezone.utc)
            sub = Subscription(
                user_id=user.id,
                plan_id=plan.id,
                plan_version_id=version.id,
                status="active" if active else "canceled",
                current_period_start=now,
                current_period_end=now + datetime.timedelta(days=30 if active else -1),
            )
            session.add(sub)
            session.commit()
            return sub

        def admin(
            self,
            role: str = "ops",
            username: str | None = None,
            password: str = "pass-1234",
        ):
            from app.modules.ops.models import AdminUser
            from app.modules.ops.service import hash_password

            n = next(seq)
            user = AdminUser(
                username=username or f"admin-{role}-{n}",
                password_hash=hash_password(password),
                role=role,
                display_name=f"staff-{role}-{n}",
            )
            session.add(user)
            session.commit()
            return user

    return Factory()
