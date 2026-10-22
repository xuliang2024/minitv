"""moderation api key editable from admin

Revision ID: a3c9f1e28b70
Revises: e5b2a9c741d3
Create Date: 2026-10-22 20:30:00.000000

The key used to live only in OPENAI_API_KEY. It is now editable in the admin
page, so the singleton row carries a Fernet-encrypted copy; an empty value
still falls back to the environment variable.
"""
import sqlalchemy as sa
from alembic import op

revision = "a3c9f1e28b70"
down_revision = "e5b2a9c741d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "moderation_settings",
        sa.Column("api_key_secret", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("moderation_settings", "api_key_secret")
