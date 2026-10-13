"""Ops module: back-office staff accounts (stage 2).

Roles: ops (content writer), finance (read money flows only),
admin (everything). Staff accounts are separate from end-user accounts.
"""

from __future__ import annotations

import datetime

from sqlalchemy import BigInteger, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

ROLE_OPS = "ops"
ROLE_FINANCE = "finance"
ROLE_ADMIN = "admin"
ROLES = (ROLE_OPS, ROLE_FINANCE, ROLE_ADMIN)
CONTENT_WRITER_ROLES = (ROLE_OPS, ROLE_ADMIN)


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    role: Mapped[str] = mapped_column(String(16), nullable=False, default=ROLE_OPS)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
