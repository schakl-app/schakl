"""google_calendar_create_tables

Revision ID: 2b43709c5d77
Revises: b6fb4cae8add
Create Date: 2026-07-12 00:00:00.000000

New google.calendar tables (issue #22): watch/sync state per connection, the local event
cache the Agenda reads, and the leave-push outbox. Expand-only: additive DDL, nothing else
references them, older code never reads them — rollback (downgrade drops all three + their
RLS policies) is safe from any released version.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.core.rls import disable_rls, enable_rls

# revision identifiers, used by Alembic.
revision: str = '2b43709c5d77'
down_revision: str | None = 'b6fb4cae8add'
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
        'google_calendar_channels',
        sa.Column('connection_id', sa.UUID(), nullable=False),
        sa.Column('calendar_id', sa.String(length=255), server_default='primary', nullable=False),
        sa.Column('channel_id', sa.String(length=64), nullable=True),
        sa.Column('resource_id', sa.String(length=128), nullable=True),
        sa.Column('channel_token', sa.String(length=255), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('watch_status', sa.String(length=16), server_default='none', nullable=False),
        sa.Column('sync_token', sa.String(length=512), nullable=True),
        sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
        *_base_columns(),
        sa.ForeignKeyConstraint(
            ['connection_id'], ['google_connections.id'],
            name=op.f('fk_google_calendar_channels_connection_id_google_connections'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['org_id'], ['orgs.id'],
            name=op.f('fk_google_calendar_channels_org_id_orgs'), ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_google_calendar_channels')),
        sa.UniqueConstraint('org_id', 'connection_id', name='uq_gcal_channels_org_connection'),
    )
    op.create_index(
        op.f('ix_google_calendar_channels_org_id'), 'google_calendar_channels', ['org_id']
    )
    op.create_index(
        op.f('ix_google_calendar_channels_connection_id'),
        'google_calendar_channels',
        ['connection_id'],
    )
    enable_rls('google_calendar_channels')

    op.create_table(
        'google_calendar_events',
        sa.Column('connection_id', sa.UUID(), nullable=False),
        sa.Column('google_event_id', sa.String(length=255), nullable=False),
        sa.Column('calendar_id', sa.String(length=255), server_default='primary', nullable=False),
        sa.Column('summary', sa.String(length=1000), nullable=True),
        sa.Column('status', sa.String(length=16), server_default='confirmed', nullable=False),
        sa.Column('html_link', sa.String(length=500), nullable=True),
        sa.Column('etag', sa.String(length=64), nullable=True),
        sa.Column('all_day', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('start_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('end_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('start_date', sa.Date(), nullable=True),
        sa.Column('end_date', sa.Date(), nullable=True),
        sa.Column('updated_at_google', sa.DateTime(timezone=True), nullable=True),
        *_base_columns(),
        sa.ForeignKeyConstraint(
            ['connection_id'], ['google_connections.id'],
            name=op.f('fk_google_calendar_events_connection_id_google_connections'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['org_id'], ['orgs.id'],
            name=op.f('fk_google_calendar_events_org_id_orgs'), ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_google_calendar_events')),
        sa.UniqueConstraint(
            'org_id', 'connection_id', 'google_event_id',
            name='uq_gcal_events_org_conn_event',
        ),
    )
    op.create_index(
        op.f('ix_google_calendar_events_org_id'), 'google_calendar_events', ['org_id']
    )
    op.create_index(
        op.f('ix_google_calendar_events_connection_id'),
        'google_calendar_events',
        ['connection_id'],
    )
    op.create_index(
        'ix_gcal_events_org_conn_start_at',
        'google_calendar_events',
        ['org_id', 'connection_id', 'start_at'],
    )
    op.create_index(
        'ix_gcal_events_org_conn_start_date',
        'google_calendar_events',
        ['org_id', 'connection_id', 'start_date'],
    )
    enable_rls('google_calendar_events')

    op.create_table(
        'calendar_event_links',
        sa.Column('local_type', sa.String(length=32), nullable=False),
        sa.Column('local_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=True),
        sa.Column('connection_id', sa.UUID(), nullable=True),
        sa.Column('calendar_id', sa.String(length=255), server_default='primary', nullable=False),
        sa.Column('google_event_id', sa.String(length=255), nullable=True),
        sa.Column('etag', sa.String(length=64), nullable=True),
        sa.Column('status', sa.String(length=20), server_default='pending', nullable=False),
        sa.Column(
            'payload', postgresql.JSONB(astext_type=sa.Text()),
            server_default='{}', nullable=False,
        ),
        sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
        sa.Column('last_error', sa.String(length=500), nullable=True),
        *_base_columns(),
        sa.ForeignKeyConstraint(
            ['user_id'], ['users.id'],
            name=op.f('fk_calendar_event_links_user_id_users'), ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['connection_id'], ['google_connections.id'],
            name=op.f('fk_calendar_event_links_connection_id_google_connections'),
            ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['org_id'], ['orgs.id'],
            name=op.f('fk_calendar_event_links_org_id_orgs'), ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_calendar_event_links')),
        sa.UniqueConstraint('org_id', 'local_type', 'local_id', name='uq_gcal_links_org_local'),
    )
    op.create_index(
        op.f('ix_calendar_event_links_org_id'), 'calendar_event_links', ['org_id']
    )
    op.create_index('ix_gcal_links_org_status', 'calendar_event_links', ['org_id', 'status'])
    enable_rls('calendar_event_links')


def downgrade() -> None:
    disable_rls('calendar_event_links')
    op.drop_index('ix_gcal_links_org_status', table_name='calendar_event_links')
    op.drop_index(op.f('ix_calendar_event_links_org_id'), table_name='calendar_event_links')
    op.drop_table('calendar_event_links')
    disable_rls('google_calendar_events')
    op.drop_index('ix_gcal_events_org_conn_start_date', table_name='google_calendar_events')
    op.drop_index('ix_gcal_events_org_conn_start_at', table_name='google_calendar_events')
    op.drop_index(
        op.f('ix_google_calendar_events_connection_id'), table_name='google_calendar_events'
    )
    op.drop_index(op.f('ix_google_calendar_events_org_id'), table_name='google_calendar_events')
    op.drop_table('google_calendar_events')
    disable_rls('google_calendar_channels')
    op.drop_index(
        op.f('ix_google_calendar_channels_connection_id'),
        table_name='google_calendar_channels',
    )
    op.drop_index(
        op.f('ix_google_calendar_channels_org_id'), table_name='google_calendar_channels'
    )
    op.drop_table('google_calendar_channels')
