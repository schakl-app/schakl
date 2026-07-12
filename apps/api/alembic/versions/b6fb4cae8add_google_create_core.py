"""google_create_core

Revision ID: b6fb4cae8add
Revises: a23a7d369f7a
Create Date: 2026-07-12 00:00:00.000000

New module tables (issue #22, google core): per-org Workspace settings and the per-user
connection vault (refresh/access tokens encrypted at rest). Expand-only: additive DDL, nothing
else references them, older code never reads them — rollback (downgrade drops both + their RLS
policies) is safe from any released version.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = 'b6fb4cae8add'
down_revision: str | None = 'a23a7d369f7a'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _base_columns() -> list[sa.Column]:
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        'google_settings',
        sa.Column('client_id', sa.String(length=512), nullable=True),
        sa.Column('client_secret_encrypted', sa.Text(), nullable=True),
        sa.Column(
            'calendar_enabled', sa.Boolean(), server_default=sa.text('false'), nullable=False
        ),
        sa.Column(
            'drive_enabled', sa.Boolean(), server_default=sa.text('false'), nullable=False
        ),
        sa.Column(
            'gmail_enabled', sa.Boolean(), server_default=sa.text('false'), nullable=False
        ),
        sa.Column('drive_shared_drive_id', sa.String(length=128), nullable=True),
        sa.Column('drive_parent_folder_id', sa.String(length=128), nullable=True),
        sa.Column('drive_template_folder_id', sa.String(length=128), nullable=True),
        sa.Column(
            'drive_auto_provision', sa.Boolean(), server_default=sa.text('false'),
            nullable=False,
        ),
        sa.Column('automation_connection_user_id', sa.UUID(), nullable=True),
        sa.Column(
            'gmail_approval_mode', sa.String(length=20),
            server_default='approval_required', nullable=False,
        ),
        sa.Column(
            'gmail_thread_followup', sa.String(length=20),
            server_default='inherit_pending', nullable=False,
        ),
        *_base_columns(),
        sa.ForeignKeyConstraint(
            ['automation_connection_user_id'], ['users.id'],
            name=op.f('fk_google_settings_automation_connection_user_id_users'),
            ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['org_id'], ['orgs.id'], name=op.f('fk_google_settings_org_id_orgs'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_google_settings')),
        sa.UniqueConstraint('org_id', name='uq_google_settings_org'),
    )
    op.create_index(op.f('ix_google_settings_org_id'), 'google_settings', ['org_id'])
    enable_rls('google_settings')

    op.create_table(
        'google_connections',
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('google_sub', sa.String(length=64), nullable=False),
        sa.Column('email', sa.String(length=320), nullable=False),
        sa.Column(
            'scopes', postgresql.ARRAY(sa.String()), server_default='{}', nullable=False
        ),
        sa.Column('refresh_token_encrypted', sa.Text(), nullable=False),
        sa.Column('access_token_encrypted', sa.Text(), nullable=True),
        sa.Column('access_token_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status', sa.String(length=16), server_default='active', nullable=False),
        sa.Column('last_error', sa.String(length=500), nullable=True),
        sa.Column('error_since', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error_notified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'gmail_sync_enabled', sa.Boolean(), server_default=sa.text('false'),
            nullable=False,
        ),
        sa.Column('gmail_excluded_label', sa.String(length=128), nullable=True),
        sa.Column('gmail_history_id', sa.String(length=32), nullable=True),
        sa.Column('gmail_last_polled_at', sa.DateTime(timezone=True), nullable=True),
        *_base_columns(),
        sa.ForeignKeyConstraint(
            ['user_id'], ['users.id'],
            name=op.f('fk_google_connections_user_id_users'), ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['org_id'], ['orgs.id'], name=op.f('fk_google_connections_org_id_orgs'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_google_connections')),
        sa.UniqueConstraint('org_id', 'user_id', name='uq_google_connections_org_user'),
    )
    op.create_index(op.f('ix_google_connections_org_id'), 'google_connections', ['org_id'])
    op.create_index(op.f('ix_google_connections_user_id'), 'google_connections', ['user_id'])
    op.create_index(
        'ix_google_connections_org_status', 'google_connections', ['org_id', 'status']
    )
    enable_rls('google_connections')


def downgrade() -> None:
    disable_rls('google_connections')
    op.drop_index('ix_google_connections_org_status', table_name='google_connections')
    op.drop_index(op.f('ix_google_connections_user_id'), table_name='google_connections')
    op.drop_index(op.f('ix_google_connections_org_id'), table_name='google_connections')
    op.drop_table('google_connections')
    disable_rls('google_settings')
    op.drop_index(op.f('ix_google_settings_org_id'), table_name='google_settings')
    op.drop_table('google_settings')
