"""tasks_create_labels

Org-defined colored labels for tasks plus the task↔label link table. Both org-scoped and
RLS-forced (CLAUDE.md §5).

Revision ID: 2c3d4e5f6a7b
Revises: 1b2c3d4e5f6a
Create Date: 2026-07-07 10:10:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from app.core.rls import disable_rls, enable_rls


# revision identifiers, used by Alembic.
revision: str = '2c3d4e5f6a7b'
down_revision: str | None = '1b2c3d4e5f6a'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'task_labels',
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('color', sa.String(length=20), nullable=False),
        sa.Column('position', sa.Integer(), server_default='0', nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_task_labels_org_id_orgs'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_task_labels')),
        sa.UniqueConstraint('org_id', 'name', name=op.f('uq_task_labels_org_id')),
    )
    op.create_index(op.f('ix_task_labels_org_id'), 'task_labels', ['org_id'], unique=False)

    op.create_table(
        'task_label_links',
        sa.Column('task_id', sa.UUID(), nullable=False),
        sa.Column('label_id', sa.UUID(), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['label_id'], ['task_labels.id'], name=op.f('fk_task_label_links_label_id_task_labels'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_task_label_links_org_id_orgs'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], name=op.f('fk_task_label_links_task_id_tasks'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_task_label_links')),
        sa.UniqueConstraint('task_id', 'label_id', name=op.f('uq_task_label_links_task_id')),
    )
    op.create_index(op.f('ix_task_label_links_label_id'), 'task_label_links', ['label_id'], unique=False)
    op.create_index(op.f('ix_task_label_links_org_id'), 'task_label_links', ['org_id'], unique=False)
    op.create_index(op.f('ix_task_label_links_task_id'), 'task_label_links', ['task_id'], unique=False)

    enable_rls("task_labels")
    enable_rls("task_label_links")


def downgrade() -> None:
    disable_rls("task_label_links")
    disable_rls("task_labels")

    op.drop_index(op.f('ix_task_label_links_task_id'), table_name='task_label_links')
    op.drop_index(op.f('ix_task_label_links_org_id'), table_name='task_label_links')
    op.drop_index(op.f('ix_task_label_links_label_id'), table_name='task_label_links')
    op.drop_table('task_label_links')
    op.drop_index(op.f('ix_task_labels_org_id'), table_name='task_labels')
    op.drop_table('task_labels')
