"""tasks_create_templates

Task templates (e.g. a client-onboarding checklist) and their items. A template is either
applied manually to a company or auto-instantiated when a company enters its trigger status.
Org-scoped + RLS-forced.

Revision ID: 5f6a7b8c9d0e
Revises: 4e5f6a7b8c9d
Create Date: 2026-07-07 10:25:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.core.rls import disable_rls, enable_rls


# revision identifiers, used by Alembic.
revision: str = '5f6a7b8c9d0e'
down_revision: str | None = '4e5f6a7b8c9d'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'task_templates',
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('trigger', sa.String(length=20), server_default='manual', nullable=False),
        sa.Column('trigger_status', sa.String(length=20), nullable=True),
        sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_task_templates_org_id_orgs'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_task_templates')),
    )
    op.create_index(op.f('ix_task_templates_org_id'), 'task_templates', ['org_id'], unique=False)

    op.create_table(
        'task_template_items',
        sa.Column('template_id', sa.UUID(), nullable=False),
        sa.Column('title', sa.String(length=512), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('priority', sa.String(length=20), server_default='normal', nullable=False),
        sa.Column('relative_due_days', sa.Integer(), nullable=True),
        sa.Column('assignee_user_id', sa.UUID(), nullable=True),
        sa.Column('position', sa.Integer(), server_default='0', nullable=False),
        sa.Column('checklist_title', sa.String(length=255), nullable=True),
        sa.Column('checklist_items', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['assignee_user_id'], ['users.id'], name=op.f('fk_task_template_items_assignee_user_id_users'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], name=op.f('fk_task_template_items_org_id_orgs'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['template_id'], ['task_templates.id'], name=op.f('fk_task_template_items_template_id_task_templates'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_task_template_items')),
    )
    op.create_index(op.f('ix_task_template_items_org_id'), 'task_template_items', ['org_id'], unique=False)
    op.create_index(op.f('ix_task_template_items_template_id'), 'task_template_items', ['template_id'], unique=False)

    enable_rls("task_templates")
    enable_rls("task_template_items")


def downgrade() -> None:
    disable_rls("task_template_items")
    disable_rls("task_templates")

    op.drop_index(op.f('ix_task_template_items_template_id'), table_name='task_template_items')
    op.drop_index(op.f('ix_task_template_items_org_id'), table_name='task_template_items')
    op.drop_table('task_template_items')
    op.drop_index(op.f('ix_task_templates_org_id'), table_name='task_templates')
    op.drop_table('task_templates')
