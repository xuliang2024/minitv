"""default billing mode is one-time

The product now sells passes by default; auto-renew has to be switched on
explicitly. Column default and the single settings row move together.

Revision ID: f4c8d2a9b316
Revises: d3e9f7a1c204
Create Date: 2026-10-19 12:00:00.000000
"""
from alembic import op

revision = "f4c8d2a9b316"
down_revision = "d3e9f7a1c204"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("billing_settings", "billing_mode", server_default="one_time")
    op.execute("UPDATE billing_settings SET billing_mode = 'one_time' WHERE id = 1")


def downgrade() -> None:
    op.alter_column("billing_settings", "billing_mode", server_default="subscription")
    op.execute("UPDATE billing_settings SET billing_mode = 'subscription' WHERE id = 1")
