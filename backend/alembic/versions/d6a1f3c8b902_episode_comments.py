"""comments belong to one episode

The product rule is one thread per episode. Existing rows (none in production)
are backfilled to their drama's first episode before the column becomes NOT NULL.

Revision ID: d6a1f3c8b902
Revises: b7e2c4d9a1f3
Create Date: 2026-10-19 15:40:00.000000
"""
from alembic import op

revision = "d6a1f3c8b902"
down_revision = "b7e2c4d9a1f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE comments AS c
        SET episode_id = (
            SELECT e.id FROM episodes AS e
            WHERE e.drama_id = c.drama_id
            ORDER BY e.episode_number, e.id
            LIMIT 1
        )
        WHERE c.episode_id IS NULL
        """
    )
    op.execute("DELETE FROM comments WHERE episode_id IS NULL")
    op.execute("ALTER TABLE comments ALTER COLUMN episode_id SET NOT NULL")

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_reactions_target "
        "ON reactions (target_type, target_id, kind)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_comments_user_created "
        "ON comments (user_id, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_comments_user_created")
    op.execute("DROP INDEX IF EXISTS ix_reactions_target")
    op.execute("ALTER TABLE comments ALTER COLUMN episode_id DROP NOT NULL")
