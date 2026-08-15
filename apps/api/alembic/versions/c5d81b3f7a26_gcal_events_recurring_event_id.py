"""gcal_events_recurring_event_id

The cached Google event now remembers the series it is an occurrence of. The sync expands
recurrences (``singleEvents=true``), so a row that schakl mirrored as *one* event with an
RRULE — a repeating freelance availability — comes back as many instances, each under an id
of its own that the push outbox has never heard of. ``recurringEventId`` is the only thing on
an instance that names the event we pushed, so it is what lets the Agenda's Google feed drop
occurrences it is already drawing natively (docs/GOOGLE.md §4).

Upgrade path: **expand-only**. A nullable column a released image simply does not select;
the downgrade drops it and the feed falls back to matching event ids, which is where it was.

Nothing can be backfilled — Google is the only holder of the parentage of an instance already
cached — so the upgrade clears every ``sync_token`` instead. The next pull then runs as an
initial sync (30 days back, no horizon forward, ``service.INITIAL_WINDOW_DAYS``) and upserts
the cached rows in place with their series filled in. Deliberately *not* the 410 path's
delete-then-refill: emptying the cache would blank everyone's agenda until the worker caught
up, and there is nothing wrong with the rows themselves.

Revision ID: c5d81b3f7a26
Revises: f4c8a2e37b19
Create Date: 2026-08-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5d81b3f7a26"
down_revision: str | None = "f4c8a2e37b19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "google_calendar_events",
        sa.Column("recurring_event_id", sa.String(length=255), nullable=True),
    )
    # `FORCE ROW LEVEL SECURITY` applies to the owner too, so a bare UPDATE here would report
    # success against zero rows: the GUC is bound per org, like every backfill.
    bind = op.get_bind()
    for org_id in bind.execute(sa.text("SELECT id FROM orgs")).scalars().all():
        bind.execute(
            sa.text("SELECT set_config('app.current_org', :org_id, true)"),
            {"org_id": str(org_id)},
        )
        bind.execute(
            sa.text(
                "UPDATE google_calendar_channels SET sync_token = NULL WHERE org_id = :org_id"
            ),
            {"org_id": str(org_id)},
        )


def downgrade() -> None:
    op.drop_column("google_calendar_events", "recurring_event_id")
