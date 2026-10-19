"""stripe ids on subscriptions

The local subscription row has to be reachable from Stripe webhooks, which
only send the Stripe subscription and customer ids. Both are nullable so the
fake-payment path and historical rows keep working unchanged.

Revision ID: f2b8c71d5a90
Revises: a1d9e4c72b60
Create Date: 2026-10-19 11:05:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "f2b8c71d5a90"
down_revision = "a1d9e4c72b60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("subscriptions", sa.Column("stripe_customer_id", sa.String(length=64), nullable=True))
    op.add_column("subscriptions", sa.Column("stripe_subscription_id", sa.String(length=64), nullable=True))
    op.add_column("subscriptions", sa.Column("stripe_price_id", sa.String(length=64), nullable=True))
    op.create_index(
        "uq_subscriptions_stripe_id",
        "subscriptions",
        ["stripe_subscription_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_subscriptions_stripe_id", table_name="subscriptions")
    op.drop_column("subscriptions", "stripe_price_id")
    op.drop_column("subscriptions", "stripe_subscription_id")
    op.drop_column("subscriptions", "stripe_customer_id")
