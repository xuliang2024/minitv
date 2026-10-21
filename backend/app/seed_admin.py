"""Bootstrap the first back-office account.

Usage:
    cd backend && ../.venv/bin/python -m app.seed_admin

`scripts/deploy_server.sh` runs this on every deploy, so it must be safe to
re-run: it only acts when `admin_users` is empty. That also means deleting or
disabling an account can no longer be undone by the next deployment — the old
behaviour recreated the default `admin/admin-1234` row forever.

The initial password comes from `SEED_ADMIN_PASSWORD`. When that is unset a
random one is generated and printed once, so a fresh environment never starts
with a guessable credential.
"""

from __future__ import annotations

import os
import secrets

from sqlalchemy import func, select

from app.db import get_engine, make_session_factory
from app.modules.ops.models import ROLE_ADMIN, STATUS_ACTIVE, AdminUser
from app.modules.ops.service import hash_password


def seed() -> None:
    factory = make_session_factory(get_engine())
    with factory() as db:
        existing = db.scalar(select(func.count(AdminUser.id))) or 0
        if existing:
            print(f"skip: admin_users already has {existing} account(s)")
            return

        username = (os.environ.get("SEED_ADMIN_USERNAME") or "admin").strip().lower() or "admin"
        password = os.environ.get("SEED_ADMIN_PASSWORD") or ""
        generated = not password
        if generated:
            password = secrets.token_urlsafe(18)

        db.add(
            AdminUser(
                username=username,
                password_hash=hash_password(password),
                role=ROLE_ADMIN,
                status=STATUS_ACTIVE,
                display_name="管理员",
            )
        )
        db.commit()
        print(f"created: {username} (role=admin)")
        if generated:
            print(f"initial password (shown once): {password}")


if __name__ == "__main__":
    seed()
