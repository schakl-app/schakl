"""tasks_create_checklist_templates

Org-wide repository of reusable checklists (title + item titles) that can be attached to any
task card. Org-scoped + RLS-forced.

Revision ID: 7b8c9d0e1f2a
Revises: 6a7b8c9d0e1f
Create Date: 2026-07-07 16:10:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.core.rls import disable_rls, enable_rls


# revision identifiers, used by Alembic.
revision: str = '7b8c9d0e1f2a'
down_revision: str | None = '6a7b8c9d0e1f'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'task_checklist_templates',
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('items', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_task_checklist_templates_org_id_orgs'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_task_checklist_templates')),
    )
    op.create_index(op.f('ix_task_checklist_templates_org_id'), 'task_checklist_templates', ['org_id'], unique=False)

    enable_rls("task_checklist_templates")


def downgrade() -> None:
    disable_rls("task_checklist_templates")

    op.drop_index(op.f('ix_task_checklist_templates_org_id'), table_name='task_checklist_templates')
    op.drop_table('task_checklist_templates')
