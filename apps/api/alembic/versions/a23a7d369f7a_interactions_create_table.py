"""interactions_create_table

Revision ID: a23a7d369f7a
Revises: b3f8a2c91e47
Create Date: 2026-07-12 00:00:00.000000

New module table (issue #22): contactmomenten — manual meetings/calls/notes plus gmail-fed
email logs. Expand-only: additive DDL, nothing else references it, older code never reads it —
rollback (downgrade drops the table + its RLS policy) is safe from any released version.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = 'a23a7d369f7a'
down_revision: str | None = 'b3f8a2c91e47'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'interactions',
        sa.Column('kind', sa.String(length=10), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('subject', sa.String(length=500), nullable=True),
        sa.Column('snippet', sa.Text(), nullable=True),
        sa.Column('body_text', sa.Text(), nullable=True),
        sa.Column('direction', sa.String(length=10), nullable=False),
        sa.Column('company_id', sa.UUID(), nullable=True),
        sa.Column('project_id', sa.UUID(), nullable=True),
        sa.Column('task_id', sa.UUID(), nullable=True),
        sa.Column('contact_id', sa.UUID(), nullable=True),
        sa.Column('owner_user_id', sa.UUID(), nullable=True),
        sa.Column('owner_name', sa.String(length=255), nullable=True),
        sa.Column(
            'participants', postgresql.JSONB(astext_type=sa.Text()),
            server_default='[]', nullable=False,
        ),
        sa.Column('source', sa.String(length=10), nullable=False),
        sa.Column('gmail_message_id', sa.String(length=64), nullable=True),
        sa.Column('gmail_thread_id', sa.String(length=64), nullable=True),
        sa.Column('rfc822_message_id', sa.String(length=512), nullable=True),
        sa.Column('deep_link', sa.String(length=500), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['company_id'], ['companies.id'],
            name=op.f('fk_interactions_company_id_companies'), ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['project_id'], ['projects.id'],
            name=op.f('fk_interactions_project_id_projects'), ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['task_id'], ['tasks.id'],
            name=op.f('fk_interactions_task_id_tasks'), ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['contact_id'], ['contacts.id'],
            name=op.f('fk_interactions_contact_id_contacts'), ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['owner_user_id'], ['users.id'],
            name=op.f('fk_interactions_owner_user_id_users'), ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['org_id'], ['orgs.id'], name=op.f('fk_interactions_org_id_orgs'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_interactions')),
    )
    op.create_index(op.f('ix_interactions_org_id'), 'interactions', ['org_id'])
    op.create_index(op.f('ix_interactions_company_id'), 'interactions', ['company_id'])
    op.create_index(op.f('ix_interactions_project_id'), 'interactions', ['project_id'])
    op.create_index(op.f('ix_interactions_task_id'), 'interactions', ['task_id'])
    op.create_index(op.f('ix_interactions_contact_id'), 'interactions', ['contact_id'])
    op.create_index(op.f('ix_interactions_owner_user_id'), 'interactions', ['owner_user_id'])
    op.create_index('ix_interactions_org_occurred', 'interactions', ['org_id', 'occurred_at'])
    op.create_index('ix_interactions_org_status', 'interactions', ['org_id', 'status'])
    op.create_index(
        'uq_interactions_org_owner_gmail_message',
        'interactions',
        ['org_id', 'owner_user_id', 'gmail_message_id'],
        unique=True,
        postgresql_where=sa.text('gmail_message_id IS NOT NULL'),
    )
    op.create_index(
        'ix_interactions_org_rfc822', 'interactions', ['org_id', 'rfc822_message_id']
    )
    op.create_index('ix_interactions_org_thread', 'interactions', ['org_id', 'gmail_thread_id'])
    enable_rls('interactions')


def downgrade() -> None:
    disable_rls('interactions')
    op.drop_index('ix_interactions_org_thread', table_name='interactions')
    op.drop_index('ix_interactions_org_rfc822', table_name='interactions')
    op.drop_index('uq_interactions_org_owner_gmail_message', table_name='interactions')
    op.drop_index('ix_interactions_org_status', table_name='interactions')
    op.drop_index('ix_interactions_org_occurred', table_name='interactions')
    op.drop_index(op.f('ix_interactions_owner_user_id'), table_name='interactions')
    op.drop_index(op.f('ix_interactions_contact_id'), table_name='interactions')
    op.drop_index(op.f('ix_interactions_task_id'), table_name='interactions')
    op.drop_index(op.f('ix_interactions_project_id'), table_name='interactions')
    op.drop_index(op.f('ix_interactions_company_id'), table_name='interactions')
    op.drop_index(op.f('ix_interactions_org_id'), table_name='interactions')
    op.drop_table('interactions')
