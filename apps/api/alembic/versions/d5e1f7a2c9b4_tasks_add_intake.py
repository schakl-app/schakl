"""tasks_add_intake

Revision ID: d5e1f7a2c9b4
Revises: c4d8e2f6a1b3
Create Date: 2026-09-11 10:00:00.000000

The e-mail intake address (``taak@bureau.nl``): org-wide tasks settings (one row per org, absent
= the defaults, so an instance that upgrades and types nothing has no intake and behaves exactly
as before) and the receipt/parked-queue table the connected-mailbox feeds write through the
``app.core.mailbox.intake`` seam. Expand-only and rollback-safe: two new tables nothing older
reads.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = 'd5e1f7a2c9b4'
down_revision: str | None = 'c4d8e2f6a1b3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'task_settings',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('intake_address', sa.String(length=320), nullable=True),
        sa.Column(
            'intake_default_due_days', sa.Integer(), server_default='1', nullable=False
        ),
        sa.Column('intake_last_received_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('intake_received_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['org_id'], ['orgs.id'], name=op.f('fk_task_settings_org_id_orgs'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_task_settings')),
        sa.UniqueConstraint('org_id', name='uq_task_settings_org'),
    )
    op.create_index(op.f('ix_task_settings_org_id'), 'task_settings', ['org_id'])
    enable_rls('task_settings')

    op.create_table(
        'task_intake_messages',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('rfc822_message_id', sa.String(length=512), nullable=True),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('provider_message_id', sa.String(length=512), nullable=False),
        sa.Column('provider_thread_id', sa.String(length=512), nullable=True),
        sa.Column('sender_user_id', sa.UUID(), nullable=True),
        sa.Column('sender_email', sa.String(length=320), nullable=False),
        sa.Column('sender_name', sa.String(length=255), nullable=True),
        sa.Column('subject', sa.String(length=500), nullable=True),
        sa.Column('body_text', sa.Text(), nullable=True),
        sa.Column('body_markdown', sa.Text(), nullable=True),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('reason', sa.String(length=50), nullable=True),
        sa.Column('task_id', sa.UUID(), nullable=True),
        sa.Column(
            'hints', postgresql.JSONB(astext_type=sa.Text()), server_default='{}',
            nullable=False,
        ),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['org_id'], ['orgs.id'], name=op.f('fk_task_intake_messages_org_id_orgs'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['sender_user_id'], ['users.id'],
            name=op.f('fk_task_intake_messages_sender_user_id_users'), ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['task_id'], ['tasks.id'], name=op.f('fk_task_intake_messages_task_id_tasks'),
            ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_task_intake_messages')),
    )
    op.create_index(
        op.f('ix_task_intake_messages_org_id'), 'task_intake_messages', ['org_id']
    )
    op.create_index(
        op.f('ix_task_intake_messages_status'), 'task_intake_messages', ['status']
    )
    op.create_index(
        'ix_task_intake_messages_org_sender_status',
        'task_intake_messages',
        ['org_id', 'sender_user_id', 'status'],
    )
    op.create_index(
        'uq_task_intake_messages_rfc822',
        'task_intake_messages',
        ['org_id', 'rfc822_message_id'],
        unique=True,
        postgresql_where=sa.text('rfc822_message_id IS NOT NULL'),
    )
    op.create_index(
        'uq_task_intake_messages_provider',
        'task_intake_messages',
        ['org_id', 'source', 'provider_message_id'],
        unique=True,
    )
    enable_rls('task_intake_messages')


def downgrade() -> None:
    disable_rls('task_intake_messages')
    op.drop_index('uq_task_intake_messages_provider', table_name='task_intake_messages')
    op.drop_index('uq_task_intake_messages_rfc822', table_name='task_intake_messages')
    op.drop_index(
        'ix_task_intake_messages_org_sender_status', table_name='task_intake_messages'
    )
    op.drop_index(op.f('ix_task_intake_messages_status'), table_name='task_intake_messages')
    op.drop_index(op.f('ix_task_intake_messages_org_id'), table_name='task_intake_messages')
    op.drop_table('task_intake_messages')
    disable_rls('task_settings')
    op.drop_index(op.f('ix_task_settings_org_id'), table_name='task_settings')
    op.drop_table('task_settings')
