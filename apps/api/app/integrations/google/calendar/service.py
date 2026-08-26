"""Incremental Calendar sync (syncToken) and the Agenda's events feed (docs/GOOGLE.md §4).

The sync engine pulls **deltas** — never the whole calendar — and maintains the local cache
the Agenda reads. A ``410 Gone`` (expired syncToken) resets the cursor and refills the cache
once; anything auth-shaped (``invalid_grant``) flags the connection and stands down.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import Org
from app.core.tenancy import RequestContext
from app.core.timezone import org_zoneinfo
from app.integrations.google.calendar.models import (
    CalendarEventLink,
    GoogleCalendarChannel,
    GoogleCalendarEvent,
    WatchStatus,
)
from app.integrations.google.client import acting_as, mark_connection_error
from app.integrations.google.models import ConnectionStatus, GoogleConnection

logger = logging.getLogger("schakl.google.calendar")

CALENDAR_API = "https://www.googleapis.com/calendar/v3"
#: How far back the *initial* sync reaches; the syncToken carries the constraint forward.
INITIAL_WINDOW_DAYS = 30
_PAGE_SIZE = 250


async def channel_for(
    session: AsyncSession,
    org_id: uuid.UUID,
    connection_id: uuid.UUID,
    calendar_id: str = "primary",
) -> GoogleCalendarChannel:
    channel = await session.scalar(
        select(GoogleCalendarChannel).where(
            GoogleCalendarChannel.org_id == org_id,
            GoogleCalendarChannel.connection_id == connection_id,
            GoogleCalendarChannel.calendar_id == calendar_id,
        )
    )
    if channel is None:
        channel = GoogleCalendarChannel(
            org_id=org_id, connection_id=connection_id, calendar_id=calendar_id
        )
        session.add(channel)
        await session.flush()
    return channel


async def channels_for(
    session: AsyncSession, org_id: uuid.UUID, connection_id: uuid.UUID
) -> list[GoogleCalendarChannel]:
    """Every calendar this connection syncs — the selection (#440). The primary is created on
    first ask, so a connection that never touched the setting behaves exactly as before."""
    rows = (
        (
            await session.execute(
                select(GoogleCalendarChannel)
                .where(
                    GoogleCalendarChannel.org_id == org_id,
                    GoogleCalendarChannel.connection_id == connection_id,
                )
                .order_by(GoogleCalendarChannel.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    if not any(row.calendar_id == "primary" for row in rows):
        primary = GoogleCalendarChannel(
            org_id=org_id, connection_id=connection_id, calendar_id="primary"
        )
        session.add(primary)
        await session.flush()
        rows = [primary, *rows]
    return list(rows)


def _parse_when(value: dict[str, Any] | None) -> tuple[datetime | None, str | None]:
    """Google's ``start``/``end``: ``{"dateTime": …}`` for timed, ``{"date": …}`` all-day."""
    if not value:
        return None, None
    if value.get("dateTime"):
        return datetime.fromisoformat(value["dateTime"]), None
    return None, value.get("date")


async def _upsert_event(
    session: AsyncSession,
    org_id: uuid.UUID,
    connection_id: uuid.UUID,
    calendar_id: str,
    item: dict[str, Any],
) -> None:
    event_id = item.get("id")
    if not event_id:
        return
    row = await session.scalar(
        select(GoogleCalendarEvent).where(
            GoogleCalendarEvent.org_id == org_id,
            GoogleCalendarEvent.connection_id == connection_id,
            GoogleCalendarEvent.calendar_id == calendar_id,
            GoogleCalendarEvent.google_event_id == event_id,
        )
    )
    start_at, start_date = _parse_when(item.get("start"))
    end_at, end_date = _parse_when(item.get("end"))
    if item.get("status") == "cancelled" and start_at is None and start_date is None:
        # A *bare* cancellation is a tombstone — a deleted event, or a dropped instance of a
        # recurring one — and Google's own instruction is to drop the local copy. A meeting the
        # organiser cancelled is the other thing wearing this status: it stays on the attendee's
        # calendar, struck through, and keeps its summary and start. The payload is the only
        # thing that tells the two apart, so the start is what we read it from: a tombstone is
        # only ever guaranteed an ``id``, and one carrying a time is still a meeting to show.
        if row is not None:
            await session.delete(row)
        return
    updated = item.get("updated")
    values = dict(
        calendar_id=calendar_id,
        recurring_event_id=(item.get("recurringEventId") or "")[:255] or None,
        summary=(item.get("summary") or "")[:1000] or None,
        status=item.get("status") or "confirmed",
        html_link=(item.get("htmlLink") or "")[:500] or None,
        etag=(item.get("etag") or "")[:64] or None,
        all_day=start_date is not None,
        start_at=start_at,
        end_at=end_at,
        start_date=datetime.fromisoformat(start_date).date() if start_date else None,
        end_date=datetime.fromisoformat(end_date).date() if end_date else None,
        updated_at_google=datetime.fromisoformat(updated) if updated else None,
    )
    if row is None:
        session.add(
            GoogleCalendarEvent(
                org_id=org_id,
                connection_id=connection_id,
                google_event_id=event_id,
                **values,
            )
        )
    else:
        for key, value in values.items():
            setattr(row, key, value)


async def sync_connection(
    session: AsyncSession, org: Org, connection: GoogleConnection
) -> None:
    """One incremental pull per selected calendar; safe to call as often as webhooks fire.

    Each channel keeps its own cursor, and a 410 resets only its own cache (#440) — an expired
    token on a shared calendar must not throw away the primary's events. An auth-shaped error
    flags the connection and stands the whole loop down: the credential failed, not a calendar.
    """
    if connection.status != ConnectionStatus.ACTIVE.value:
        return
    for channel in await channels_for(session, org.id, connection.id):
        try:
            await _sync_with_token(session, org, connection, channel)
        except SyncTokenExpired:
            # 410 Gone: the cursor died. Reset and refill once — never loop.
            logger.info(
                "gcal syncToken expired for connection %s calendar %s; full resync",
                connection.id,
                channel.calendar_id,
            )
            channel.sync_token = None
            await session.execute(
                delete(GoogleCalendarEvent).where(
                    GoogleCalendarEvent.org_id == org.id,
                    GoogleCalendarEvent.connection_id == connection.id,
                    GoogleCalendarEvent.calendar_id == channel.calendar_id,
                )
            )
            await _sync_with_token(session, org, connection, channel)
        except Exception as exc:
            from app.integrations.google.client import is_oauth_error

            if await is_oauth_error(exc):
                await mark_connection_error(session, org, connection, str(exc))
                return
            raise


class SyncTokenExpired(Exception):
    pass


async def _sync_with_token(
    session: AsyncSession,
    org: Org,
    connection: GoogleConnection,
    channel: GoogleCalendarChannel,
) -> None:
    params: dict[str, Any] = {"maxResults": _PAGE_SIZE, "singleEvents": "true"}
    if channel.sync_token:
        params["syncToken"] = channel.sync_token
    else:
        time_min = datetime.now(UTC) - timedelta(days=INITIAL_WINDOW_DAYS)
        params["timeMin"] = time_min.isoformat().replace("+00:00", "Z")
        # Deliberately no ``showDeleted``: on a *listing* Google hands back soft-deleted events
        # with their fields still populated, which the cancelled-vs-tombstone rule in
        # ``_upsert_event`` would read as live cancelled meetings and resurrect things the user
        # deleted. The cost is that a full refill (an expired syncToken) forgets the cancelled
        # copies until Google mentions them again — it loses a strikethrough rather than
        # inventing an event, which is the right way round.

    async with acting_as(session, org, connection) as client:
        page_token: str | None = None
        while True:
            page_params = dict(params)
            if page_token:
                page_params["pageToken"] = page_token
            response = await client.get(
                f"{CALENDAR_API}/calendars/{channel.calendar_id}/events", params=page_params
            )
            if response.status_code == 410:
                raise SyncTokenExpired
            response.raise_for_status()
            body = response.json()
            for item in body.get("items", []):
                await _upsert_event(session, org.id, connection.id, channel.calendar_id, item)
            page_token = body.get("nextPageToken")
            if not page_token:
                if body.get("nextSyncToken"):
                    channel.sync_token = body["nextSyncToken"][:512]
                break
    channel.last_synced_at = datetime.now(UTC)
    await session.flush()


# --------------------------------------------------------------------------- #
# The Agenda's feed — the viewer's own cached events, date-windowed, cache-only
# --------------------------------------------------------------------------- #
async def events_feed(
    ctx: RequestContext, date_from: str, date_to: str
) -> list[dict[str, Any]]:
    connection = await ctx.session.scalar(
        select(GoogleConnection).where(
            GoogleConnection.org_id == ctx.org.id,
            GoogleConnection.user_id == ctx.user.id,
        )
    )
    if connection is None:
        return []
    zone = await org_zoneinfo(ctx.session, ctx.org.id)
    window_start = datetime.fromisoformat(date_from).replace(tzinfo=zone)
    window_end = datetime.fromisoformat(date_to).replace(tzinfo=zone) + timedelta(days=1)

    # Events schakl itself pushed (approved leave #148, freelance availability, task blocks
    # #188) already render natively on the Agenda through their own feeds — showing the Google
    # mirror too is the same item twice.
    #
    # **A row that repeats is one event and many occurrences, and only the first carries the id
    # we pushed.** A repeating availability row mirrors as a single event with an RRULE (that is
    # what keeps an edit an edit), while the sync expands recurrences (``singleEvents=true``) —
    # so what comes back is a *series of instances*, each under an id of its own that the outbox
    # has never heard of. Every occurrence of a freelancer's weekly availability was therefore
    # drawn twice: once natively, once as its own mirror. An instance names its master in
    # ``recurringEventId``, so the test is "this event, or the series it belongs to".
    pushed = select(CalendarEventLink.google_event_id).where(
        CalendarEventLink.org_id == ctx.org.id,
        CalendarEventLink.google_event_id.is_not(None),
    )
    rows = (
        (
            await ctx.session.execute(
                select(GoogleCalendarEvent).where(
                    GoogleCalendarEvent.org_id == ctx.org.id,
                    GoogleCalendarEvent.connection_id == connection.id,
                    GoogleCalendarEvent.google_event_id.not_in(pushed),
                    # `NOT IN` answers NULL for a NULL left-hand side, which would drop every
                    # one-off event on the calendar — so the absence of a series is stated.
                    or_(
                        GoogleCalendarEvent.recurring_event_id.is_(None),
                        GoogleCalendarEvent.recurring_event_id.not_in(pushed),
                    ),
                    # Two shapes, one window: timed events by instant overlap, all-day by date.
                    (
                        GoogleCalendarEvent.start_at.is_not(None)
                        & (GoogleCalendarEvent.start_at < window_end)
                        & (GoogleCalendarEvent.end_at > window_start)
                    )
                    | (
                        GoogleCalendarEvent.start_date.is_not(None)
                        & (GoogleCalendarEvent.start_date <= datetime.fromisoformat(date_to).date())
                        & (GoogleCalendarEvent.end_date > datetime.fromisoformat(date_from).date())
                    ),
                )
            )
        )
        .scalars()
        .all()
    )

    items: list[dict[str, Any]] = []
    for row in rows:
        if row.all_day and row.start_date is not None:
            start = row.start_date.isoformat()
            # Google's all-day end is exclusive; the Agenda wants inclusive.
            end = (row.end_date - timedelta(days=1)).isoformat() if row.end_date else start
            title = row.summary or ""
        elif row.start_at is not None:
            local_start = row.start_at.astimezone(zone)
            local_end = (row.end_at or row.start_at).astimezone(zone)
            start = local_start.date().isoformat()
            # An event ending exactly at midnight belongs to the day it ended *into*, minus one.
            end_marker = local_end - timedelta(microseconds=1)
            end = max(local_start.date(), end_marker.date()).isoformat()
            title = f"{local_start.strftime('%H:%M')} {row.summary or ''}".strip()
        else:
            continue
        items.append(
            {
                "id": str(row.id),
                "title": title,
                "start": start,
                "end": end,
                "all_day": row.all_day,
                # Which calendar it came off (#440): the feeds menu colours and hides per
                # calendar, exactly as the team feeds do per person (#281).
                "calendar_id": row.calendar_id,
                # The time grid (#155) positions timed events by these instants.
                "starts_at": row.start_at.isoformat() if row.start_at else None,
                "ends_at": (row.end_at or row.start_at).isoformat() if row.start_at else None,
                "html_link": row.html_link,
                "tentative": row.status == "tentative",
                "cancelled": row.status == "cancelled",
            }
        )
    items.sort(key=lambda item: (item["start"], item["title"]))
    return items


# --------------------------------------------------------------------------- #
# Calendar selection (#440): which of the viewer's calendars sync
# --------------------------------------------------------------------------- #
_CALENDAR_LIST_TTL = 300
#: A selection ceiling, stated rather than discovered: every selected calendar costs a sync
#: loop per connection, and nobody plans work across more than a handful.
MAX_SELECTED_CALENDARS = 10


def _calendar_list_key(org_id: uuid.UUID, user_id: uuid.UUID) -> str:
    return f"gcal:list:{org_id}:{user_id}"


async def _viewer_connection(ctx: RequestContext) -> GoogleConnection:
    from app.errors import AppError

    connection = await ctx.session.scalar(
        select(GoogleConnection).where(
            GoogleConnection.org_id == ctx.org.id,
            GoogleConnection.user_id == ctx.user.id,
            GoogleConnection.status == ConnectionStatus.ACTIVE.value,
        )
    )
    if connection is None:
        raise AppError("google_not_connected", "errors.google_not_connected", status_code=409)
    return connection


async def _fetch_calendar_list(
    ctx: RequestContext, connection: GoogleConnection, *, refresh: bool = False
) -> list[dict[str, Any]]:
    """The viewer's ``calendarList``, Redis-cached briefly — an account-page read must not
    cost Google a round trip on every open. ``calendar.events`` covers this read."""
    import json

    from app.core.cache import get_redis

    cache_key = _calendar_list_key(ctx.org.id, ctx.user.id)
    if not refresh:
        try:
            cached = await get_redis().get(cache_key)
        except Exception:  # noqa: BLE001 — a cold cache, not an error
            cached = None
        if cached:
            return json.loads(cached)

    entries: list[dict[str, Any]] = []
    async with acting_as(ctx.session, ctx.org, connection) as client, ctx.release_db():
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {"maxResults": 250}
            if page_token:
                params["pageToken"] = page_token
            response = await client.get(f"{CALENDAR_API}/users/me/calendarList", params=params)
            response.raise_for_status()
            body = response.json()
            for item in body.get("items", []):
                if not item.get("id"):
                    continue
                entries.append(
                    {
                        "id": item["id"],
                        "summary": (item.get("summaryOverride") or item.get("summary") or "")[
                            :255
                        ],
                        "primary": bool(item.get("primary")),
                        "access_role": item.get("accessRole") or "",
                    }
                )
            page_token = body.get("nextPageToken")
            if not page_token:
                break
    try:
        await get_redis().set(cache_key, json.dumps(entries), ex=_CALENDAR_LIST_TTL)
    except Exception:  # noqa: BLE001 — the cache is a convenience
        pass
    return entries


async def list_calendars(ctx: RequestContext) -> list[dict[str, Any]]:
    """The viewer's calendars, each saying whether it syncs. The primary always does — that is
    the pre-#440 behaviour, kept as the floor rather than offered as a checkbox."""
    connection = await _viewer_connection(ctx)
    entries = await _fetch_calendar_list(ctx, connection)
    # A plain read — no primary row is created on a GET; the sync loop makes that one.
    selected = set(
        (
            await ctx.session.execute(
                select(GoogleCalendarChannel.calendar_id).where(
                    GoogleCalendarChannel.org_id == ctx.org.id,
                    GoogleCalendarChannel.connection_id == connection.id,
                )
            )
        ).scalars()
    )
    return [
        {
            **entry,
            "selected": entry["primary"] or entry["id"] in selected,
        }
        for entry in entries
    ]


async def set_calendars(ctx: RequestContext, calendar_ids: list[str]) -> list[dict[str, Any]]:
    """Reconcile the viewer's selection: a named calendar gains a channel, an unnamed one
    loses its channel *and its cached events* — a deselected calendar's meetings must leave
    the agenda, not linger until the next full resync. The primary is never up for it.

    Ids are validated against the live ``calendarList`` (an id arrives from a form anyone can
    edit, and subscribing a stranger's string would poll Google forever about a calendar that
    404s); the list also donates the stored ``summary`` the feeds menu prints.
    """
    from app.errors import AppError

    connection = await _viewer_connection(ctx)
    entries = await _fetch_calendar_list(ctx, connection, refresh=True)
    by_id = {entry["id"]: entry for entry in entries}
    primary_ids = {entry["id"] for entry in entries if entry["primary"]}

    wanted: list[str] = []
    for calendar_id in calendar_ids:
        if calendar_id in primary_ids or calendar_id == "primary":
            continue  # always synced; not a choice to restate
        if calendar_id not in by_id:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"calendar_ids": "errors.google_calendar_unknown"},
            )
        if calendar_id not in wanted:
            wanted.append(calendar_id)
    if len(wanted) > MAX_SELECTED_CALENDARS:
        raise AppError(
            "validation",
            "errors.validation",
            status_code=422,
            fields={"calendar_ids": "errors.google_calendar_too_many"},
            details={"limit": MAX_SELECTED_CALENDARS},
        )

    existing = {
        row.calendar_id: row
        for row in await channels_for(ctx.session, ctx.org.id, connection.id)
    }
    for calendar_id in wanted:
        if calendar_id in existing:
            existing[calendar_id].summary = by_id[calendar_id]["summary"]
            continue
        ctx.session.add(
            GoogleCalendarChannel(
                org_id=ctx.org.id,
                connection_id=connection.id,
                calendar_id=calendar_id,
                summary=by_id[calendar_id]["summary"],
            )
        )
    for calendar_id, row in existing.items():
        if calendar_id == "primary" or calendar_id in wanted:
            continue
        await ctx.session.delete(row)
        await ctx.session.execute(
            delete(GoogleCalendarEvent).where(
                GoogleCalendarEvent.org_id == ctx.org.id,
                GoogleCalendarEvent.connection_id == connection.id,
                GoogleCalendarEvent.calendar_id == calendar_id,
            )
        )
    await ctx.session.flush()

    # The new calendar's events should not wait for the next webhook or the 15-minute poll.
    from app.core.jobs import enqueue

    try:
        await enqueue("google_calendar_sync_connection", str(ctx.org.id), str(connection.id))
    except Exception:  # noqa: BLE001 — the poll fallback carries it
        logger.warning("gcal selection sync enqueue failed for connection %s", connection.id)

    return await list_calendars(ctx)


