"""Approved leave, task blocks and availability → the person's own Outlook calendar, one-way.

Event-bus handlers run in the emitter's transaction (``app/core/events.py``), so they must
never speak HTTP: they write/flip an outbox row (``microsoft_calendar_event_links``) carrying
the shared mirror snapshot (``app/core/calendarmirror``), and offer it to the worker
(best-effort; the sweep cron is the safety net). The worker does the Graph I/O:

- ``pending``        → create (or patch, when a bounced request was re-approved) the event in
                       the **person's** default calendar; skip cleanly when they never connected.
- ``delete_pending`` → delete the event; a 404/410 is an event already gone.

The Google integration subscribes to the same seven events; a colleague who connected both
accounts gets the block in both calendars, which is what "mirror my planning" honestly means.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import calendarmirror as mirror
from app.core.calendarmirror import (
    LOCAL_TYPE_AVAILABILITY,
    LOCAL_TYPE_LEAVE,
    LOCAL_TYPE_TASK_SCHEDULE,
)
from app.core.events import EmitContext
from app.core.models import Org
from app.integrations.microsoft.calendar.models import (
    PRIMARY_CALENDAR,
    LinkStatus,
    MicrosoftCalendarEventLink,
)
from app.integrations.microsoft.calendar.service import calendar_path
from app.integrations.microsoft.client import (
    acting_as,
    connection_for,
    is_oauth_error,
    mark_connection_error,
)
from app.integrations.microsoft.models import ConnectionStatus
from app.integrations.microsoft.oauth import has_calendar_write_scope, microsoft_settings_row

logger = logging.getLogger("schakl.microsoft.calendar")

MAX_ATTEMPTS = 5
#: The extended property that marks an event as schakl's (a fixed property-set GUID plus a
#: name, Graph's own addressing scheme for custom properties). Google puts the same fact in
#: ``extendedProperties.private``; here it is what would let a reconcile recognise our events.
SCHAKL_PROPERTY_ID = "String {66f5a359-4659-4830-9070-00040ec6ac6e} Name schakl"
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


async def _enqueue_push(org_id: uuid.UUID, link_id: uuid.UUID) -> None:
    """Best-effort offer to the worker — a Redis outage must never fail the user's write.

    Deferred a moment so the emitter's transaction commits before the worker looks for the
    row; the sweep cron re-offers anything that slips through.
    """
    from app.core.jobs import enqueue

    try:
        await enqueue(
            "microsoft_calendar_push_link",
            str(org_id),
            str(link_id),
            _defer_by=timedelta(seconds=2),
        )
    except Exception:  # noqa: BLE001 — the sweep cron re-offers pending links
        logger.warning("mscal push enqueue failed for link %s; sweep will retry", link_id)


async def _link_for(
    session: AsyncSession, org_id: uuid.UUID, local_type: str, local_id: uuid.UUID
) -> MicrosoftCalendarEventLink | None:
    return await session.scalar(
        select(MicrosoftCalendarEventLink).where(
            MicrosoftCalendarEventLink.org_id == org_id,
            MicrosoftCalendarEventLink.local_type == local_type,
            MicrosoftCalendarEventLink.local_id == local_id,
        )
    )


async def _pushable_connection(session: AsyncSession, org_id: uuid.UUID, user_id: Any):
    """The connection an event may be written through, or ``None``.

    Calendar sync is per-person opt-in via "Microsoft koppelen" and never someone else's
    token, so "no connection" is an ordinary answer, not a failure.
    """
    if not user_id:
        return None
    row = await microsoft_settings_row(session, org_id)
    if row is None or not row.calendar_enabled:
        return None
    connection = await connection_for(session, org_id, user_id)
    if (
        connection is None
        or connection.status != ConnectionStatus.ACTIVE.value
        or not has_calendar_write_scope(connection.scopes)
    ):
        return None
    return connection


def _event_body(payload: dict[str, Any]) -> dict[str, Any]:
    """The Graph event from the shared snapshot: timed within one day, else an all-day span.

    Graph has no ``date``-only start: an all-day event is ``isAllDay`` with midnight instants,
    in UTC so the calendar's own zone cannot shift the day. A timed event carries the org's
    wall clock and its zone, so it still starts at 08:30 on the two days a year the clocks move.
    """
    start_date = payload["start_date"]
    end_date = payload["end_date"]
    body: dict[str, Any] = {
        "subject": payload.get("summary") or "",
        "body": {"contentType": "text", "content": payload.get("description") or ""},
        "showAs": "free" if payload.get("transparency") == "transparent" else "busy",
        "singleValueExtendedProperties": [
            {
                "id": SCHAKL_PROPERTY_ID,
                "value": f"{payload.get('local_type') or LOCAL_TYPE_LEAVE}:"
                f"{payload.get('local_id') or ''}",
            }
        ],
    }
    timed = mirror.is_timed(payload)
    if timed:
        zone = payload.get("timezone") or "UTC"
        body["isAllDay"] = False
        body["start"] = {"dateTime": f"{start_date}T{payload['start_time']}", "timeZone": zone}
        body["end"] = {"dateTime": f"{end_date}T{payload['end_time']}", "timeZone": zone}
    else:
        exclusive_end = (date.fromisoformat(end_date) + timedelta(days=1)).isoformat()
        body["isAllDay"] = True
        body["start"] = {"dateTime": f"{start_date}T00:00:00", "timeZone": "UTC"}
        body["end"] = {"dateTime": f"{exclusive_end}T00:00:00", "timeZone": "UTC"}
    recurrence = _recurrence(payload)
    if recurrence:
        body["recurrence"] = recurrence
    return body


def _recurrence(payload: dict[str, Any]) -> dict[str, Any] | None:
    """A weekly ``patternedRecurrence`` for a rule-shaped row, or ``None`` for a one-off.

    A repeating availability row *is* a recurrence rule, so it mirrors as one event rather than
    as N — which is what keeps an edit an edit and a delete a delete. Graph's shape is a pattern
    plus a range rather than an RRULE string, but the facts are the same three.
    """
    weeks = payload.get("repeat_weeks")
    if not weeks:
        return None
    start = date.fromisoformat(str(payload["start_date"]))
    pattern = {
        "type": "weekly",
        "interval": int(weeks),
        "daysOfWeek": [_WEEKDAYS[start.weekday()]],
    }
    until = payload.get("repeat_until")
    if until:
        range_ = {"type": "endDate", "startDate": start.isoformat(), "endDate": str(until)}
    else:
        range_ = {"type": "noEnd", "startDate": start.isoformat()}
    return {"pattern": pattern, "range": range_}


async def _store_snapshot(
    ctx: EmitContext,
    *,
    local_type: str,
    local_id: uuid.UUID,
    user_id: Any,
    connection_id: uuid.UUID,
    snapshot: dict[str, Any],
    link: MicrosoftCalendarEventLink | None,
) -> None:
    if link is None:
        link = MicrosoftCalendarEventLink(
            org_id=ctx.org.id,
            local_type=local_type,
            local_id=local_id,
            user_id=user_id,
            connection_id=connection_id,
            status=LinkStatus.PENDING.value,
            payload=snapshot,
        )
        ctx.session.add(link)
    else:
        # A bounced request re-approved, or an edit: refresh the snapshot; the worker patches
        # the stored event in place.
        link.user_id = user_id
        link.connection_id = connection_id
        link.status = LinkStatus.PENDING.value
        link.payload = snapshot
        link.attempts = 0
        link.last_error = None
    await ctx.session.flush()
    await _enqueue_push(ctx.org.id, link.id)


async def _tombstone_or_drop(ctx: EmitContext, link: MicrosoftCalendarEventLink | None) -> None:
    if link is None:
        return
    if link.graph_event_id:
        link.status = LinkStatus.DELETE_PENDING.value
        link.attempts = 0
        await ctx.session.flush()
        await _enqueue_push(ctx.org.id, link.id)
    else:
        # Never reached Graph (still pending, or the person not connected): just drop it.
        await ctx.session.delete(link)
        await ctx.session.flush()


# --------------------------------------------------------------------------- #
# Bus handlers — in-transaction, write-only
# --------------------------------------------------------------------------- #
async def handle_leave_approved(ctx: EmitContext, payload: dict[str, Any]) -> None:
    user_id, request_id = payload.get("user_id"), payload.get("leave_request_id")
    if not user_id or not request_id:
        return
    connection = await _pushable_connection(ctx.session, ctx.org.id, user_id)
    if connection is None:
        return
    snapshot = await mirror.leave_snapshot(ctx.session, ctx.org.id, payload, user_id)
    link = await _link_for(ctx.session, ctx.org.id, LOCAL_TYPE_LEAVE, request_id)
    await _store_snapshot(
        ctx,
        local_type=LOCAL_TYPE_LEAVE,
        local_id=request_id,
        user_id=user_id,
        connection_id=connection.id,
        snapshot=snapshot,
        link=link,
    )


async def handle_leave_gone(ctx: EmitContext, payload: dict[str, Any]) -> None:
    """Cancelled / rejected-after-bounce / edited-back-to-pending: remove the pushed event."""
    request_id = payload.get("leave_request_id")
    if not request_id:
        return
    await _tombstone_or_drop(
        ctx, await _link_for(ctx.session, ctx.org.id, LOCAL_TYPE_LEAVE, request_id)
    )


async def handle_availability_saved(ctx: EmitContext, payload: dict[str, Any]) -> None:
    """A freelancer's availability exception → their own Outlook calendar: the row, not the
    day it resolves to, with a repeat as a recurrence (``app/core/calendarmirror`` says why)."""
    user_id, entry_id = payload.get("user_id"), payload.get("availability_id")
    if not user_id or not entry_id:
        return
    connection = await _pushable_connection(ctx.session, ctx.org.id, user_id)
    if connection is None:
        return
    snapshot = await mirror.availability_snapshot(ctx.session, ctx.org.id, payload, user_id)
    local_id = uuid.UUID(str(entry_id))
    link = await _link_for(ctx.session, ctx.org.id, LOCAL_TYPE_AVAILABILITY, local_id)
    await _store_snapshot(
        ctx,
        local_type=LOCAL_TYPE_AVAILABILITY,
        local_id=local_id,
        user_id=user_id,
        connection_id=connection.id,
        snapshot=snapshot,
        link=link,
    )


async def handle_availability_gone(ctx: EmitContext, payload: dict[str, Any]) -> None:
    """The row is going away — so must its mirror, or a withdrawn day stays on the calendar."""
    entry_id = payload.get("availability_id")
    if not entry_id:
        return
    await _tombstone_or_drop(
        ctx,
        await _link_for(
            ctx.session, ctx.org.id, LOCAL_TYPE_AVAILABILITY, uuid.UUID(str(entry_id))
        ),
    )


async def handle_task_schedule_saved(ctx: EmitContext, payload: dict[str, Any]) -> None:
    """A planned task block → the assigned person's Outlook calendar.

    Order matters: what is already in Outlook is settled before the "may we write?" guards,
    because they answer "may we write an event for this person" and never "may that person's
    old event stay" — reassigning to a colleague who never connected must still take the block
    off the original person's calendar.
    """
    user_id, schedule_id = payload.get("user_id"), payload.get("schedule_id")
    if not user_id or not schedule_id:
        return
    link = await _link_for(ctx.session, ctx.org.id, LOCAL_TYPE_TASK_SCHEDULE, schedule_id)
    connection = await _pushable_connection(ctx.session, ctx.org.id, user_id)

    if link is not None and link.graph_event_id and link.user_id != user_id:
        # Reassigned: Graph cannot move an event between people's calendars, so tombstone the
        # old person's event and let this link recreate fresh on the new one.
        tombstone = MicrosoftCalendarEventLink(
            org_id=ctx.org.id,
            local_type=LOCAL_TYPE_TASK_SCHEDULE,
            local_id=uuid.uuid4(),
            user_id=link.user_id,
            connection_id=link.connection_id,
            calendar_id=link.calendar_id,
            graph_event_id=link.graph_event_id,
            status=LinkStatus.DELETE_PENDING.value,
            payload={},
        )
        ctx.session.add(tombstone)
        await ctx.session.flush()
        await _enqueue_push(ctx.org.id, tombstone.id)
        link.graph_event_id = None
        link.change_key = None

    if connection is None:
        # Nothing to push to. A link with no event behind it describes nothing and is dropped;
        # one still holding an event is left alone, because deleting it needs the token we no
        # longer have.
        if link is not None and not link.graph_event_id:
            await ctx.session.delete(link)
            await ctx.session.flush()
        return

    snapshot = await mirror.task_schedule_snapshot(ctx.session, ctx.org, payload, user_id)
    await _store_snapshot(
        ctx,
        local_type=LOCAL_TYPE_TASK_SCHEDULE,
        local_id=schedule_id,
        user_id=user_id,
        connection_id=connection.id,
        snapshot=snapshot,
        link=link,
    )


async def handle_task_schedule_gone(ctx: EmitContext, payload: dict[str, Any]) -> None:
    """A removed block: delete its pushed event so no ghost is left behind."""
    schedule_id = payload.get("schedule_id")
    if not schedule_id:
        return
    await _tombstone_or_drop(
        ctx, await _link_for(ctx.session, ctx.org.id, LOCAL_TYPE_TASK_SCHEDULE, schedule_id)
    )


# --------------------------------------------------------------------------- #
# Worker side — the Graph I/O
# --------------------------------------------------------------------------- #
def _events_path(calendar_id: str) -> str:
    return f"{calendar_path(calendar_id)}/events"


async def push_link(session: AsyncSession, org: Org, link: MicrosoftCalendarEventLink) -> None:
    if link.status not in (LinkStatus.PENDING.value, LinkStatus.DELETE_PENDING.value):
        return  # already handled — job idempotence comes from this guard, not the queue

    connection = None
    if link.user_id is not None:
        connection = await connection_for(session, org.id, link.user_id)
    if connection is None or connection.status != ConnectionStatus.ACTIVE.value:
        if link.status == LinkStatus.DELETE_PENDING.value:
            await session.delete(link)  # nothing left to delete *with*; drop the tombstone
        else:
            link.status = LinkStatus.FAILED.value
            link.last_error = "not_connected"
        await session.flush()
        return

    calendar_id = link.calendar_id or PRIMARY_CALENDAR
    try:
        async with acting_as(session, org, connection) as client:
            if link.status == LinkStatus.DELETE_PENDING.value:
                response = await client.delete(f"/me/events/{link.graph_event_id}")
                if response.status_code not in (200, 204, 404, 410):
                    response.raise_for_status()
                await session.delete(link)
            elif link.graph_event_id:
                response = await client.patch(
                    f"/me/events/{link.graph_event_id}", json=_event_body(link.payload)
                )
                response.raise_for_status()
                link.change_key = (response.json().get("changeKey") or "")[:255] or None
                link.status = LinkStatus.PUSHED.value
            else:
                response = await client.post(
                    _events_path(calendar_id), json=_event_body(link.payload)
                )
                response.raise_for_status()
                body = response.json()
                link.graph_event_id = (body.get("id") or "")[:512] or None
                link.change_key = (body.get("changeKey") or "")[:255] or None
                link.status = LinkStatus.PUSHED.value
    except Exception as exc:
        link.attempts += 1
        link.last_error = str(exc)[:500]
        if await is_oauth_error(exc):
            await mark_connection_error(session, org, connection, str(exc))
        if link.attempts >= MAX_ATTEMPTS:
            link.status = LinkStatus.FAILED.value
        logger.warning("mscal push failed for link %s (attempt %s)", link.id, link.attempts)
    await session.flush()
