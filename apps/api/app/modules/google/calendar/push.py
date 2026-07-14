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
from app.modules.google.calendar.models import CalendarEventLink, LinkStatus
from app.modules.google.calendar.service import CALENDAR_API
from app.modules.google.client import acting_as, connection_for, mark_connection_error
from app.modules.google.models import ConnectionStatus
from app.modules.google.oauth import google_settings_row, has_calendar_write_scope

logger = logging.getLogger("schakl.google.calendar")

LOCAL_TYPE_LEAVE = "leave_request"
LOCAL_TYPE_TASK_SCHEDULE = "task_schedule"
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
    if payload.get("start_time") and payload.get("end_time") and start_date == end_date:
        zone = payload.get("timezone") or "UTC"
        body["start"] = {"dateTime": f"{start_date}T{payload['start_time']}", "timeZone": zone}
        body["end"] = {"dateTime": f"{end_date}T{payload['end_time']}", "timeZone": zone}
        return body
    exclusive_end = (date.fromisoformat(end_date) + timedelta(days=1)).isoformat()
    body["start"] = {"date": start_date}
    body["end"] = {"date": exclusive_end}
    return body


# --------------------------------------------------------------------------- #
# Bus handlers — in-transaction, write-only
# --------------------------------------------------------------------------- #
async def handle_leave_approved(ctx: EmitContext, payload: dict[str, Any]) -> None:
    user_id, request_id = payload.get("user_id"), payload.get("leave_request_id")
    if not user_id or not request_id:
        return
    row = await google_settings_row(ctx.session, ctx.org.id)
    if row is None or not row.calendar_enabled:
        return
    connection = await connection_for(ctx.session, ctx.org.id, user_id)
    # No connection, or one without the calendar grant: nothing to push, by design — leave
    # sync is per-person opt-in via "Google koppelen", never someone else's token. Accept the
    # broad ``calendar`` scope as well as ``calendar.events`` — both write events, and a
    # connection carrying only the broader one was silently dropped before (#148).
    if (
        connection is None
        or connection.status != ConnectionStatus.ACTIVE.value
        or not has_calendar_write_scope(connection.scopes)
    ):
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
# Task-schedule handlers (#188) — same outbox, same worker; a task block ↔ one event
# --------------------------------------------------------------------------- #
async def handle_task_schedule_saved(ctx: EmitContext, payload: dict[str, Any]) -> None:
    """A planned task block → the assigned person's Google Calendar. Guards mirror leave: the
    org must have calendar sync on, and the person must have personally connected with a
    calendar-write scope. The snapshot carries everything ``_event_body`` needs — the worker
    never re-reads a task."""
    user_id, schedule_id = payload.get("user_id"), payload.get("schedule_id")
    if not user_id or not schedule_id:
        return
    row = await google_settings_row(ctx.session, ctx.org.id)
    if row is None or not row.calendar_enabled:
        return
    connection = await connection_for(ctx.session, ctx.org.id, user_id)
    if (
        connection is None
        or connection.status != ConnectionStatus.ACTIVE.value
        or not has_calendar_write_scope(connection.scopes)
    ):
        return

    locale = (
        await ctx.session.scalar(select(User.locale).where(User.id == user_id))
        or await _org_locale(ctx.session, ctx.org.id)
    )
    snapshot = {
        "summary": _task_summary(payload.get("task_title"), locale),
        "description": _task_description(ctx.org, payload, locale),
        "local_type": LOCAL_TYPE_TASK_SCHEDULE,
        "local_id": str(schedule_id),
        "start_date": str(payload["start_date"]),
        "end_date": str(payload["end_date"]),
        "start_time": str(payload["start_time"]) if payload.get("start_time") else None,
        "end_time": str(payload["end_time"]) if payload.get("end_time") else None,
        "timezone": payload.get("timezone") or await _org_timezone(ctx.session, ctx.org.id),
    }
    link = await _link_for(ctx.session, ctx.org.id, LOCAL_TYPE_TASK_SCHEDULE, schedule_id)
    if link is not None and link.google_event_id and link.user_id != user_id:
        # Reassigned to someone else: Google can't move an event between calendars, so tombstone
        # the old person's event for deletion and let this link recreate fresh on the new one.
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


def _task_summary(title: str | None, locale: str | None) -> str:
    """"Taak: Redesign homepage" — the task marker plus its title, in the person's locale."""
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
        from app.modules.google.client import is_oauth_error

        link.attempts += 1
        link.last_error = str(exc)[:500]
        if await is_oauth_error(exc):
            await mark_connection_error(session, org, connection, str(exc))
        if link.attempts >= MAX_ATTEMPTS:
            link.status = LinkStatus.FAILED.value
        logger.warning("gcal push failed for link %s (attempt %s)", link.id, link.attempts)
    await session.flush()
