"""staff account lifecycle: last login, disable reason, value constraints

Back-office accounts are disabled instead of deleted, so the list needs to say
why an account is off (`disable_reason`) and when it was last used
(`last_login_at`). The two CHECK constraints close a hole where a typo in
`role` / `status` produced an account that could neither write content nor be
treated as an admin.

Revision ID: b7f2c9d41e03
Revises: e8c1f4a7b203
Create Date: 2026-10-21 10:20:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = "b7f2c9d41e03"
down_revision = "e8c1f4a7b203"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "admin_users", sa.Column("disable_reason", sa.String(length=200), nullable=True)
    )
    op.add_column(
        "admin_users",
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_admin_users_role", "admin_users", "role IN ('ops', 'finance', 'admin')"
    )
    op.create_check_constraint(
        "ck_admin_users_status", "admin_users", "status IN ('active', 'disabled')"
    )


def downgrade() -> None:
    op.drop_constraint("ck_admin_users_status", "admin_users", type_="check")
    op.drop_constraint("ck_admin_users_role", "admin_users", type_="check")
    op.drop_column("admin_users", "last_login_at")
    op.drop_column("admin_users", "disable_reason")
