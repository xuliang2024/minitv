"""Seed default staff accounts for local development.

Usage:
    cd backend && ../.venv/bin/python -m app.seed_admin
"""

from __future__ import annotations

from app.db import get_engine, make_session_factory
from app.modules.ops.models import AdminUser, ROLE_ADMIN, ROLE_FINANCE, ROLE_OPS
from app.modules.ops.service import hash_password

DEFAULT_ACCOUNTS = (
    ("ops", "ops-1234", ROLE_OPS, "运营示例"),
    ("finance", "finance-1234", ROLE_FINANCE, "财务示例"),
    ("admin", "admin-1234", ROLE_ADMIN, "管理员"),
)


def seed() -> None:
    factory = make_session_factory(get_engine())
    with factory() as db:
        for username, password, role, display_name in DEFAULT_ACCOUNTS:
            existing = db.query(AdminUser).filter(AdminUser.username == username).one_or_none()
            if existing is not None:
                print(f"skip: {username} already exists (role={existing.role})")
                continue
            db.add(
                AdminUser(
                    username=username,
                    password_hash=hash_password(password),
                    role=role,
                    display_name=display_name,
                )
            )
            print(f"created: {username} (role={role}, password={password})")
        db.commit()


if __name__ == "__main__":
    seed()
