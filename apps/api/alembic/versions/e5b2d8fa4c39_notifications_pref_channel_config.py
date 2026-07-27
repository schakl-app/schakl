"""notifications_pref_channel_config

Per-event preferences for a *personal external channel* (#283, Phase B). Until now a
``notification_preferences`` row could only mean the bell or personal e-mail — the two implicit
channels every member has. A member connecting their own Slack DM needs the same per-event
enable + cadence against that specific channel, so the row gains ``channel_config_id``.

The four partial unique indexes are reworked rather than replaced: each existing one gains
``AND channel_config_id IS NULL`` so it keeps covering exactly the implicit rows, and one new
index covers the per-channel rows. An external personal preference is always user-scoped and
always per-event (routing to *my* Slack is not an org default, and a channel with no event is
just an unrouted channel), so no org-default or general variant is needed.

Upgrade plan (docs/WORKFLOW.md -> *Breaking database changes*):

* **Expand-only.** One nullable FK column, no backfill. Every pre-existing row is ``NULL``, so
  the recreated indexes cover the same set of rows they covered before -- the rebuild cannot
  fail on a duplicate that did not already exist. Applies on top of any released ``head``.
* **Rolling the image tag back is safe.** The previous release resolves preferences by
  ``(user, event, channel)`` and would see a per-channel row as an ordinary ``external`` row.
  It never writes one and never reads a fourth key, so the worst case is a personal channel
  falling back to its ``event_filter`` -- degraded, not broken -- and rolling forward again
  restores it. Nothing is dropped or renamed, so old code keeps running on the new schema.
* **Reversible.** ``downgrade`` drops the new index, deletes the per-channel rows (per org,
  with the RLS GUC bound), restores the four originals unqualified, and drops the column.
  Dropping the rows is the only honest meaning of "undo this feature": the widened unique
  indexes cannot hold two of them for one user and event.

Revision ID: e5b2d8fa4c39
Revises: d4a1c7e93b28
Create Date: 2026-07-27 12:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5b2d8fa4c39'
down_revision: str | None = 'd4a1c7e93b28'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: ``(name, columns, where)`` for the four implicit-channel indexes, in both shapes.
_QUADRANTS: tuple[tuple[str, list[str], str], ...] = (
    (
        'uq_notif_pref_user_event',
        ['org_id', 'user_id', 'event_type', 'channel'],
        'user_id IS NOT NULL AND event_type IS NOT NULL',
    ),
    (
        'uq_notif_pref_user_general',
        ['org_id', 'user_id', 'channel'],
        'user_id IS NOT NULL AND event_type IS NULL',
    ),
    (
        'uq_notif_pref_org_event',
        ['org_id', 'event_type', 'channel'],
        'user_id IS NULL AND event_type IS NOT NULL',
    ),
    (
        'uq_notif_pref_org_general',
        ['org_id', 'channel'],
        'user_id IS NULL AND event_type IS NULL',
    ),
)


def upgrade() -> None:
    op.add_column(
        'notification_preferences',
        sa.Column('channel_config_id', sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        'fk_notification_preferences_channel_config_id_notificati',
        'notification_preferences',
        'notification_channels',
        ['channel_config_id'],
        ['id'],
        ondelete='CASCADE',
    )
    op.create_index(
        'ix_notification_preferences_channel_config_id',
        'notification_preferences',
        ['channel_config_id'],
    )

    # Narrow the four quadrants to the implicit channels they have always described.
    for name, columns, where in _QUADRANTS:
        op.drop_index(name, table_name='notification_preferences')
        op.create_index(
            name,
            'notification_preferences',
            columns,
            unique=True,
            postgresql_where=sa.text(f'{where} AND channel_config_id IS NULL'),
        )

    # One row per (user, channel, event) for a personal external channel.
    op.create_index(
        'uq_notif_pref_user_channel_event',
        'notification_preferences',
        ['org_id', 'user_id', 'channel_config_id', 'event_type'],
        unique=True,
        postgresql_where=sa.text('channel_config_id IS NOT NULL AND event_type IS NOT NULL'),
    )

    _backfill_existing_personal_channels()


#: The event vocabulary as it stood when this migration was written. Deliberately a literal:
#: a migration must apply identically on any future release, so it must never import the
#: evolving ``events.EVENT_TYPES`` (docs/WORKFLOW.md). Later events simply do not need
#: backfilling -- no pre-#283 channel could have been routed one.
_EVENT_TYPES_AT_WRITE_TIME: tuple[str, ...] = (
    'task.assigned',
    'task.unassigned',
    'task.status_changed',
    'task.commented',
    'task.mentioned',
    'task.due_soon',
    'task.overdue',
    'task.scheduled',
    'project.assigned',
    'project.status_changed',
    'project.budget_threshold',
    'company.created',
    'company.status_changed',
    'company.assigned',
    'leave.requested',
    'leave.approved',
    'leave.rejected',
    'time.entry_approved',
    'time.timesheet_reminder',
    'interactions.email_pending',
    'interactions.mentioned',
)


def _backfill_existing_personal_channels() -> None:
    """Keep any already-connected personal channel delivering exactly what it delivered.

    Before this change a personal channel routed by ``event_filter`` and fired immediately.
    From here on the owner's per-event preference routes it, and no row means *not routed* --
    so without a backfill an existing personal channel would silently go quiet on upgrade.
    Each one therefore gets an ``immediate`` row per event it was already receiving (an empty
    filter meant "every event").

    Idempotent (``ON CONFLICT DO NOTHING`` against the new unique index) and per org with the
    RLS GUC bound, because the migration runs as ``schakl_app`` with row security forced and an
    unscoped write would silently touch nothing (docs/WORKFLOW.md).
    """
    bind = op.get_bind()
    org_ids = bind.execute(sa.text('SELECT id FROM orgs')).scalars().all()
    if not org_ids:
        return
    # The event names are a hardcoded constant, so they go in as a SQL literal array rather
    # than a bound parameter -- no array-binding behaviour to depend on across drivers, and
    # nothing user-supplied to inject.
    events_sql = ', '.join(f"'{event}'" for event in _EVENT_TYPES_AT_WRITE_TIME)
    insert = sa.text(
        f"""
        INSERT INTO notification_preferences (
            id, org_id, user_id, channel_config_id, event_type, channel,
            enabled, delay_minutes, digest, created_at, updated_at
        )
        SELECT gen_random_uuid(), c.org_id, c.user_id, c.id, e.event_type, 'external',
               true, 0, 'immediate', now(), now()
        FROM notification_channels c
        CROSS JOIN unnest(ARRAY[{events_sql}]::text[]) AS e(event_type)
        WHERE c.org_id = :org_id
          AND c.user_id IS NOT NULL
          AND (
              jsonb_array_length(c.event_filter) = 0
              OR c.event_filter @> to_jsonb(e.event_type)
          )
        ON CONFLICT DO NOTHING
        """  # noqa: S608 - the interpolated list is the module constant above, not input
    )
    for org_id in org_ids:
        bind.execute(
            sa.text("SELECT set_config('app.current_org', :org_id, true)"),
            {'org_id': str(org_id)},
        )
        bind.execute(insert, {'org_id': str(org_id)})


def downgrade() -> None:
    op.drop_index('uq_notif_pref_user_channel_event', table_name='notification_preferences')

    # The per-channel rows must go before the quadrants widen again, or two of them (the same
    # user + event on two of their channels) would collide on the unqualified unique index and
    # the downgrade would abort mid-flight. RLS is forced and the migration runs as
    # ``schakl_app``, so the delete is per org with the GUC bound -- an unscoped DELETE would
    # silently match nothing and leave the collision in place (docs/WORKFLOW.md).
    bind = op.get_bind()
    org_ids = bind.execute(sa.text('SELECT id FROM orgs')).scalars().all()
    for org_id in org_ids:
        bind.execute(
            sa.text("SELECT set_config('app.current_org', :org_id, true)"),
            {'org_id': str(org_id)},
        )
        bind.execute(
            sa.text(
                'DELETE FROM notification_preferences '
                'WHERE org_id = :org_id AND channel_config_id IS NOT NULL'
            ),
            {'org_id': str(org_id)},
        )

    for name, columns, where in _QUADRANTS:
        op.drop_index(name, table_name='notification_preferences')
        op.create_index(
            name,
            'notification_preferences',
            columns,
            unique=True,
            postgresql_where=sa.text(where),
        )

    op.drop_index(
        'ix_notification_preferences_channel_config_id',
        table_name='notification_preferences',
    )
    op.drop_constraint(
        'fk_notification_preferences_channel_config_id_notificati',
        'notification_preferences',
        type_='foreignkey',
    )
    op.drop_column('notification_preferences', 'channel_config_id')
