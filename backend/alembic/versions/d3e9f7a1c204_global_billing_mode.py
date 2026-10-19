"""global billing mode + one-time passes

One switch decides whether *new* purchases are auto-renewing subscriptions or
one-time passes. Existing subscriptions keep renewing (product decision), so the
mode is a global setting rather than a per-plan attribute.

Revision ID: d3e9f7a1c204
Revises: c7d1a4b8e523
Create Date: 2026-10-19 11:50:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "d3e9f7a1c204"
down_revision = "c7d1a4b8e523"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "billing_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("billing_mode", sa.String(length=16), nullable=False, server_default="subscription"),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute("INSERT INTO billing_settings (id, billing_mode) VALUES (1, 'subscription')")

    op.add_column("plan_versions", sa.Column("stripe_one_time_price_id", sa.String(length=64), nullable=True))
    op.create_index(
        "uq_plan_versions_stripe_one_time_price",
        "plan_versions",
        ["stripe_one_time_price_id"],
        unique=True,
    )

    op.add_column(
        "subscriptions",
        sa.Column("billing_mode", sa.String(length=16), nullable=False, server_default="subscription"),
    )
    op.add_column("subscriptions", sa.Column("pass_end", sa.DateTime(timezone=True), nullable=True))

    op.add_column(
        "orders",
        sa.Column("billing_mode", sa.String(length=16), nullable=False, server_default="subscription"),
    )


def downgrade() -> None:
    op.drop_column("orders", "billing_mode")
    op.drop_column("subscriptions", "pass_end")
    op.drop_column("subscriptions", "billing_mode")
    op.drop_index("uq_plan_versions_stripe_one_time_price", table_name="plan_versions")
    op.drop_column("plan_versions", "stripe_one_time_price_id")
    op.drop_table("billing_settings")
