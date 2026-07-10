"""notifications_create_tables

In-app notifications (CLAUDE.md §6, issue #16): a recipient-independent event log that also
feeds per-record activity, a per-recipient inbox (the in-app channel), watch/mute rows, the
per-user × event × channel preference matrix, and the external-delivery seam for issue #17.

All five tables are org-scoped and RLS-forced. The migration also enables the ``notifications``
module in every existing org's ``org_settings.enabled_modules`` (RLS is FORCED, so the GUC is
bound per org — same pattern as c8d9e0f1a2b3 / 9d0e1f2a3b4c).

Upgrade path: purely additive — new tables + an append to an existing array column. A rolled-
back image simply ignores the new tables and the extra ``enabled_modules`` entry. No backfill
of existing rows, no destructive change, safe to run unattended on a populated database.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-07-10 09:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.core.rls import disable_rls, enable_rls


# revision identifiers, used by Alembic.
revision: str = 'a7b8c9d0e1f2'
down_revision: str | None = 'f6a7b8c9d0e1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (
    "notification_deliveries",
    "notifications",
    "notification_watchers",
    "notification_preferences",
    "notification_events",
)


def upgrade() -> None:
    # --- notification_events -------------------------------------------------------------- #
    op.create_table(
        'notification_events',
        sa.Column('event_type', sa.String(length=50), nullable=False),
        sa.Column('entity_type', sa.String(length=30), nullable=False),
        sa.Column('entity_id', sa.UUID(), nullable=False),
        sa.Column('actor_user_id', sa.UUID(), nullable=True),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()),
                  server_default='{}', nullable=False),
        sa.Column('dedup_key', sa.String(length=120), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'],
                                name=op.f('fk_notification_events_actor_user_id_users'),
                                ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'],
                                name=op.f('fk_notification_events_org_id_orgs'),
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_notification_events')),
    )
    op.create_index(op.f('ix_notification_events_event_type'), 'notification_events',
                    ['event_type'], unique=False)
    op.create_index(op.f('ix_notification_events_org_id'), 'notification_events',
                    ['org_id'], unique=False)
    op.create_index('ix_notification_events_entity', 'notification_events',
                    ['org_id', 'entity_type', 'entity_id', 'created_at'], unique=False)
    op.create_index('uq_notification_events_dedup', 'notification_events',
                    ['org_id', 'dedup_key'], unique=True,
                    postgresql_where=sa.text('dedup_key IS NOT NULL'))

    # --- notification_events depends on nothing else; events → notifications FK next ------- #
    op.create_table(
        'notifications',
        sa.Column('event_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('visible_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.ForeignKeyConstraint(['event_id'], ['notification_events.id'],
                                name=op.f('fk_notifications_event_id_notification_events'),
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'],
                                name=op.f('fk_notifications_org_id_orgs'),
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'],
                                name=op.f('fk_notifications_user_id_users'),
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_notifications')),
    )
    op.create_index(op.f('ix_notifications_event_id'), 'notifications',
                    ['event_id'], unique=False)
    op.create_index(op.f('ix_notifications_org_id'), 'notifications', ['org_id'], unique=False)
    op.create_index(op.f('ix_notifications_user_id'), 'notifications', ['user_id'], unique=False)
    op.create_index('ix_notifications_user', 'notifications',
                    ['org_id', 'user_id', 'created_at'], unique=False)
    op.create_index('ix_notifications_unread', 'notifications',
                    ['org_id', 'user_id', 'visible_at'], unique=False,
                    postgresql_where=sa.text('read_at IS NULL'))

    # --- notification_watchers ------------------------------------------------------------ #
    op.create_table(
        'notification_watchers',
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('entity_type', sa.String(length=30), nullable=False),
        sa.Column('entity_id', sa.UUID(), nullable=False),
        sa.Column('muted', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'],
                                name=op.f('fk_notification_watchers_org_id_orgs'),
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'],
                                name=op.f('fk_notification_watchers_user_id_users'),
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_notification_watchers')),
        sa.UniqueConstraint('org_id', 'user_id', 'entity_type', 'entity_id',
                            name='uq_notification_watchers_org_id'),
    )
    op.create_index(op.f('ix_notification_watchers_org_id'), 'notification_watchers',
                    ['org_id'], unique=False)
    op.create_index(op.f('ix_notification_watchers_user_id'), 'notification_watchers',
                    ['user_id'], unique=False)
    op.create_index('ix_notification_watchers_entity', 'notification_watchers',
                    ['org_id', 'entity_type', 'entity_id'], unique=False)

    # --- notification_preferences --------------------------------------------------------- #
    op.create_table(
        'notification_preferences',
        sa.Column('user_id', sa.UUID(), nullable=True),
        sa.Column('event_type', sa.String(length=50), nullable=True),
        sa.Column('channel', sa.String(length=20), server_default='in_app', nullable=False),
        sa.Column('enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('delay_minutes', sa.Integer(), server_default='0', nullable=False),
        sa.Column('digest', sa.String(length=10), server_default='immediate', nullable=False),
        sa.Column('digest_time', sa.Time(), nullable=True),
        sa.Column('digest_weekday', sa.Integer(), nullable=True),
        sa.Column('quiet_hours_start', sa.Time(), nullable=True),
        sa.Column('quiet_hours_end', sa.Time(), nullable=True),
        sa.Column('due_soon_days', sa.Integer(), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'],
                                name=op.f('fk_notification_preferences_org_id_orgs'),
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'],
                                name=op.f('fk_notification_preferences_user_id_users'),
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_notification_preferences')),
    )
    op.create_index(op.f('ix_notification_preferences_org_id'), 'notification_preferences',
                    ['org_id'], unique=False)
    op.create_index(op.f('ix_notification_preferences_user_id'), 'notification_preferences',
                    ['user_id'], unique=False)
    op.create_index('uq_notif_pref_user_event', 'notification_preferences',
                    ['org_id', 'user_id', 'event_type', 'channel'], unique=True,
                    postgresql_where=sa.text('user_id IS NOT NULL AND event_type IS NOT NULL'))
    op.create_index('uq_notif_pref_user_general', 'notification_preferences',
                    ['org_id', 'user_id', 'channel'], unique=True,
                    postgresql_where=sa.text('user_id IS NOT NULL AND event_type IS NULL'))
    op.create_index('uq_notif_pref_org_event', 'notification_preferences',
                    ['org_id', 'event_type', 'channel'], unique=True,
                    postgresql_where=sa.text('user_id IS NULL AND event_type IS NOT NULL'))
    op.create_index('uq_notif_pref_org_general', 'notification_preferences',
                    ['org_id', 'channel'], unique=True,
                    postgresql_where=sa.text('user_id IS NULL AND event_type IS NULL'))

    # --- notification_deliveries (issue #17 seam) ----------------------------------------- #
    op.create_table(
        'notification_deliveries',
        sa.Column('notification_id', sa.UUID(), nullable=False),
        sa.Column('channel', sa.String(length=20), nullable=False),
        sa.Column('status', sa.String(length=20), server_default='pending', nullable=False),
        sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.ForeignKeyConstraint(['notification_id'], ['notifications.id'],
                                name=op.f('fk_notification_deliveries_notification_id_notifications'),
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'],
                                name=op.f('fk_notification_deliveries_org_id_orgs'),
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_notification_deliveries')),
    )
    op.create_index(op.f('ix_notification_deliveries_org_id'), 'notification_deliveries',
                    ['org_id'], unique=False)
    op.create_index(op.f('ix_notification_deliveries_notification_id'),
                    'notification_deliveries', ['notification_id'], unique=False)
    op.create_index('ix_notification_deliveries_pending', 'notification_deliveries',
                    ['org_id', 'created_at'], unique=False,
                    postgresql_where=sa.text("status = 'pending'"))

    # Tenant isolation (defence-in-depth): every notifications table is org-scoped, RLS-forced.
    for table in _TABLES:
        enable_rls(table)

    # Enable the module for existing orgs (RLS is FORCED, so bind the GUC per org).
    bind = op.get_bind()
    org_ids = bind.execute(sa.text("SELECT id FROM orgs")).scalars().all()
    for org_id in org_ids:
        bind.execute(
            sa.text("SELECT set_config('app.current_org', :org_id, true)"),
            {"org_id": str(org_id)},
        )
        bind.execute(
            sa.text(
                """
                UPDATE org_settings
                SET enabled_modules = enabled_modules || '{notifications}'
                WHERE org_id = :org_id AND NOT ('notifications' = ANY(enabled_modules))
                """
            ),
            {"org_id": str(org_id)},
        )


def downgrade() -> None:
    # Undo the module enablement too, so a downgraded install does not advertise a module whose
    # tables no longer exist. Same GUC dance as the upgrade — org_settings is RLS-FORCED.
    bind = op.get_bind()
    org_ids = bind.execute(sa.text("SELECT id FROM orgs")).scalars().all()
    for org_id in org_ids:
        bind.execute(
            sa.text("SELECT set_config('app.current_org', :org_id, true)"),
            {"org_id": str(org_id)},
        )
        bind.execute(
            sa.text(
                """
                UPDATE org_settings
                SET enabled_modules = array_remove(enabled_modules, 'notifications')
                WHERE org_id = :org_id
                """
            ),
            {"org_id": str(org_id)},
        )

    for table in _TABLES:
        disable_rls(table)

    op.drop_index('ix_notification_deliveries_pending', table_name='notification_deliveries')
    op.drop_index(op.f('ix_notification_deliveries_notification_id'),
                  table_name='notification_deliveries')
    op.drop_index(op.f('ix_notification_deliveries_org_id'),
                  table_name='notification_deliveries')
    op.drop_table('notification_deliveries')

    op.drop_index('uq_notif_pref_org_general', table_name='notification_preferences')
    op.drop_index('uq_notif_pref_org_event', table_name='notification_preferences')
    op.drop_index('uq_notif_pref_user_general', table_name='notification_preferences')
    op.drop_index('uq_notif_pref_user_event', table_name='notification_preferences')
    op.drop_index(op.f('ix_notification_preferences_user_id'),
                  table_name='notification_preferences')
    op.drop_index(op.f('ix_notification_preferences_org_id'),
                  table_name='notification_preferences')
    op.drop_table('notification_preferences')

    op.drop_index('ix_notification_watchers_entity', table_name='notification_watchers')
    op.drop_index(op.f('ix_notification_watchers_user_id'), table_name='notification_watchers')
    op.drop_index(op.f('ix_notification_watchers_org_id'), table_name='notification_watchers')
    op.drop_table('notification_watchers')

    op.drop_index('ix_notifications_unread', table_name='notifications')
    op.drop_index('ix_notifications_user', table_name='notifications')
    op.drop_index(op.f('ix_notifications_user_id'), table_name='notifications')
    op.drop_index(op.f('ix_notifications_org_id'), table_name='notifications')
    op.drop_index(op.f('ix_notifications_event_id'), table_name='notifications')
    op.drop_table('notifications')

    op.drop_index('uq_notification_events_dedup', table_name='notification_events')
    op.drop_index('ix_notification_events_entity', table_name='notification_events')
    op.drop_index(op.f('ix_notification_events_org_id'), table_name='notification_events')
    op.drop_index(op.f('ix_notification_events_event_type'), table_name='notification_events')
    op.drop_table('notification_events')
