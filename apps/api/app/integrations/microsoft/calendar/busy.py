"""The busy seam's Microsoft third (``app/core/busy.py``): the mirror of somebody's Outlook diary.

The Agenda feed reads **one** connection — the viewer's own — and nothing in this integration
ever reads a colleague's calendar, on purpose: a diary is personal. What a scheduler needs is
narrower than a diary and is exactly what Outlook itself shows a colleague: *that* an hour is
taken, never *what by*. So this reads the cached events of every named person's connection and
answers a window with no title for anyone but the caller — the free/busy answer, computed from
the cache the sync already keeps (no live call, no second credential).

Two things are dropped on purpose. Events schakl itself pushed (approved leave, task blocks,
availability) are already answered natively by their own providers, so their mirrors would be
the same hour twice. And a cancelled event is not a booking.
"""

from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta

from sqlalchemy import or_, select

from app.core.busy import BusyItem
from app.core.tenancy import RequestContext
from app.core.timezone import org_zoneinfo
from app.integrations.microsoft.calendar.models import (
    MicrosoftCalendarEvent,
    MicrosoftCalendarEventLink,
)
from app.integrations.microsoft.models import MicrosoftConnection

SOURCE = "microsoft.calendar"


async def microsoft_calendar_busy(
    ctx: RequestContext,
    user_ids: list[uuid.UUID],
    window_start: datetime,
    window_end: datetime,
) -> list[BusyItem]:
    connections = (
        (
            await ctx.session.execute(
                select(MicrosoftConnection).where(
                    MicrosoftConnection.org_id == ctx.org.id,
                    MicrosoftConnection.user_id.in_(user_ids),
                )
            )
        )
        .scalars()
        .all()
    )
    if not connections:
        return []
    owner_of = {connection.id: connection.user_id for connection in connections}
    zone = await org_zoneinfo(ctx.session, ctx.org.id)
    date_from = window_start.astimezone(zone).date()
    date_to = (window_end.astimezone(zone) - timedelta(microseconds=1)).date()

    pushed = select(MicrosoftCalendarEventLink.graph_event_id).where(
        MicrosoftCalendarEventLink.org_id == ctx.org.id,
        MicrosoftCalendarEventLink.graph_event_id.is_not(None),
    )
    rows = (
        (
            await ctx.session.execute(
                select(MicrosoftCalendarEvent).where(
                    MicrosoftCalendarEvent.org_id == ctx.org.id,
                    MicrosoftCalendarEvent.connection_id.in_(list(owner_of)),
                    MicrosoftCalendarEvent.status != "cancelled",
                    MicrosoftCalendarEvent.graph_event_id.not_in(pushed),
                    or_(
                        MicrosoftCalendarEvent.series_master_id.is_(None),
                        MicrosoftCalendarEvent.series_master_id.not_in(pushed),
                    ),
                    (
                        MicrosoftCalendarEvent.start_at.is_not(None)
                        & (MicrosoftCalendarEvent.start_at < window_end)
                        & (MicrosoftCalendarEvent.end_at > window_start)
                    )
                    | (
                        MicrosoftCalendarEvent.start_date.is_not(None)
                        & (MicrosoftCalendarEvent.start_date <= date_to)
                        & (MicrosoftCalendarEvent.end_date > date_from)
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    # The caller's own diary reads its titles under the same key the Agenda feed declares;
    # everybody else's is a window and nothing more.
    own_readable = ctx.can("microsoft.calendar.read")
    items: list[BusyItem] = []
    for event in rows:
        user_id = owner_of[event.connection_id]
        mine = own_readable and user_id == ctx.user.id
        if event.all_day and event.start_date is not None and event.end_date is not None:
            starts_at = datetime.combine(event.start_date, time.min).replace(tzinfo=zone)
            ends_at = datetime.combine(event.end_date, time.min).replace(tzinfo=zone)
        elif event.start_at is not None and event.end_at is not None:
            starts_at, ends_at = event.start_at, event.end_at
        else:
            continue
        items.append(
            BusyItem(
                user_id=user_id,
                starts_at=starts_at,
                ends_at=ends_at,
                source=SOURCE,
                all_day=bool(event.all_day),
                tentative=event.status == "tentative",
                title=(event.subject or None) if mine else None,
                ref=str(event.id) if mine else None,
                href=event.web_link if mine else None,
            )
        )
    return items
