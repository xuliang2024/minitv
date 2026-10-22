"""supplier monthly settlements

Revision ID: e5b2a9c741d3
Revises: d3f8b1c6a940
Create Date: 2026-10-22 11:15:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = "e5b2a9c741d3"
down_revision = "d3f8b1c6a940"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "supplier_settlements",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("supplier_id", sa.BigInteger(), sa.ForeignKey("suppliers.id"), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="USD"),
        sa.Column("gross_cents", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("revenue_cents", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("effective_share_bps", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("order_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("note", sa.String(length=500), nullable=False, server_default=""),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('draft', 'confirmed', 'paid')", name="ck_settlements_status"
        ),
        sa.UniqueConstraint("supplier_id", "period_start", name="uq_settlement_period"),
    )
    op.create_index(
        "ix_settlements_period", "supplier_settlements", ["period_start", "status"]
    )


def downgrade() -> None:
    op.drop_index("ix_settlements_period", table_name="supplier_settlements")
    op.drop_table("supplier_settlements")
