"""async moderation queue columns

Visibility (`status`) and job state (`moderation_state`) are deliberately
separate: a comment can stay pending for the public while its AI verdict is
still running, and a human decision can cancel a late worker write.

Revision ID: e8c1f4a7b203
Revises: d6a1f3c8b902
Create Date: 2026-10-19 16:12:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = "e8c1f4a7b203"
down_revision = "d6a1f3c8b902"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("comments", sa.Column("moderation_state", sa.String(length=16), nullable=True))
    op.add_column(
        "comments",
        sa.Column(
            "moderation_attempts",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "comments",
        sa.Column("moderation_claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("comments", sa.Column("moderation_error", sa.String(length=200), nullable=True))
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_comments_moderation_queue "
        "ON comments (id) WHERE moderation_state IN ('queued', 'running')"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_comments_moderation_queue")
    op.drop_column("comments", "moderation_error")
    op.drop_column("comments", "moderation_claimed_at")
    op.drop_column("comments", "moderation_attempts")
    op.drop_column("comments", "moderation_state")
