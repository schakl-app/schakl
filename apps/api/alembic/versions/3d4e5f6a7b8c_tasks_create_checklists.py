"""tasks_create_checklists

Named checklists per task, each with ordered check-off items. Org-scoped + RLS-forced.

Revision ID: 3d4e5f6a7b8c
Revises: 2c3d4e5f6a7b
Create Date: 2026-07-07 10:15:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from app.core.rls import disable_rls, enable_rls


# revision identifiers, used by Alembic.
revision: str = '3d4e5f6a7b8c'
down_revision: str | None = '2c3d4e5f6a7b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'task_checklists',
        sa.Column('task_id', sa.UUID(), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('position', sa.Integer(), server_default='0', nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_task_checklists_org_id_orgs'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], name=op.f('fk_task_checklists_task_id_tasks'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_task_checklists')),
    )
    op.create_index(op.f('ix_task_checklists_org_id'), 'task_checklists', ['org_id'], unique=False)
    op.create_index(op.f('ix_task_checklists_task_id'), 'task_checklists', ['task_id'], unique=False)

    op.create_table(
        'task_checklist_items',
        sa.Column('checklist_id', sa.UUID(), nullable=False),
        sa.Column('title', sa.String(length=512), nullable=False),
        sa.Column('done', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('position', sa.Integer(), server_default='0', nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['checklist_id'], ['task_checklists.id'], name=op.f('fk_task_checklist_items_checklist_id_task_checklists'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_task_checklist_items_org_id_orgs'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_task_checklist_items')),
    )
    op.create_index(op.f('ix_task_checklist_items_checklist_id'), 'task_checklist_items', ['checklist_id'], unique=False)
    op.create_index(op.f('ix_task_checklist_items_org_id'), 'task_checklist_items', ['org_id'], unique=False)

    enable_rls("task_checklists")
    enable_rls("task_checklist_items")


def downgrade() -> None:
    disable_rls("task_checklist_items")
    disable_rls("task_checklists")

    op.drop_index(op.f('ix_task_checklist_items_org_id'), table_name='task_checklist_items')
    op.drop_index(op.f('ix_task_checklist_items_checklist_id'), table_name='task_checklist_items')
    op.drop_table('task_checklist_items')
    op.drop_index(op.f('ix_task_checklists_task_id'), table_name='task_checklists')
    op.drop_index(op.f('ix_task_checklists_org_id'), table_name='task_checklists')
    op.drop_table('task_checklists')
