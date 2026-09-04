"""Approved leave → the requester's own Google Calendar, one-way (docs/GOOGLE.md §4, §14).

Event-bus handlers run in the emitter's transaction (``app/core/events.py``), so they must
never speak HTTP: they write/flip an outbox row (``calendar_event_links``) with the event body
snapshotted, and offer it to the worker (best-effort; the sweep cron is the safety net). The
worker does the Google I/O:

- ``pending``      → insert (or update, when a bounced request was re-approved) the event in
                     the **requester's** calendar; skip cleanly when they never connected.
- ``delete_pending`` → delete the event; a 404/410 is an event already gone. An approved
                     request that is cancelled, rejected after a bounce, or edited back to
                     pending must not leave a ghost in someone's calendar.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.models import User
from app.core.auth.sso import org_base_url
from app.core.events import EmitContext
from app.core.models import Org, OrgSettings
from app.core.richtext import markdown_to_plaintext
from app.i18n import translate
from app.integrations.google.calendar.models import CalendarEventLink, LinkStatus
from app.integrations.google.calendar.service import CALENDAR_API
from app.integrations.google.client import acting_as, connection_for, mark_connection_error
from app.integrations.google.models import ConnectionStatus
from app.integrations.google.oauth import google_settings_row, has_calendar_write_scope

logger = logging.getLogger("schakl.google.calendar")

LOCAL_TYPE_LEAVE = "leave_request"
LOCAL_TYPE_TASK_SCHEDULE = "task_schedule"
LOCAL_TYPE_AVAILABILITY = "availability"
MAX_ATTEMPTS = 5


async def _enqueue_push(org_id: uuid.UUID, link_id: uuid.UUID) -> None:
    """Best-effort offer to the worker — a Redis outage must never fail the user's write.

    Deferred a moment so the emitter's transaction commits before the worker looks for the
    row (the automation queue's rule); the sweep cron re-offers anything that slips through.
    """
    from datetime import timedelta as _td

    from app.core.jobs import enqueue

    try:
        await enqueue(
            "google_calendar_push_link", str(org_id), str(link_id), _defer_by=_td(seconds=2)
        )
    except Exception:  # noqa: BLE001 — the sweep cron re-offers pending links
        logger.warning("gcal push enqueue failed for link %s; sweep will retry", link_id)


async def _link_for(
    session: AsyncSession, org_id: uuid.UUID, local_type: str, local_id: uuid.UUID
) -> CalendarEventLink | None:
    return await session.scalar(
        select(CalendarEventLink).where(
            CalendarEventLink.org_id == org_id,
            CalendarEventLink.local_type == local_type,
            CalendarEventLink.local_id == local_id,
        )
    )


async def _pushable_connection(session: AsyncSession, org_id: uuid.UUID, user_id: Any):
    """The connection an event may be written through, or ``None``.

    Calendar sync is per-person opt-in via "Google koppelen" and never someone else's token, so
    "no connection" is an ordinary answer, not a failure. Accept the broad ``calendar`` scope as
    well as ``calendar.events`` — both write events, and a connection carrying only the broader
    one was silently dropped before (#148).
    """
    if not user_id:
        return None
    row = await google_settings_row(session, org_id)
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


async def _org_locale(session: AsyncSession, org_id: uuid.UUID) -> str | None:
    return await session.scalar(
        select(OrgSettings.default_locale).where(OrgSettings.org_id == org_id)
    )


def _event_body(payload: dict[str, Any]) -> dict[str, Any]:
    """The Google event from the snapshot: timed within one day, else an all-day span.

    The event carries its schakl identity in ``extendedProperties.private`` (#148) — that is
    what lets the Agenda's Google feed drop the mirror of a leave item it already shows
    natively, and what marks the event as ours for any future reconciliation.
    """
    start_date = payload["start_date"]
    end_date = payload["end_date"]
    body: dict[str, Any] = {
        "summary": payload.get("summary") or "",
        "extendedProperties": {
            "private": {
                "schakl": payload.get("local_type") or LOCAL_TYPE_LEAVE,
                "schakl_id": payload.get("local_id") or "",
            }
        },
    }
    if payload.get("description"):
        body["description"] = payload["description"]
    # "Show me as free" for a marker that records *availability* rather than an engagement
    # (#... freelance): an extra day someone offers to work is not a booking, and mirroring it
    # as busy would block the very hours it exists to advertise. Absent = Google's own default.
    if payload.get("transparency"):
        body["transparency"] = payload["transparency"]
    timed = bool(payload.get("start_time") and payload.get("end_time") and start_date == end_date)
    if timed:
        zone = payload.get("timezone") or "UTC"
        body["start"] = {"dateTime": f"{start_date}T{payload['start_time']}", "timeZone": zone}
        body["end"] = {"dateTime": f"{end_date}T{payload['end_time']}", "timeZone": zone}
    else:
        exclusive_end = (date.fromisoformat(end_date) + timedelta(days=1)).isoformat()
        body["start"] = {"date": start_date}
        body["end"] = {"date": exclusive_end}
    rule = _rrule(payload.get("repeat_weeks"), payload.get("repeat_until"), timed=timed)
    if rule:
        body["recurrence"] = [rule]
    return body


def _rrule(repeat_weeks: Any, repeat_until: Any, *, timed: bool) -> str | None:
    """A weekly RRULE for a rule-shaped row, or ``None`` for a one-off.

    A repeating availability row *is* a recurrence rule, so it mirrors as one event rather than
    as N — which is what keeps an edit an edit and a delete a delete instead of a diff against
    whatever the last horizon happened to place.

    ``UNTIL`` follows RFC 5545's typing rule: a DATE for an all-day series, a UTC DATE-TIME for a
    timed one. The timed form is stamped a **day late** on purpose — an occurrence at 17:00 local
    in a zone behind UTC falls after 23:59:59Z of its own date, so the honest bound would drop
    the last occurrence. A cadence is at least a week, so a day of slack can never let an extra
    one in.
    """
    if not repeat_weeks:
        return None
    rule = f"RRULE:FREQ=WEEKLY;INTERVAL={int(repeat_weeks)}"
    if repeat_until:
        end = date.fromisoformat(str(repeat_until))
        if timed:
            rule += f";UNTIL={(end + timedelta(days=1)).strftime('%Y%m%d')}T235959Z"
        else:
            rule += f";UNTIL={end.strftime('%Y%m%d')}"
    return rule


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

    # The event lands on the *requester's* calendar, so their locale words it (#148);
    # the org default is the fallback, like everywhere (§8).
    locale = (
        await ctx.session.scalar(select(User.locale).where(User.id == user_id))
        or await _org_locale(ctx.session, ctx.org.id)
    )
    snapshot = {
        "summary": await _leave_summary(ctx.session, ctx.org.id, payload, locale),
        "description": _leave_description(payload, locale),
        "local_type": LOCAL_TYPE_LEAVE,
        "local_id": str(request_id),
        "start_date": str(payload["start_date"]),
        "end_date": str(payload["end_date"]),
        "start_time": str(payload["start_time"]) if payload.get("start_time") else None,
        "end_time": str(payload["end_time"]) if payload.get("end_time") else None,
        "timezone": await _org_timezone(ctx.session, ctx.org.id),
    }
    link = await _link_for(ctx.session, ctx.org.id, LOCAL_TYPE_LEAVE, request_id)
    if link is None:
        link = CalendarEventLink(
            org_id=ctx.org.id,
            local_type=LOCAL_TYPE_LEAVE,
            local_id=request_id,
            user_id=user_id,
            connection_id=connection.id,
            status=LinkStatus.PENDING.value,
            payload=snapshot,
        )
        ctx.session.add(link)
    else:
        # A bounced request re-approved: refresh the snapshot; the worker updates in place.
        link.user_id = user_id
        link.connection_id = connection.id
        link.status = LinkStatus.PENDING.value
        link.payload = snapshot
        link.attempts = 0
        link.last_error = None
    await ctx.session.flush()
    await _enqueue_push(ctx.org.id, link.id)


async def handle_leave_gone(ctx: EmitContext, payload: dict[str, Any]) -> None:
    """Cancelled / rejected-after-bounce / edited-back-to-pending: remove the pushed event."""
    request_id = payload.get("leave_request_id")
    if not request_id:
        return
    link = await _link_for(ctx.session, ctx.org.id, LOCAL_TYPE_LEAVE, request_id)
    if link is None:
        return
    if link.google_event_id:
        link.status = LinkStatus.DELETE_PENDING.value
        link.attempts = 0
        await ctx.session.flush()
        await _enqueue_push(ctx.org.id, link.id)
    else:
        # Never reached Google (still pending, or requester not connected): just drop it.
        await ctx.session.delete(link)
        await ctx.session.flush()


# --------------------------------------------------------------------------- #
# Availability handlers (freelance) — one exception row ↔ one Google event
# --------------------------------------------------------------------------- #
async def handle_availability_saved(ctx: EmitContext, payload: dict[str, Any]) -> None:
    """A freelancer's availability exception → their own Google Calendar.

    Same guards as leave: org sync on, and the person personally connected with a write scope.
    What is mirrored is the **row**, not the day it resolves to — the resolved day is the base
    week bent by exceptions and Google has no base week, so pushing the resolution would mean
    pushing every ordinary working day too. One row, one event, and a repeat travels as an
    RRULE rather than as a horizon of copies.

    The two kinds differ in exactly one more way, and it is the useful one: an ``unavailable``
    day is **busy** (that is what it says), while an ``extra`` day is **free** — a day somebody
    offers to work is not a booking, and mirroring it as busy would block the very hours it
    exists to advertise.
    """
    user_id, entry_id = payload.get("user_id"), payload.get("availability_id")
    if not user_id or not entry_id:
        return
    connection = await _pushable_connection(ctx.session, ctx.org.id, user_id)
    if connection is None:
        return

    locale = (
        await ctx.session.scalar(select(User.locale).where(User.id == user_id))
        or await _org_locale(ctx.session, ctx.org.id)
    )
    unavailable = payload.get("kind") == "unavailable"
    summary = translate(
        "google.calendar.availability_unavailable"
        if unavailable
        else "google.calendar.availability_available",
        locale,
    )
    snapshot = {
        "summary": summary,
        "description": payload.get("note") or "",
        "local_type": LOCAL_TYPE_AVAILABILITY,
        "local_id": str(entry_id),
        "start_date": str(payload["date"]),
        "end_date": str(payload["date"]),
        "start_time": payload.get("start_time"),
        "end_time": payload.get("end_time"),
        "repeat_weeks": payload.get("repeat_weeks"),
        "repeat_until": payload.get("repeat_until"),
        "transparency": "opaque" if unavailable else "transparent",
        "timezone": await _org_timezone(ctx.session, ctx.org.id),
    }
    link = await _link_for(ctx.session, ctx.org.id, LOCAL_TYPE_AVAILABILITY, uuid.UUID(entry_id))
    if link is None:
        link = CalendarEventLink(
            org_id=ctx.org.id,
            local_type=LOCAL_TYPE_AVAILABILITY,
            local_id=uuid.UUID(entry_id),
            user_id=user_id,
            connection_id=connection.id,
            status=LinkStatus.PENDING.value,
            payload=snapshot,
        )
        ctx.session.add(link)
    else:
        link.user_id = user_id
        link.connection_id = connection.id
        link.status = LinkStatus.PENDING.value
        link.payload = snapshot
        link.attempts = 0
        link.last_error = None
    await ctx.session.flush()
    await _enqueue_push(ctx.org.id, link.id)


async def handle_availability_gone(ctx: EmitContext, payload: dict[str, Any]) -> None:
    """The row is going away — so must its mirror, or a withdrawn day stays on the calendar."""
    entry_id = payload.get("availability_id")
    if not entry_id:
        return
    link = await _link_for(ctx.session, ctx.org.id, LOCAL_TYPE_AVAILABILITY, uuid.UUID(entry_id))
    if link is None:
        return
    if link.google_event_id:
        link.status = LinkStatus.DELETE_PENDING.value
        link.attempts = 0
        await ctx.session.flush()
        await _enqueue_push(ctx.org.id, link.id)
    else:
        await ctx.session.delete(link)
        await ctx.session.flush()


# --------------------------------------------------------------------------- #
# Task-schedule handlers (#188) — same outbox, same worker; a task block ↔ one event
# --------------------------------------------------------------------------- #
async def handle_task_schedule_saved(ctx: EmitContext, payload: dict[str, Any]) -> None:
    """A planned task block → the assigned person's Google Calendar. Guards mirror leave: the
    org must have calendar sync on, and the person must have personally connected with a
    calendar-write scope. The snapshot carries everything ``_event_body`` needs — the worker
    never re-reads a task.

    Order matters: what is already in Google is settled before those guards, because they answer
    "may we write an event for this person" and never "may that person's old event stay"."""
    user_id, schedule_id = payload.get("user_id"), payload.get("schedule_id")
    if not user_id or not schedule_id:
        return
    link = await _link_for(ctx.session, ctx.org.id, LOCAL_TYPE_TASK_SCHEDULE, schedule_id)
    connection = await _pushable_connection(ctx.session, ctx.org.id, user_id)

    if link is not None and link.google_event_id and link.user_id != user_id:
        # Reassigned to someone else: Google can't move an event between calendars, so tombstone
        # the old person's event for deletion and let this link recreate fresh on the new one.
        # This runs *before* the "is there anything to push to?" guard on purpose — whether the
        # block's new owner can receive an event says nothing about whether its old owner should
        # keep one, and reassigning to a colleague who never connected Google used to leave the
        # block on the original person's calendar for good.
        tombstone = CalendarEventLink(
            org_id=ctx.org.id,
            local_type=LOCAL_TYPE_TASK_SCHEDULE,
            local_id=uuid.uuid4(),
            user_id=link.user_id,
            connection_id=link.connection_id,
            calendar_id=link.calendar_id,
            google_event_id=link.google_event_id,
            status=LinkStatus.DELETE_PENDING.value,
            payload={},
        )
        ctx.session.add(tombstone)
        await ctx.session.flush()
        await _enqueue_push(ctx.org.id, tombstone.id)
        link.google_event_id = None
        link.etag = None

    if connection is None:
        # Nothing to push to: sync is off org-wide, or this person never connected with a
        # calendar-write scope. A link with no event behind it (never pushed, or just emptied
        # above) describes nothing and is dropped; one still holding an event is left alone,
        # because deleting it needs the token we no longer have.
        if link is not None and not link.google_event_id:
            await ctx.session.delete(link)
            await ctx.session.flush()
        return

    locale = (
        await ctx.session.scalar(select(User.locale).where(User.id == user_id))
        or await _org_locale(ctx.session, ctx.org.id)
    )
    snapshot = {
        "summary": _task_summary(
            payload.get("task_title"), payload.get("company_name"), locale
        ),
        "description": _task_description(ctx.org, payload, locale),
        "local_type": LOCAL_TYPE_TASK_SCHEDULE,
        "local_id": str(schedule_id),
        "start_date": str(payload["start_date"]),
        "end_date": str(payload["end_date"]),
        "start_time": str(payload["start_time"]) if payload.get("start_time") else None,
        "end_time": str(payload["end_time"]) if payload.get("end_time") else None,
        "timezone": payload.get("timezone") or await _org_timezone(ctx.session, ctx.org.id),
    }
    if link is None:
        link = CalendarEventLink(
            org_id=ctx.org.id,
            local_type=LOCAL_TYPE_TASK_SCHEDULE,
            local_id=schedule_id,
            user_id=user_id,
            connection_id=connection.id,
            status=LinkStatus.PENDING.value,
            payload=snapshot,
        )
        ctx.session.add(link)
    else:
        link.user_id = user_id
        link.connection_id = connection.id
        link.status = LinkStatus.PENDING.value
        link.payload = snapshot
        link.attempts = 0
        link.last_error = None
    await ctx.session.flush()
    await _enqueue_push(ctx.org.id, link.id)


async def handle_task_schedule_gone(ctx: EmitContext, payload: dict[str, Any]) -> None:
    """A removed block: delete its pushed event so no ghost is left behind."""
    schedule_id = payload.get("schedule_id")
    if not schedule_id:
        return
    link = await _link_for(ctx.session, ctx.org.id, LOCAL_TYPE_TASK_SCHEDULE, schedule_id)
    if link is None:
        return
    if link.google_event_id:
        link.status = LinkStatus.DELETE_PENDING.value
        link.attempts = 0
        await ctx.session.flush()
        await _enqueue_push(ctx.org.id, link.id)
    else:
        await ctx.session.delete(link)
        await ctx.session.flush()


def _task_summary(title: str | None, company_name: str | None, locale: str | None) -> str:
    """"Nova Fietsen: Redesign homepage" — the client's name and the task's title.

    The client leads because that is what a glance at a week wants to know: whose work sits
    where. The old marker ("Taak: …") said what *kind* of record the block was, which a
    calendar full of them already says, and is kept only for a task with no client — an
    internal job — where there is nothing else to lead with. ``d4a9b3c6f2e7`` retitled the
    events already mirrored, so the two shapes never sit side by side on one calendar.
    """
    if company_name and title:
        return f"{company_name}: {title}"
    if company_name:
        return company_name
    base = translate("google.calendar.task_event_title", locale)
    return f"{base}: {title}" if title else base


def _task_description(org: Org, payload: dict[str, Any], locale: str | None) -> str:
    """The task's own description (flattened from markdown) plus a direct deeplink to the task —
    Google events have no URL field, so the link lives in the notes text (#188)."""
    parts: list[str] = []
    desc = payload.get("task_description")
    if desc:
        parts.append(markdown_to_plaintext(desc))
    parts.append(f"{org_base_url(org)}/tasks/{payload['task_id']}")
    return "\n\n".join(parts)


async def _leave_summary(
    session: AsyncSession, org_id: uuid.UUID, payload: dict[str, Any], locale: str | None
) -> str:
    """"Verlof: Vakantie", never a bare "Verlof" (#148). The tenant's own type label
    (``label_i18n``) is read with org-scoped SQL — the mirror never imports leave internals."""
    base = translate("google.calendar.leave_event_title", locale)
    type_id = payload.get("leave_type_id")
    if not type_id:
        return base
    label_i18n = await session.scalar(
        text("SELECT label_i18n FROM leave_types WHERE id = :tid AND org_id = :oid"),
        {"tid": type_id, "oid": org_id},
    )
    if not isinstance(label_i18n, dict):
        return base
    label = label_i18n.get(locale or "") or label_i18n.get("nl") or label_i18n.get("en")
    if not label:
        label = next(iter(label_i18n.values()), None)
    return f"{base}: {label}" if label else base


def _leave_description(payload: dict[str, Any], locale: str | None) -> str:
    """The per-day breakdown, one line per working day (#148) — Google shows a multi-day
    all-day span without saying which day costs what; this does."""
    lines = [
        translate(
            "google.calendar.leave_event_day",
            locale,
            date=_european_day(row["date"]),
            hours=f"{row['hours']:g}",
        )
        for row in payload.get("breakdown") or []
    ]
    return "\n".join(lines)


def _european_day(iso_day: str) -> str:
    year, month, day = iso_day.split("-")
    return f"{day}-{month}-{year}"


async def _org_timezone(session: AsyncSession, org_id: uuid.UUID) -> str:
    from app.config import settings

    zone = await session.scalar(
        select(OrgSettings.timezone).where(OrgSettings.org_id == org_id)
    )
    return zone or settings.default_timezone


# --------------------------------------------------------------------------- #
# Worker side — the Google I/O
# --------------------------------------------------------------------------- #
async def push_link(session: AsyncSession, org: Org, link: CalendarEventLink) -> None:
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

    try:
        async with acting_as(session, org, connection) as client:
            if link.status == LinkStatus.DELETE_PENDING.value:
                response = await client.delete(
                    f"{CALENDAR_API}/calendars/{link.calendar_id}/events/{link.google_event_id}"
                )
                if response.status_code not in (200, 204, 404, 410):
                    response.raise_for_status()
                await session.delete(link)
            elif link.google_event_id:
                response = await client.put(
                    f"{CALENDAR_API}/calendars/{link.calendar_id}/events/{link.google_event_id}",
                    json=_event_body(link.payload),
                )
                response.raise_for_status()
                link.etag = (response.json().get("etag") or "")[:64] or None
                link.status = LinkStatus.PUSHED.value
            else:
                response = await client.post(
                    f"{CALENDAR_API}/calendars/{link.calendar_id}/events",
                    json=_event_body(link.payload),
                )
                response.raise_for_status()
                body = response.json()
                link.google_event_id = (body.get("id") or "")[:255] or None
                link.etag = (body.get("etag") or "")[:64] or None
                link.status = LinkStatus.PUSHED.value
    except Exception as exc:
        from app.integrations.google.client import is_oauth_error

        link.attempts += 1
        link.last_error = str(exc)[:500]
        if await is_oauth_error(exc):
            await mark_connection_error(session, org, connection, str(exc))
        if link.attempts >= MAX_ATTEMPTS:
            link.status = LinkStatus.FAILED.value
        logger.warning("gcal push failed for link %s (attempt %s)", link.id, link.attempts)
    await session.flush()
