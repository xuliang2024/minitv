"""episode takedown: allow published <-> unpublished

Episodes gain a real takedown state so the admin can批量上架/下架 without
losing the uploaded video (``not_uploaded`` means "no video", which forced a
re-upload). The DB trigger that mirrors the Python state machine is updated
accordingly.

Revision ID: c4f7a9b2d6e1
Revises: 9c1b7f2a4d10
Create Date: 2026-10-14 12:05:00.000000
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = 'c4f7a9b2d6e1'
down_revision = '9c1b7f2a4d10'
branch_labels = None
depends_on = None


OLD_FUNCTION = """
        CREATE OR REPLACE FUNCTION minitv_assert_episode_status_transition() RETURNS trigger AS $$
        BEGIN
            IF NEW.status = OLD.status THEN
                RETURN NEW;
            END IF;
            IF NOT (
                   (OLD.status = 'not_uploaded' AND NEW.status = 'transcoding')
                OR (OLD.status = 'transcoding' AND NEW.status IN ('in_review', 'transcode_failed'))
                OR (OLD.status = 'transcode_failed' AND NEW.status = 'transcoding')
                OR (OLD.status = 'in_review' AND NEW.status IN ('published', 'not_uploaded'))
                OR (OLD.status = 'published' AND NEW.status = 'not_uploaded')
            ) THEN
                RAISE EXCEPTION 'illegal episode status transition: % -> %', OLD.status, NEW.status;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
"""

NEW_FUNCTION = """
        CREATE OR REPLACE FUNCTION minitv_assert_episode_status_transition() RETURNS trigger AS $$
        BEGIN
            IF NEW.status = OLD.status THEN
                RETURN NEW;
            END IF;
            IF NOT (
                   (OLD.status = 'not_uploaded' AND NEW.status = 'transcoding')
                OR (OLD.status = 'transcoding' AND NEW.status IN ('in_review', 'transcode_failed'))
                OR (OLD.status = 'transcode_failed' AND NEW.status = 'transcoding')
                OR (OLD.status = 'in_review' AND NEW.status IN ('published', 'not_uploaded'))
                OR (OLD.status = 'published' AND NEW.status IN ('unpublished', 'not_uploaded'))
                OR (OLD.status = 'unpublished' AND NEW.status IN ('published', 'not_uploaded'))
            ) THEN
                RAISE EXCEPTION 'illegal episode status transition: % -> %', OLD.status, NEW.status;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.execute(NEW_FUNCTION)


def downgrade() -> None:
    op.execute(
        """
        UPDATE episodes SET status = 'in_review' WHERE status = 'unpublished';
        """
    )
    op.execute(OLD_FUNCTION)