async def selected_channels(ctx: RequestContext) -> list[dict[str, Any]]:
    """The viewer's selection off the database alone — what the Agenda's feeds menu reads on
    every open, so it must never cost a Google call. Read-only: it creates no primary row."""
    connection = await ctx.session.scalar(
        select(GoogleConnection).where(
            GoogleConnection.org_id == ctx.org.id,
            GoogleConnection.user_id == ctx.user.id,
        )
    )
    if connection is None:
        return []
    rows = (
        (
            await ctx.session.execute(
                select(GoogleCalendarChannel)
                .where(
                    GoogleCalendarChannel.org_id == ctx.org.id,
                    GoogleCalendarChannel.connection_id == connection.id,
                )
                .order_by(GoogleCalendarChannel.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "calendar_id": row.calendar_id,
            "summary": row.summary,
            "primary": row.calendar_id == "primary",
        }
        for row in rows
    ]


# --------------------------------------------------------------------------- #
# Watch channels (push notifications) — renewal lives in jobs.py
# --------------------------------------------------------------------------- #
def watch_address(org: Org) -> str:
    from app.core.auth.sso import org_base_url

    return f"{org_base_url(org)}/api/v1/google/calendar/webhook"


def mint_channel_token(org_id: uuid.UUID, connection_id: uuid.UUID) -> str:
    """``org.connection.secret`` — how the webhook maps a push back to a tenant (GOOGLE.md)."""
    return f"{org_id}.{connection_id}.{uuid.uuid4().hex}"


async def ensure_watch(
    session: AsyncSession, org: Org, connection: GoogleConnection
) -> None:
    """Register (or renew) the push channel for one connection; failure parks it on polling."""
    channel = await channel_for(session, org.id, connection.id)
    now = datetime.now(UTC)
    if (
        channel.watch_status == WatchStatus.ACTIVE.value
        and channel.expires_at is not None
        and channel.expires_at > now + timedelta(hours=24)
    ):
        return

    new_channel_id = uuid.uuid4().hex
    token = mint_channel_token(org.id, connection.id)
    try:
        async with acting_as(session, org, connection) as client:
            # Renewal is stop + re-watch; a stop that 404s is a channel already gone.
            if channel.channel_id and channel.resource_id:
                await client.post(
                    f"{CALENDAR_API}/channels/stop",
                    json={"id": channel.channel_id, "resourceId": channel.resource_id},
                )
            response = await client.post(
                f"{CALENDAR_API}/calendars/{channel.calendar_id}/events/watch",
                json={
                    "id": new_channel_id,
                    "type": "web_hook",
                    "address": watch_address(org),
                    "token": token,
                },
            )
            response.raise_for_status()
            body = response.json()
    except Exception as exc:  # noqa: BLE001 — a failed watch is the designed polling fallback
        from app.integrations.google.client import is_oauth_error

        if await is_oauth_error(exc):
            await mark_connection_error(session, org, connection, str(exc))
        elif channel.watch_status != WatchStatus.FAILED.value:
            logger.info(
                "gcal watch registration failed for connection %s (%s); polling carries it",
                connection.id,
                exc,
            )
        channel.watch_status = WatchStatus.FAILED.value
        await session.flush()
        return

    channel.channel_id = new_channel_id
    channel.resource_id = (body.get("resourceId") or "")[:128] or None
    channel.channel_token = token
    expiration_ms = body.get("expiration")
    channel.expires_at = (
        datetime.fromtimestamp(int(expiration_ms) / 1000, tz=UTC) if expiration_ms else None
    )
    channel.watch_status = WatchStatus.ACTIVE.value
    await session.flush()
