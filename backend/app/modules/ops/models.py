"""Ops module: back-office staff accounts (stage 2).

Roles: ops (content writer), finance (read money flows only),
admin (everything). Staff accounts are separate from end-user accounts.

Accounts are never deleted: `status=disabled` keeps every audit trail and
ledger row pointing at a real staff row, while `resolve_session_token`
rejects the account on the very next request.
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
    SmallInteger,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
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


API_KEY_STATUSES = (STATUS_ACTIVE, "revoked")


class AdminApiKey(Base):
    """Long-lived credential bound to one staff account.

    The key never carries its own role: ``require_admin`` re-reads the bound
    ``admin_users`` row on every request, so disabling the account or changing
    its role takes effect immediately. ``scopes`` can only narrow that role,
    never widen it (see docs/admin-api-key-remediation.md).

    Only ``key_hash`` is stored; the plaintext exists solely in the response
    that created or rotated the key.
    """

    __tablename__ = "admin_api_keys"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'revoked')", name="ck_admin_api_keys_status"
        ),
        Index("uq_admin_api_keys_prefix", "key_prefix", unique=True),
        # Partial unique index: a revoked key releases its name, so rotating
        # can reuse it and operators never see "name already taken".
        Index(
            "uq_admin_api_keys_user_name_active",
            "admin_user_id",
            "name",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index("ix_admin_api_keys_user_status", "admin_user_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    admin_user_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    # "mk_live_" + 8 hex chars. Displayed in the list, used as the lookup hit.
    key_prefix: Mapped[str] = mapped_column(String(24), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Empty list = follow the account role. Never wider than that role.
    scopes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=STATUS_ACTIVE)
    expires_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_used_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("admin_users.id"), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    revoked_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_by: Mapped[int | None] = mapped_column(
        ForeignKey("admin_users.id"), nullable=True
    )
    revoke_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)


class ApiKeyEvent(Base):
    """One row per write request made with an API key.

    audit_logs already records *who* acted; this table answers "which key did
    it", which is the part a key-bound principal previously lost. GETs are
    deliberately not recorded — they would dominate the table.
    """

    __tablename__ = "api_key_events"
    __table_args__ = (
        Index("ix_api_key_events_key_created", "key_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    key_id: Mapped[int] = mapped_column(
        ForeignKey("admin_api_keys.id", ondelete="CASCADE"), nullable=False
    )
    staff_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    method: Mapped[str] = mapped_column(String(8), nullable=False)
    path: Mapped[str] = mapped_column(String(200), nullable=False)
    status_code: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
