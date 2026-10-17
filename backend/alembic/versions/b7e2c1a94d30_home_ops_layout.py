"""home ops layout: tab lock, banner copy, floor style

The home-ops editor needs three things the original tables did not store:
which tab is pinned (Discover), which group a tab opens, the two lines of
copy on a banner, and which layout a homepage floor uses.

Revision ID: b7e2c1a94d30
Revises: c4f7a9b2d6e1
Create Date: 2026-10-16 09:05:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "b7e2c1a94d30"
down_revision = "c4f7a9b2d6e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("channel_tabs", sa.Column("locked", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("channel_tabs", sa.Column("group_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key("fk_channel_tabs_group", "channel_tabs", "home_groups", ["group_id"], ["id"])

    op.add_column("banners", sa.Column("kicker", sa.String(length=80), server_default="", nullable=False))
    op.add_column("banners", sa.Column("headline", sa.String(length=80), server_default="", nullable=False))

    op.add_column("home_groups", sa.Column("style", sa.String(length=16), server_default="poster3", nullable=False))
    op.add_column("home_groups", sa.Column("badge", sa.String(length=16), server_default="rank", nullable=False))


def downgrade() -> None:
    op.drop_column("home_groups", "badge")
    op.drop_column("home_groups", "style")
    op.drop_column("banners", "headline")
    op.drop_column("banners", "kicker")
    op.drop_constraint("fk_channel_tabs_group", "channel_tabs", type_="foreignkey")
    op.drop_column("channel_tabs", "group_id")
    op.drop_column("channel_tabs", "locked")
