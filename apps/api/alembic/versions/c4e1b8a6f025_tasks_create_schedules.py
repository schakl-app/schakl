"""tasks_create_schedules

Revision ID: c4e1b8a6f025
Revises: 3b2d80af13c5
Create Date: 2026-07-14 00:00:00.000000

Task scheduling (#188): planned time blocks for a task on someone's calendar. Expand-only,
additive DDL — nothing existing references ``task_schedules``, older code never reads it, so a
rollback (downgrade drops the table + its RLS policy) is safe from any released version. No
change to existing tables: the link to a time entry lives on this new table, not on ``tasks``
or ``time_entries``.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = 'c4e1b8a6f025'
down_revision: str | None = '3b2d80af13c5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'task_schedules',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('task_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=True),
        sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ends_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('note', sa.String(length=500), nullable=True),
        sa.Column('time_entry_id', sa.UUID(), nullable=True),
        sa.Column('created_by_user_id', sa.UUID(), nullable=True),
        sa.Column('created_by_name', sa.String(length=255), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['org_id'], ['orgs.id'], name=op.f('fk_task_schedules_org_id_orgs'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['task_id'], ['tasks.id'], name=op.f('fk_task_schedules_task_id_tasks'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['user_id'], ['users.id'], name=op.f('fk_task_schedules_user_id_users'),
            ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['time_entry_id'], ['time_entries.id'],
            name=op.f('fk_task_schedules_time_entry_id_time_entries'), ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['created_by_user_id'], ['users.id'],
            name=op.f('fk_task_schedules_created_by_user_id_users'), ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_task_schedules')),
    )
    op.create_index(op.f('ix_task_schedules_org_id'), 'task_schedules', ['org_id'])
    op.create_index(op.f('ix_task_schedules_task_id'), 'task_schedules', ['task_id'])
    op.create_index(op.f('ix_task_schedules_user_id'), 'task_schedules', ['user_id'])
    op.create_index(
        'ix_task_schedules_org_user_start', 'task_schedules', ['org_id', 'user_id', 'starts_at']
    )
    op.create_index('ix_task_schedules_org_task', 'task_schedules', ['org_id', 'task_id'])
    enable_rls('task_schedules')


def downgrade() -> None:
    disable_rls('task_schedules')
    op.drop_index('ix_task_schedules_org_task', table_name='task_schedules')
    op.drop_index('ix_task_schedules_org_user_start', table_name='task_schedules')
    op.drop_index(op.f('ix_task_schedules_user_id'), table_name='task_schedules')
    op.drop_index(op.f('ix_task_schedules_task_id'), table_name='task_schedules')
    op.drop_index(op.f('ix_task_schedules_org_id'), table_name='task_schedules')
    op.drop_table('task_schedules')
