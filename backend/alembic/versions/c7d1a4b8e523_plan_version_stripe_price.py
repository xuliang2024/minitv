"""stripe price per plan version

Price edits used to need a matching object created in the Stripe dashboard.
Store the Stripe price id on the version instead, so the backend can create it
on demand and the admin "会员套餐" page becomes the only place a price changes.

Revision ID: c7d1a4b8e523
Revises: f2b8c71d5a90
Create Date: 2026-10-19 11:40:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "c7d1a4b8e523"
down_revision = "f2b8c71d5a90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("plan_versions", sa.Column("stripe_price_id", sa.String(length=64), nullable=True))
    op.create_index(
        "uq_plan_versions_stripe_price",
        "plan_versions",
        ["stripe_price_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_plan_versions_stripe_price", table_name="plan_versions")
    op.drop_column("plan_versions", "stripe_price_id")
