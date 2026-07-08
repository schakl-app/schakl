"""tasks_add_project_id

Links a task to a project (a project's to-do list). SET NULL keeps the task if the project is
deleted. CLAUDE.md §6, §10.

Revision ID: e3c4d5f6a1b2
Revises: d2b3c4e5f6a1
Create Date: 2026-07-07 13:41:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e3c4d5f6a1b2'
down_revision: str | None = 'd2b3c4e5f6a1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('tasks', sa.Column('project_id', sa.UUID(), nullable=True))
    op.create_index(op.f('ix_tasks_project_id'), 'tasks', ['project_id'], unique=False)
    op.create_foreign_key(
        op.f('fk_tasks_project_id_projects'),
        'tasks',
        'projects',
        ['project_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint(op.f('fk_tasks_project_id_projects'), 'tasks', type_='foreignkey')
    op.drop_index(op.f('ix_tasks_project_id'), table_name='tasks')
    op.drop_column('tasks', 'project_id')
