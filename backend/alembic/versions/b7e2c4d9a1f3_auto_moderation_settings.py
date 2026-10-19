"""auto moderation settings and comment verdict columns

Adds the single-row policy table the admin page edits, plus the per-comment
audit trail (verdict / score / reason / model / timestamp). The API key stays
in the environment; nothing secret is stored here.

Revision ID: b7e2c4d9a1f3
Revises: f4c8d2a9b316
Create Date: 2026-10-19 15:15:00.000000
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b7e2c4d9a1f3"
down_revision = "f4c8d2a9b316"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "moderation_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "base_url",
            sa.String(length=200),
            nullable=False,
            server_default="https://api.openai.com/v1",
        ),
        sa.Column("model", sa.String(length=64), nullable=False, server_default="gpt-4o-mini"),
        sa.Column("categories", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("system_prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("approve_threshold", sa.Float(), nullable=False, server_default=sa.text("0.75")),
        sa.Column("block_threshold", sa.Float(), nullable=False, server_default=sa.text("0.75")),
        sa.Column("fallback_action", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default=sa.text("6")),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.add_column("comments", sa.Column("moderation_verdict", sa.String(length=16), nullable=True))
    op.add_column("comments", sa.Column("moderation_score", sa.Float(), nullable=True))
    op.add_column("comments", sa.Column("moderation_reason", sa.Text(), nullable=True))
    op.add_column("comments", sa.Column("moderation_model", sa.String(length=64), nullable=True))
    op.add_column("comments", sa.Column("moderated_at", sa.DateTime(timezone=True), nullable=True))

    # Episode-scoped reads are the product rule; the index makes the list query
    # and the per-episode count a single index scan.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_comments_episode_status_created "
        "ON comments (episode_id, status, created_at DESC, id DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_comments_episode_status_created")
    op.drop_column("comments", "moderated_at")
    op.drop_column("comments", "moderation_model")
    op.drop_column("comments", "moderation_reason")
    op.drop_column("comments", "moderation_score")
    op.drop_column("comments", "moderation_verdict")
    op.drop_table("moderation_settings")
