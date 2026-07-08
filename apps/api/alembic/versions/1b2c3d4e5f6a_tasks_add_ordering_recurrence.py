"""tasks_add_ordering_recurrence

Adds manual ordering (``position``), completion tracking (``completed_at``) and simple
recurrence (``recurrence`` JSONB + a cron-queryable ``recurrence_next_run`` date) to tasks.
Existing rows get a stable initial order from their creation time.

Revision ID: 1b2c3d4e5f6a
Revises: 0a1b2c3d4e5f
Create Date: 2026-07-07 10:05:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '1b2c3d4e5f6a'
down_revision: str | None = '0a1b2c3d4e5f'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'tasks',
        sa.Column('position', sa.Double(), server_default='0', nullable=False),
    )
    op.add_column(
        'tasks',
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'tasks',
        sa.Column('recurrence', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        'tasks',
        sa.Column('recurrence_next_run', sa.Date(), nullable=True),
    )
    # Stable initial manual order: oldest first, matching creation time.
    op.execute("UPDATE tasks SET position = EXTRACT(EPOCH FROM created_at)")
    op.create_index(
        'ix_tasks_recurrence_next_run',
        'tasks',
        ['recurrence_next_run'],
        unique=False,
        postgresql_where=sa.text('recurrence_next_run IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index('ix_tasks_recurrence_next_run', table_name='tasks')
    op.drop_column('tasks', 'recurrence_next_run')
    op.drop_column('tasks', 'recurrence')
    op.drop_column('tasks', 'completed_at')
    op.drop_column('tasks', 'position')
