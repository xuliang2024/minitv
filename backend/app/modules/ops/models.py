"""Ops module: back-office staff accounts (stage 2).

Roles: ops (content writer), finance (read money flows only),
admin (everything). Staff accounts are separate from end-user accounts.

Accounts are never deleted: `status=disabled` keeps every audit trail and
ledger row pointing at a real staff row, while `resolve_session_token`
rejects the account on the very next request.
"""

from __future__ import annotations

import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

ROLE_OPS = "ops"
ROLE_FINANCE = "finance"
ROLE_ADMIN = "admin"
ROLES = (ROLE_OPS, ROLE_FINANCE, ROLE_ADMIN)
CONTENT_WRITER_ROLES = (ROLE_OPS, ROLE_ADMIN)

STATUS_ACTIVE = "active"
STATUS_DISABLED = "disabled"
STATUSES = (STATUS_ACTIVE, STATUS_DISABLED)


class AdminUser(Base):
    __tablename__ = "admin_users"
    __table_args__ = (
        CheckConstraint("role IN ('ops', 'finance', 'admin')", name="ck_admin_users_role"),
        CheckConstraint("status IN ('active', 'disabled')", name="ck_admin_users_status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    role: Mapped[str] = mapped_column(String(16), nullable=False, default=ROLE_OPS)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=STATUS_ACTIVE)
    # Why the account was switched off — shown in the staff list and kept in
    # audit_logs; cleared again when the account is re-enabled.
    disable_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Written on every successful login; NULL means "never signed in".
    last_login_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
