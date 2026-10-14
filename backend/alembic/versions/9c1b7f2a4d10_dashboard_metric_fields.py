"""dashboard metric fields

Extends daily_metrics so the admin dashboard can report every aggregate it
shows (plays, completions, paid orders, unlocks) from real events instead of
only a four-number subset.

Revision ID: 9c1b7f2a4d10
Revises: e9f043ff49af
Create Date: 2026-10-14 10:40:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '9c1b7f2a4d10'
down_revision = 'e9f043ff49af'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('daily_metrics', sa.Column('plays', sa.Integer(), server_default='0', nullable=False))
    op.add_column('daily_metrics', sa.Column('completes', sa.Integer(), server_default='0', nullable=False))
    op.add_column('daily_metrics', sa.Column('paid_orders', sa.Integer(), server_default='0', nullable=False))
    op.add_column('daily_metrics', sa.Column('unlock_count', sa.Integer(), server_default='0', nullable=False))
    op.add_column(
        'daily_metrics',
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )


def downgrade() -> None:
    op.drop_column('daily_metrics', 'updated_at')
    op.drop_column('daily_metrics', 'unlock_count')
    op.drop_column('daily_metrics', 'paid_orders')
    op.drop_column('daily_metrics', 'completes')
    op.drop_column('daily_metrics', 'plays')
