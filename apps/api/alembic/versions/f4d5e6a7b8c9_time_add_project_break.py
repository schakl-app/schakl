"""time_add_project_break

Links a time entry to a project and adds a break/pause field so worked ``minutes`` can be
derived from start/end minus breaks (CLAUDE.md §6, §10). SET NULL keeps the entry if the project
is deleted.

Revision ID: f4d5e6a7b8c9
Revises: e3c4d5f6a1b2
Create Date: 2026-07-07 14:10:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f4d5e6a7b8c9'
down_revision: str | None = 'e3c4d5f6a1b2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('time_entries', sa.Column('project_id', sa.UUID(), nullable=True))
    op.add_column(
        'time_entries',
        sa.Column('break_minutes', sa.Integer(), server_default='0', nullable=False),
    )
    op.create_index(op.f('ix_time_entries_project_id'), 'time_entries', ['project_id'], unique=False)
    op.create_foreign_key(
        op.f('fk_time_entries_project_id_projects'),
        'time_entries',
        'projects',
        ['project_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint(op.f('fk_time_entries_project_id_projects'), 'time_entries', type_='foreignkey')
    op.drop_index(op.f('ix_time_entries_project_id'), table_name='time_entries')
    op.drop_column('time_entries', 'break_minutes')
    op.drop_column('time_entries', 'project_id')
