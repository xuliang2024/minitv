"""daily task claims: like_5 / favorite_5 / watch_8

One row per user, task and America/Los_Angeles calendar day. The unique key is
the payout guard; the API replays instead of paying twice when it is hit.

Revision ID: a1d9e4c72b60
Revises: b7e2c1a94d30
Create Date: 2026-10-19 10:05:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "a1d9e4c72b60"
down_revision = "b7e2c1a94d30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_claims",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("period_key", sa.String(length=16), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "code", "period_key", name="uq_task_claim_user_code_day"),
    )


def downgrade() -> None:
    op.drop_table("task_claims")
