"""gcal_multi_calendar

Revision ID: e7a94c1d5b26
Revises: d4e91b2c7f38
Create Date: 2026-08-26 12:00:00.000000

Shared/secondary Google calendars (#440). Expand-only in effect:

- ``google_calendar_channels`` goes from one row per connection to one per
  ``(connection, calendar)`` — the unique constraint widens, and a ``summary`` column arrives
  (``NOT NULL DEFAULT ''``) so the feeds menu can name a selected calendar. Every existing row
  is a valid primary channel under the new constraint, so nothing is rewritten and nobody's
  sync token is lost.
- ``google_calendar_events`` gets ``calendar_id`` into its identity: the same Google event id
  legitimately exists on two calendars (an invitation on the inviter's and on a shared one),
  and two synced calendars must not fight over one row.

``downgrade()`` restores the narrow constraints; it can only apply after the secondary
channels and their events are deleted (the rows the new feature created), which is stated here
rather than silently attempted — a half-applied downgrade on somebody's server is the failure
docs/WORKFLOW.md guards against, so the downgrade deletes the non-primary rows first.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7a94c1d5b26"
down_revision: str | None = "d4e91b2c7f38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "google_calendar_channels",
        sa.Column("summary", sa.String(length=255), server_default="", nullable=False),
    )
    op.drop_constraint(
        "uq_gcal_channels_org_connection", "google_calendar_channels", type_="unique"
    )
    op.create_unique_constraint(
        "uq_gcal_channels_org_conn_calendar",
        "google_calendar_channels",
        ["org_id", "connection_id", "calendar_id"],
    )
    op.drop_constraint("uq_gcal_events_org_conn_event", "google_calendar_events", type_="unique")
    op.create_unique_constraint(
        "uq_gcal_events_org_conn_cal_event",
        "google_calendar_events",
        ["org_id", "connection_id", "calendar_id", "google_event_id"],
    )


def downgrade() -> None:
    # The rows only this feature creates; the narrow constraints cannot hold with them in.
    op.execute("DELETE FROM google_calendar_events WHERE calendar_id <> 'primary'")
    op.execute("DELETE FROM google_calendar_channels WHERE calendar_id <> 'primary'")
    op.drop_constraint(
        "uq_gcal_events_org_conn_cal_event", "google_calendar_events", type_="unique"
    )
    op.create_unique_constraint(
        "uq_gcal_events_org_conn_event",
        "google_calendar_events",
        ["org_id", "connection_id", "google_event_id"],
    )
    op.drop_constraint(
        "uq_gcal_channels_org_conn_calendar", "google_calendar_channels", type_="unique"
    )
    op.create_unique_constraint(
        "uq_gcal_channels_org_connection",
        "google_calendar_channels",
        ["org_id", "connection_id"],
    )
    op.drop_column("google_calendar_channels", "summary")
