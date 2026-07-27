"""notifications_org_channel_routing

One routing table for every notification channel (#295). A **shared room** (`#crm`) used to be
routed by two columns of its own — ``event_filter`` said which events, ``digest`` said how often —
while a **personal** channel was routed per event from its owner's matrix (#283). That split is
what made "group Slack the way e-mail groups" impossible: a room had exactly one cadence for
everything it received, and the matrix had no column to say otherwise.

So a shared room now gets per-event preference rows too, in the **org** scope
(``user_id IS NULL``) — the same rows a personal channel already had, one layer up. They render as
one more column on the org-default matrix (Instellingen -> Standaard meldingen), so every channel
is set up in the same place and in the same way.

Two indexes rather than one because Postgres treats ``NULL`` as distinct inside a unique index: a
single index spanning ``user_id`` would have admitted unlimited duplicate org rows. The #283 index
therefore gains ``AND user_id IS NOT NULL`` and a mirror-image one covers the org rows.

Upgrade path (docs/WORKFLOW.md -> *Breaking database changes*):

* **Expand only.** Nothing is dropped or renamed. ``notification_channels.event_filter`` and
  ``.digest`` keep their data and their columns and simply stop being read; the *contract* half —
  dropping them — is a later release, once no supported version reads them.
* **Backfilled, so behaviour is unchanged.** Every existing shared channel gets one row per event
  it was already receiving (an empty ``event_filter`` meant "every event"), carrying the cadence
  it was already delivering at. Without this an upgraded instance would go silent on its shared
  rooms, because no row means *not routed*.
* **Rolling the image tag back is safe.** The previous release reads ``event_filter``/``digest``,
  which this migration leaves exactly as it found them, and ignores org-scope channel rows the way
  it ignored every row it did not recognise. Re-narrowing the #283 index only removes org rows from
  its coverage, and the previous release writes none.
* **Reversible.** ``downgrade`` deletes the org-scope channel rows and restores the single #283
  index unqualified. It does not attempt to rebuild ``event_filter`` from the rows: the old columns
  were never touched, so they are still the truth the previous release wants.

Revision ID: f7a2c9e51db4
Revises: b3e6c2f10a47
Create Date: 2026-07-27 14:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f7a2c9e51db4'
down_revision: str | None = 'b3e6c2f10a47'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: The event vocabulary as it stood when this migration was written. Deliberately a literal: a
#: migration must apply identically on any future release, so it must never import the evolving
#: ``events.EVENT_TYPES`` (docs/WORKFLOW.md). A channel with an empty filter received "every
#: event", which can only mean every event that existed at upgrade time.
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


def upgrade() -> None:
    # #283's index covered "any channel row"; it now covers only the personal ones, so that the
    # org mirror below is the sole constraint on org rows rather than a second, weaker one.
    op.drop_index('uq_notif_pref_user_channel_event', table_name='notification_preferences')
    op.create_index(
        'uq_notif_pref_user_channel_event',
        'notification_preferences',
        ['org_id', 'user_id', 'channel_config_id', 'event_type'],
        unique=True,
        postgresql_where=sa.text(
            'channel_config_id IS NOT NULL AND event_type IS NOT NULL AND user_id IS NOT NULL'
        ),
    )
    # One row per (channel, event) for a shared room. ``user_id`` is out of the key entirely:
    # inside a unique index NULLs do not equal each other, so including it would constrain
    # nothing at all.
    op.create_index(
        'uq_notif_pref_org_channel_event',
        'notification_preferences',
        ['org_id', 'channel_config_id', 'event_type'],
        unique=True,
        postgresql_where=sa.text(
            'channel_config_id IS NOT NULL AND event_type IS NOT NULL AND user_id IS NULL'
        ),
    )

    _backfill_shared_channel_routing()


def _backfill_shared_channel_routing() -> None:
    """Keep every already-configured shared room delivering exactly what it delivered.

    ``event_filter`` becomes one row per event it named (empty = every event), and the channel's
    own ``digest`` becomes each row's cadence — so a ``#crm`` room set to a daily digest of leave
    events comes out the other side as a daily digest of leave events, now visible and editable as
    a column of the org-default matrix.

    Idempotent (``ON CONFLICT DO NOTHING`` against the index created above) and per org with the
    RLS GUC bound, because the migration runs as ``schakl_app`` with row security forced and an
    unscoped write would silently touch nothing (docs/WORKFLOW.md).
    """
    bind = op.get_bind()
    org_ids = bind.execute(sa.text('SELECT id FROM orgs')).scalars().all()
    if not org_ids:
        return
    # The event names are a hardcoded constant, so they go in as a SQL literal array rather than
    # a bound parameter -- no array-binding behaviour to depend on across drivers, and nothing
    # user-supplied to inject.
    events_sql = ', '.join(f"'{event}'" for event in _EVENT_TYPES_AT_WRITE_TIME)
    insert = sa.text(
        f"""
        INSERT INTO notification_preferences (
            id, org_id, user_id, channel_config_id, event_type, channel,
            enabled, delay_minutes, digest, created_at, updated_at
        )
        SELECT gen_random_uuid(), c.org_id, NULL, c.id, e.event_type, 'external',
               true, 0, COALESCE(c.digest, 'immediate'), now(), now()
        FROM notification_channels c
        CROSS JOIN unnest(ARRAY[{events_sql}]::text[]) AS e(event_type)
        WHERE c.org_id = :org_id
          AND c.user_id IS NULL
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
    op.drop_index('uq_notif_pref_org_channel_event', table_name='notification_preferences')

    # The org-scope channel rows go before the #283 index widens again: it would then cover them
    # while treating their NULL ``user_id`` as distinct, which is not a collision but is a
    # constraint that no longer says anything -- and the previous release routes shared rooms
    # from ``event_filter`` anyway, so these rows would only be dead weight. RLS is forced and
    # the migration runs as ``schakl_app``, so the delete is per org with the GUC bound: an
    # unscoped DELETE would silently match nothing (docs/WORKFLOW.md).
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
                'WHERE org_id = :org_id AND channel_config_id IS NOT NULL AND user_id IS NULL'
            ),
            {'org_id': str(org_id)},
        )

    op.drop_index('uq_notif_pref_user_channel_event', table_name='notification_preferences')
    op.create_index(
        'uq_notif_pref_user_channel_event',
        'notification_preferences',
        ['org_id', 'user_id', 'channel_config_id', 'event_type'],
        unique=True,
        postgresql_where=sa.text('channel_config_id IS NOT NULL AND event_type IS NOT NULL'),
    )
