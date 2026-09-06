"""microsoft_create_tables

Revision ID: b7d3f9a2c4e6
Revises: a3c9e17f5b2d
Create Date: 2026-09-06 00:00:00.000000

New integration tables (docs/MICROSOFT.md): the per-org Microsoft 365 settings and the per-user
connection vault (refresh/access tokens encrypted at rest), the Outlook calendar cache, its
change-notification subscriptions and the push outbox, the OneDrive reference links and the
folder-provisioning outbox, and the Outlook suppression list and persisted skips. Expand-only:
additive DDL, nothing else references them, older code never reads them — rollback (downgrade
drops all nine + their RLS policies) is safe from any released version.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = 'b7d3f9a2c4e6'
down_revision: str | None = 'a3c9e17f5b2d'
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


def _org_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ['org_id'], ['orgs.id'], name=op.f(f'fk_{table}_org_id_orgs'), ondelete='CASCADE'
    )


def _connection_fk(table: str, ondelete: str = 'CASCADE') -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ['connection_id'], ['microsoft_connections.id'],
        name=op.f(f'fk_{table}_connection_id_microsoft_connections'),
        ondelete=ondelete,
    )


def _user_fk(table: str, column: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], ['users.id'], name=op.f(f'fk_{table}_{column}_users'), ondelete='SET NULL'
    )


def upgrade() -> None:
    # --- core ------------------------------------------------------------------------- #
    op.create_table(
        'microsoft_settings',
        sa.Column('client_id', sa.String(length=512), nullable=True),
        sa.Column('client_secret_encrypted', sa.Text(), nullable=True),
        sa.Column('tenant_id', sa.String(length=128), nullable=True),
        sa.Column(
            'calendar_enabled', sa.Boolean(), server_default=sa.text('false'), nullable=False
        ),
        sa.Column(
            'onedrive_enabled', sa.Boolean(), server_default=sa.text('false'), nullable=False
        ),
        sa.Column(
            'outlook_enabled', sa.Boolean(), server_default=sa.text('false'), nullable=False
        ),
        sa.Column('onedrive_drive_id', sa.String(length=256), nullable=True),
        sa.Column('onedrive_parent_folder_id', sa.String(length=256), nullable=True),
        sa.Column('onedrive_template_folder_id', sa.String(length=256), nullable=True),
        sa.Column(
            'onedrive_auto_provision', sa.Boolean(), server_default=sa.text('false'),
            nullable=False,
        ),
        sa.Column('automation_connection_user_id', sa.UUID(), nullable=True),
        sa.Column(
            'outlook_approval_mode', sa.String(length=20),
            server_default='approval_required', nullable=False,
        ),
        sa.Column(
            'outlook_thread_followup', sa.String(length=20),
            server_default='inherit_pending', nullable=False,
        ),
        sa.Column(
            'outlook_log_internal', sa.Boolean(), server_default=sa.text('false'),
            nullable=False,
        ),
        *_base_columns(),
        _user_fk('microsoft_settings', 'automation_connection_user_id'),
        _org_fk('microsoft_settings'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_microsoft_settings')),
        sa.UniqueConstraint('org_id', name='uq_microsoft_settings_org'),
    )
    op.create_index(op.f('ix_microsoft_settings_org_id'), 'microsoft_settings', ['org_id'])
    enable_rls('microsoft_settings')

    op.create_table(
        'microsoft_connections',
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('microsoft_oid', sa.String(length=64), nullable=False),
        sa.Column('tenant_id', sa.String(length=128), nullable=True),
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
            'outlook_sync_enabled', sa.Boolean(), server_default=sa.text('false'),
            nullable=False,
        ),
        sa.Column('outlook_excluded_category', sa.String(length=128), nullable=True),
        sa.Column('outlook_cursor_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('outlook_last_polled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('outlook_manual_poll_at', sa.DateTime(timezone=True), nullable=True),
        *_base_columns(),
        sa.ForeignKeyConstraint(
            ['user_id'], ['users.id'],
            name=op.f('fk_microsoft_connections_user_id_users'), ondelete='CASCADE',
        ),
        _org_fk('microsoft_connections'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_microsoft_connections')),
        sa.UniqueConstraint('org_id', 'user_id', name='uq_microsoft_connections_org_user'),
    )
    op.create_index(
        op.f('ix_microsoft_connections_org_id'), 'microsoft_connections', ['org_id']
    )
    op.create_index(
        op.f('ix_microsoft_connections_user_id'), 'microsoft_connections', ['user_id']
    )
    op.create_index(
        'ix_microsoft_connections_org_status', 'microsoft_connections', ['org_id', 'status']
    )
    enable_rls('microsoft_connections')

    # --- calendar --------------------------------------------------------------------- #
    op.create_table(
        'microsoft_calendar_channels',
        sa.Column('connection_id', sa.UUID(), nullable=False),
        sa.Column(
            'calendar_id', sa.String(length=512), server_default='primary', nullable=False
        ),
        sa.Column('summary', sa.String(length=255), server_default='', nullable=False),
        sa.Column('subscription_id', sa.String(length=128), nullable=True),
        sa.Column('client_state', sa.String(length=255), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('watch_status', sa.String(length=16), server_default='none', nullable=False),
        sa.Column('delta_link', sa.Text(), nullable=True),
        sa.Column('window_end', sa.Date(), nullable=True),
        sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
        *_base_columns(),
        _connection_fk('microsoft_calendar_channels'),
        _org_fk('microsoft_calendar_channels'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_microsoft_calendar_channels')),
        sa.UniqueConstraint(
            'org_id', 'connection_id', 'calendar_id',
            name='uq_mscal_channels_org_conn_calendar',
        ),
    )
    op.create_index(
        op.f('ix_microsoft_calendar_channels_org_id'), 'microsoft_calendar_channels', ['org_id']
    )
    op.create_index(
        op.f('ix_microsoft_calendar_channels_connection_id'),
        'microsoft_calendar_channels',
        ['connection_id'],
    )
    enable_rls('microsoft_calendar_channels')

    op.create_table(
        'microsoft_calendar_events',
        sa.Column('connection_id', sa.UUID(), nullable=False),
        sa.Column('graph_event_id', sa.String(length=512), nullable=False),
        sa.Column('series_master_id', sa.String(length=512), nullable=True),
        sa.Column(
            'calendar_id', sa.String(length=512), server_default='primary', nullable=False
        ),
        sa.Column('subject', sa.String(length=1000), nullable=True),
        sa.Column('status', sa.String(length=16), server_default='confirmed', nullable=False),
        sa.Column('web_link', sa.String(length=1000), nullable=True),
        sa.Column('change_key', sa.String(length=255), nullable=True),
        sa.Column('all_day', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('start_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('end_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('start_date', sa.Date(), nullable=True),
        sa.Column('end_date', sa.Date(), nullable=True),
        sa.Column('updated_at_graph', sa.DateTime(timezone=True), nullable=True),
        *_base_columns(),
        _connection_fk('microsoft_calendar_events'),
        _org_fk('microsoft_calendar_events'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_microsoft_calendar_events')),
        sa.UniqueConstraint(
            'org_id', 'connection_id', 'calendar_id', 'graph_event_id',
            name='uq_mscal_events_org_conn_cal_event',
        ),
    )
    op.create_index(
        op.f('ix_microsoft_calendar_events_org_id'), 'microsoft_calendar_events', ['org_id']
    )
    op.create_index(
        op.f('ix_microsoft_calendar_events_connection_id'),
        'microsoft_calendar_events',
        ['connection_id'],
    )
    op.create_index(
        'ix_mscal_events_org_conn_start_at',
        'microsoft_calendar_events',
        ['org_id', 'connection_id', 'start_at'],
    )
    op.create_index(
        'ix_mscal_events_org_conn_start_date',
        'microsoft_calendar_events',
        ['org_id', 'connection_id', 'start_date'],
    )
    enable_rls('microsoft_calendar_events')

    op.create_table(
        'microsoft_calendar_event_links',
        sa.Column('local_type', sa.String(length=32), nullable=False),
        sa.Column('local_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=True),
        sa.Column('connection_id', sa.UUID(), nullable=True),
        sa.Column(
            'calendar_id', sa.String(length=512), server_default='primary', nullable=False
        ),
        sa.Column('graph_event_id', sa.String(length=512), nullable=True),
        sa.Column('change_key', sa.String(length=255), nullable=True),
        sa.Column('status', sa.String(length=20), server_default='pending', nullable=False),
        sa.Column(
            'payload', postgresql.JSONB(astext_type=sa.Text()), server_default='{}',
            nullable=False,
        ),
        sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
        sa.Column('last_error', sa.String(length=500), nullable=True),
        *_base_columns(),
        _user_fk('microsoft_calendar_event_links', 'user_id'),
        _connection_fk('microsoft_calendar_event_links', ondelete='SET NULL'),
        _org_fk('microsoft_calendar_event_links'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_microsoft_calendar_event_links')),
        sa.UniqueConstraint(
            'org_id', 'local_type', 'local_id', name='uq_mscal_links_org_local'
        ),
    )
    op.create_index(
        op.f('ix_microsoft_calendar_event_links_org_id'),
        'microsoft_calendar_event_links',
        ['org_id'],
    )
    op.create_index(
        'ix_mscal_links_org_status', 'microsoft_calendar_event_links', ['org_id', 'status']
    )
    enable_rls('microsoft_calendar_event_links')

    # --- onedrive --------------------------------------------------------------------- #
    op.create_table(
        'onedrive_links',
        sa.Column('entity_type', sa.String(length=32), nullable=False),
        sa.Column('entity_id', sa.UUID(), nullable=False),
        sa.Column('drive_id', sa.String(length=256), nullable=False),
        sa.Column('item_id', sa.String(length=256), nullable=False),
        sa.Column('web_url', sa.String(length=1000), nullable=False),
        sa.Column('name', sa.String(length=500), nullable=False),
        sa.Column('mime_type', sa.String(length=255), nullable=True),
        sa.Column('is_folder', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('is_root', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('created_by_user_id', sa.UUID(), nullable=True),
        sa.Column('created_by_name', sa.String(length=255), nullable=True),
        *_base_columns(),
        _user_fk('onedrive_links', 'created_by_user_id'),
        _org_fk('onedrive_links'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_onedrive_links')),
        sa.UniqueConstraint(
            'org_id', 'entity_type', 'entity_id', 'drive_id', 'item_id',
            name='uq_onedrive_links_org_entity_item',
        ),
    )
    op.create_index(op.f('ix_onedrive_links_org_id'), 'onedrive_links', ['org_id'])
    op.create_index(
        'ix_onedrive_links_org_entity', 'onedrive_links', ['org_id', 'entity_type', 'entity_id']
    )
    op.create_index(
        'ix_onedrive_links_org_item', 'onedrive_links', ['org_id', 'drive_id', 'item_id']
    )
    op.create_index(
        'uq_onedrive_links_org_entity_root',
        'onedrive_links',
        ['org_id', 'entity_type', 'entity_id'],
        unique=True,
        postgresql_where=sa.text('is_root'),
    )
    enable_rls('onedrive_links')

    op.create_table(
        'onedrive_folder_jobs',
        sa.Column('entity_type', sa.String(length=32), nullable=False),
        sa.Column('entity_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=500), nullable=False),
        sa.Column('parent_entity_id', sa.UUID(), nullable=True),
        sa.Column('parent_entity_type', sa.String(length=32), nullable=True),
        sa.Column('status', sa.String(length=16), server_default='pending', nullable=False),
        sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
        sa.Column('last_error', sa.String(length=500), nullable=True),
        *_base_columns(),
        _org_fk('onedrive_folder_jobs'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_onedrive_folder_jobs')),
        sa.UniqueConstraint(
            'org_id', 'entity_type', 'entity_id', name='uq_onedrive_folder_jobs_org_entity'
        ),
    )
    op.create_index(op.f('ix_onedrive_folder_jobs_org_id'), 'onedrive_folder_jobs', ['org_id'])
    op.create_index(
        'ix_onedrive_folder_jobs_org_status', 'onedrive_folder_jobs', ['org_id', 'status']
    )
    enable_rls('onedrive_folder_jobs')

    # --- outlook ---------------------------------------------------------------------- #
    op.create_table(
        'outlook_suppressions',
        sa.Column('connection_id', sa.UUID(), nullable=False),
        sa.Column('message_id', sa.String(length=512), nullable=True),
        sa.Column('conversation_id', sa.String(length=512), nullable=True),
        *_base_columns(),
        _connection_fk('outlook_suppressions'),
        _org_fk('outlook_suppressions'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_outlook_suppressions')),
    )
    op.create_index(op.f('ix_outlook_suppressions_org_id'), 'outlook_suppressions', ['org_id'])
    op.create_index(
        op.f('ix_outlook_suppressions_connection_id'), 'outlook_suppressions', ['connection_id']
    )
    op.create_index(
        'uq_outlook_suppressions_org_conn_message',
        'outlook_suppressions',
        ['org_id', 'connection_id', 'message_id'],
        unique=True,
        postgresql_where=sa.text('message_id IS NOT NULL'),
    )
    op.create_index(
        'ix_outlook_suppressions_org_conn_conversation',
        'outlook_suppressions',
        ['org_id', 'connection_id', 'conversation_id'],
    )
    enable_rls('outlook_suppressions')

    op.create_table(
        'outlook_skips',
        sa.Column('connection_id', sa.UUID(), nullable=False),
        sa.Column('message_id', sa.String(length=512), nullable=False),
        sa.Column('conversation_id', sa.String(length=512), nullable=True),
        sa.Column('reason', sa.String(length=32), nullable=False),
        sa.Column('detail', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        *_base_columns(),
        _connection_fk('outlook_skips'),
        _org_fk('outlook_skips'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_outlook_skips')),
    )
    op.create_index(op.f('ix_outlook_skips_org_id'), 'outlook_skips', ['org_id'])
    op.create_index(op.f('ix_outlook_skips_connection_id'), 'outlook_skips', ['connection_id'])
    op.create_index(
        'uq_outlook_skips_org_conn_message',
        'outlook_skips',
        ['org_id', 'connection_id', 'message_id'],
        unique=True,
    )
    op.create_index('ix_outlook_skips_org_created', 'outlook_skips', ['org_id', 'created_at'])
    enable_rls('outlook_skips')


def downgrade() -> None:
    for table in (
        'outlook_skips',
        'outlook_suppressions',
        'onedrive_folder_jobs',
        'onedrive_links',
        'microsoft_calendar_event_links',
        'microsoft_calendar_events',
        'microsoft_calendar_channels',
        'microsoft_connections',
        'microsoft_settings',
    ):
        disable_rls(table)
        op.drop_table(table)
