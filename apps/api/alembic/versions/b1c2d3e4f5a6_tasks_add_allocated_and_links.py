"""tasks_add_allocated_and_links

Allocated time (budget in minutes) on tasks and template items, plus URL attachments
(``task_links``). Org-scoped + RLS-forced.

Revision ID: b1c2d3e4f5a6
Revises: a0b1c2d3e4f5
Create Date: 2026-07-08 18:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from app.core.rls import disable_rls, enable_rls


# revision identifiers, used by Alembic.
revision: str = 'b1c2d3e4f5a6'
down_revision: str | None = 'a0b1c2d3e4f5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('tasks', sa.Column('allocated_minutes', sa.Integer(), nullable=True))
    op.add_column(
        'task_template_items', sa.Column('allocated_minutes', sa.Integer(), nullable=True)
    )

    op.create_table(
        'task_links',
        sa.Column('task_id', sa.UUID(), nullable=False),
        sa.Column('url', sa.String(length=1024), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_task_links_org_id_orgs'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], name=op.f('fk_task_links_task_id_tasks'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_task_links')),
    )
    op.create_index(op.f('ix_task_links_org_id'), 'task_links', ['org_id'], unique=False)
    op.create_index(op.f('ix_task_links_task_id'), 'task_links', ['task_id'], unique=False)

    enable_rls("task_links")


def downgrade() -> None:
    disable_rls("task_links")

    op.drop_index(op.f('ix_task_links_task_id'), table_name='task_links')
    op.drop_index(op.f('ix_task_links_org_id'), table_name='task_links')
    op.drop_table('task_links')
    op.drop_column('task_template_items', 'allocated_minutes')
    op.drop_column('tasks', 'allocated_minutes')
