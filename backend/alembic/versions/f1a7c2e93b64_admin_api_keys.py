"""API keys bound to staff accounts + per-key write events

Long-lived machine credentials for automation. A key is bound to one
`admin_users` row, so the effective role is always the account's current role;
`scopes` can only narrow it. Only the sha256 of the plaintext is stored.

`api_key_events` answers "which key performed this write" — audit_logs already
answers "which person". GETs are not recorded.

Revision ID: f1a7c2e93b64
Revises: b7f2c9d41e03
Create Date: 2026-10-21 10:50:00.000000
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f1a7c2e93b64"
down_revision = "b7f2c9d41e03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_api_keys",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("admin_user_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("key_prefix", sa.String(length=24), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "scopes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "status", sa.String(length=16), server_default="active", nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.BigInteger(), nullable=True),
        sa.Column("revoke_reason", sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(["admin_user_id"], ["admin_users.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["admin_users.id"]),
        sa.ForeignKeyConstraint(["revoked_by"], ["admin_users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')", name="ck_admin_api_keys_status"
        ),
    )
    op.create_index(
        "uq_admin_api_keys_prefix", "admin_api_keys", ["key_prefix"], unique=True
    )
    op.create_index(
        "uq_admin_api_keys_user_name_active",
        "admin_api_keys",
        ["admin_user_id", "name"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "ix_admin_api_keys_user_status",
        "admin_api_keys",
        ["admin_user_id", "status"],
    )

    op.create_table(
        "api_key_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("key_id", sa.BigInteger(), nullable=False),
        sa.Column("staff_id", sa.BigInteger(), nullable=False),
        sa.Column("method", sa.String(length=8), nullable=False),
        sa.Column("path", sa.String(length=200), nullable=False),
        sa.Column("status_code", sa.SmallInteger(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["key_id"], ["admin_api_keys.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_api_key_events_key_created", "api_key_events", ["key_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_api_key_events_key_created", table_name="api_key_events")
    op.drop_table("api_key_events")
    op.drop_index("ix_admin_api_keys_user_status", table_name="admin_api_keys")
    op.drop_index(
        "uq_admin_api_keys_user_name_active", table_name="admin_api_keys"
    )
    op.drop_index("uq_admin_api_keys_prefix", table_name="admin_api_keys")
    op.drop_table("admin_api_keys")
