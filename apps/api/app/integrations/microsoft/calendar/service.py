"""Incremental Outlook calendar sync (delta links) and the Agenda feed (docs/MICROSOFT.md §4).

The sync engine pulls **deltas** — never the whole calendar — and maintains the local cache the
Agenda reads. Graph's ``calendarView/delta`` differs from Google's ``syncToken`` in one way that
shapes this file: the delta chain is bounded to the window named on its *first* call and never
widens, so a chain started in July never learns about next August. The channel therefore
remembers ``window_end`` and re-baselines (drops the link, wipes its own cache, refills) when
the horizon draws near, exactly as it does on a ``410`` / ``SyncStateNotFound``. Anything
auth-shaped (``invalid_grant``) flags the connection and stands the whole loop down.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import Org
from app.core.tenancy import RequestContext
from app.core.timezone import org_zoneinfo
from app.integrations.microsoft.calendar.models import (
    PRIMARY_CALENDAR,
    MicrosoftCalendarChannel,
    MicrosoftCalendarEvent,
    MicrosoftCalendarEventLink,
    WatchStatus,
)
from app.integrations.microsoft.client import (
    acting_as,
    describe_api_error,
    is_oauth_error,
    mark_connection_error,
)
from app.integrations.microsoft.models import ConnectionStatus, MicrosoftConnection

logger = logging.getLogger("schakl.microsoft.calendar")

#: How far back the *initial* window reaches, and how far ahead. Graph fixes both on the first
#: delta call; the past bound is what the Agenda's history needs, the future bound is what a
#: delta chain can see at all.
INITIAL_WINDOW_PAST_DAYS = 30
INITIAL_WINDOW_FUTURE_DAYS = 365
#: Re-baseline when this much of the future window is left: a chain that stops learning about
#: events 120 days out would leave next quarter's meetings off the Agenda while looking healthy.
REBASELINE_WHEN_LEFT_DAYS = 120
_SELECT = (
    "id,subject,start,end,isAllDay,isCancelled,showAs,webLink,changeKey,"
    "lastModifiedDateTime,seriesMasterId,type"
)


def calendar_path(calendar_id: str) -> str:
    """Graph's address for a calendar: the default needs no id, a named one carries it."""
    if calendar_id == PRIMARY_CALENDAR:
        return "/me"
    return f"/me/calendars/{calendar_id}"


async def channel_for(
    session: AsyncSession,
    org_id: uuid.UUID,
    connection_id: uuid.UUID,
    calendar_id: str = PRIMARY_CALENDAR,
) -> MicrosoftCalendarChannel:
    channel = await session.scalar(
        select(MicrosoftCalendarChannel).where(
            MicrosoftCalendarChannel.org_id == org_id,
            MicrosoftCalendarChannel.connection_id == connection_id,
            MicrosoftCalendarChannel.calendar_id == calendar_id,
        )
    )
    if channel is None:
        channel = MicrosoftCalendarChannel(
            org_id=org_id, connection_id=connection_id, calendar_id=calendar_id
        )
        session.add(channel)
        await session.flush()
    return channel


async def channels_for(
    session: AsyncSession, org_id: uuid.UUID, connection_id: uuid.UUID
) -> list[MicrosoftCalendarChannel]:
    """Every calendar this connection syncs — the selection. The default calendar is created on
    first ask, so a connection that never touched the setting behaves exactly as before."""
    rows = (
        (
            await session.execute(
                select(MicrosoftCalendarChannel)
                .where(
                    MicrosoftCalendarChannel.org_id == org_id,
                    MicrosoftCalendarChannel.connection_id == connection_id,
                )
                .order_by(MicrosoftCalendarChannel.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    if not any(row.calendar_id == PRIMARY_CALENDAR for row in rows):
        primary = MicrosoftCalendarChannel(
            org_id=org_id, connection_id=connection_id, calendar_id=PRIMARY_CALENDAR
        )
        session.add(primary)
        await session.flush()
        rows = [primary, *rows]
    return list(rows)


def parse_graph_datetime(value: str | None) -> datetime | None:
    """``2026-07-08T09:00:00.0000000`` (seven fractional digits, no offset) as a UTC instant.

    Graph answers wall-clock text in whatever zone the ``Prefer`` header named — UTC here — and
    Python parses at most six fractional digits, so the tail is cut before ``fromisoformat``.
    An offset, when a payload does carry one, is honoured.
    """
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    head, _, tail = text.partition(".")
    if tail:
        digits = "".join(ch for ch in tail if ch.isdigit())
        rest = tail[len(digits) :]
        tail = digits[:6] + rest
        text = f"{head}.{tail}" if tail else head
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _parse_when(
    value: dict[str, Any] | None, *, all_day: bool
) -> tuple[datetime | None, date | None]:
    """Graph's ``start``/``end``: an instant for a timed event, the date for an all-day one."""
    if not value or not value.get("dateTime"):
        return None, None
    instant = parse_graph_datetime(value["dateTime"])
    if instant is None:
        return None, None
    if all_day:
        return None, instant.date()
    return instant, None


def _status_of(item: dict[str, Any]) -> str:
    if item.get("isCancelled"):
        return "cancelled"
    if item.get("showAs") == "tentative":
        return "tentative"
    return "confirmed"


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
        select(MicrosoftCalendarEvent).where(
            MicrosoftCalendarEvent.org_id == org_id,
            MicrosoftCalendarEvent.connection_id == connection_id,
            MicrosoftCalendarEvent.calendar_id == calendar_id,
            MicrosoftCalendarEvent.graph_event_id == event_id,
        )
    )
    if "@removed" in item:
        # A tombstone — deleted, or an occurrence dropped from its series. Graph's own
        # instruction is to drop the local copy. An organiser-cancelled meeting is a different
        # thing: it stays on the attendee's calendar with ``isCancelled`` and a start, and is
        # kept struck through below.
        if row is not None:
            await session.delete(row)
        return
    if item.get("type") == "seriesMaster":
        # ``calendarView`` expands the series into occurrences, which each arrive on their own;
        # the master carries the rule and no single date, and would draw as a phantom event.
        return
    all_day = bool(item.get("isAllDay"))
    start_at, start_date = _parse_when(item.get("start"), all_day=all_day)
    end_at, end_date = _parse_when(item.get("end"), all_day=all_day)
    if start_at is None and start_date is None:
        return
    values = dict(
        calendar_id=calendar_id,
        series_master_id=(item.get("seriesMasterId") or "")[:512] or None,
        subject=(item.get("subject") or "")[:1000] or None,
        status=_status_of(item),
        web_link=(item.get("webLink") or "")[:1000] or None,
        change_key=(item.get("changeKey") or "")[:255] or None,
        all_day=all_day,
        start_at=start_at,
        end_at=end_at,
        start_date=start_date,
        end_date=end_date,
        updated_at_graph=parse_graph_datetime(item.get("lastModifiedDateTime")),
    )
    if row is None:
        session.add(
            MicrosoftCalendarEvent(
                org_id=org_id,
                connection_id=connection_id,
                graph_event_id=event_id,
                **values,
            )
        )
    else:
        for key, value in values.items():
            setattr(row, key, value)


class SyncStateLost(Exception):
    """The delta chain died (``410`` / ``SyncStateNotFound``) — re-baseline, never loop."""


async def _wipe_channel_cache(
    session: AsyncSession, org_id: uuid.UUID, connection_id: uuid.UUID, calendar_id: str
) -> None:
    await session.execute(
        delete(MicrosoftCalendarEvent).where(
            MicrosoftCalendarEvent.org_id == org_id,
            MicrosoftCalendarEvent.connection_id == connection_id,
            MicrosoftCalendarEvent.calendar_id == calendar_id,
        )
    )


def _window_nearly_spent(channel: MicrosoftCalendarChannel, today: date) -> bool:
    return (
        channel.delta_link is not None
        and channel.window_end is not None
        and (channel.window_end - today).days < REBASELINE_WHEN_LEFT_DAYS
    )


async def sync_connection(
    session: AsyncSession, org: Org, connection: MicrosoftConnection
) -> None:
    """One incremental pull per selected calendar; safe to call as often as notifications fire.

    Each channel keeps its own cursor and a reset touches only its own cache — a dead chain on
    a shared calendar must not throw away the default calendar's events. An auth-shaped error
    flags the connection and stands the whole loop down: the credential failed, not a calendar.
    """
    if connection.status != ConnectionStatus.ACTIVE.value:
        return
    today = datetime.now(UTC).date()
    for channel in await channels_for(session, org.id, connection.id):
        if _window_nearly_spent(channel, today):
            # The chain cannot see past the window it was started over, so a fresh one is
            # started before the Agenda runs out of future. A refill, not a delta: the old
            # cache is replaced wholesale so nothing the old window lost lingers.
            logger.info(
                "mscal window nearly spent for connection %s calendar %s; re-baselining",
                connection.id,
                channel.calendar_id,
            )
            channel.delta_link = None
            await _wipe_channel_cache(session, org.id, connection.id, channel.calendar_id)
        try:
            await _sync_with_delta(session, org, connection, channel)
        except SyncStateLost:
            logger.info(
                "mscal delta state lost for connection %s calendar %s; full resync",
                connection.id,
                channel.calendar_id,
            )
            channel.delta_link = None
            await _wipe_channel_cache(session, org.id, connection.id, channel.calendar_id)
            await _sync_with_delta(session, org, connection, channel)
        except Exception as exc:
            if await is_oauth_error(exc):
                await mark_connection_error(session, org, connection, str(exc))
                return
            raise


def _initial_window(today: date) -> tuple[datetime, datetime]:
    midnight = datetime.min.time()
    start = datetime.combine(today - timedelta(days=INITIAL_WINDOW_PAST_DAYS), midnight, UTC)
    end = datetime.combine(today + timedelta(days=INITIAL_WINDOW_FUTURE_DAYS), midnight, UTC)
    return start, end


def _iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"


async def _sync_with_delta(
    session: AsyncSession,
    org: Org,
    connection: MicrosoftConnection,
    channel: MicrosoftCalendarChannel,
) -> None:
    today = datetime.now(UTC).date()
    async with acting_as(session, org, connection) as client:
        if channel.delta_link:
            url: str = channel.delta_link
            params: dict[str, Any] | None = None
            window_end = channel.window_end
        else:
            window_start, window_end_dt = _initial_window(today)
            url = f"{calendar_path(channel.calendar_id)}/calendarView/delta"
            params = {
                "startDateTime": _iso_utc(window_start),
                "endDateTime": _iso_utc(window_end_dt),
                "$select": _SELECT,
            }
            window_end = window_end_dt.date()
        while True:
            response = await client.get(url, params=params)
            if response.status_code == 410 or _sync_state_lost(response):
                raise SyncStateLost
            response.raise_for_status()
            body = response.json()
            for item in body.get("value", []):
                await _upsert_event(session, org.id, connection.id, channel.calendar_id, item)
            next_link = body.get("@odata.nextLink")
            if next_link:
                # The continuation carries its own query; passing ours again would double it.
                url, params = next_link, None
                continue
            if body.get("@odata.deltaLink"):
                channel.delta_link = body["@odata.deltaLink"]
                channel.window_end = window_end
            break
    channel.last_synced_at = datetime.now(UTC)
    await session.flush()


def _sync_state_lost(response: Any) -> bool:
    """Graph says a delta cursor died in the body's ``error.code`` as often as in a 410, so a
    refused delta read is inspected before it is raised on."""
    if response.status_code < 400:
        return False
    try:
        error = (response.json() or {}).get("error") or {}
    except Exception:  # noqa: BLE001 — a body that is not JSON is not a sync-state answer
        return False
    code = str(error.get("code") or "") if isinstance(error, dict) else ""
    return code in {"SyncStateNotFound", "SyncStateInvalid", "ErrorInvalidSyncStateData"}


# --------------------------------------------------------------------------- #
# The Agenda's feed — the viewer's own cached events, date-windowed, cache-only
# --------------------------------------------------------------------------- #
def _pushed_ids(org_id: uuid.UUID):
    return select(MicrosoftCalendarEventLink.graph_event_id).where(
        MicrosoftCalendarEventLink.org_id == org_id,
        MicrosoftCalendarEventLink.graph_event_id.is_not(None),
    )


async def events_feed(ctx: RequestContext, date_from: str, date_to: str) -> list[dict[str, Any]]:
    connection = await ctx.session.scalar(
        select(MicrosoftConnection).where(
            MicrosoftConnection.org_id == ctx.org.id,
            MicrosoftConnection.user_id == ctx.user.id,
        )
    )
    if connection is None:
        return []
    zone = await org_zoneinfo(ctx.session, ctx.org.id)
    window_start = datetime.fromisoformat(date_from).replace(tzinfo=zone)
    window_end = datetime.fromisoformat(date_to).replace(tzinfo=zone) + timedelta(days=1)

    # Events schakl itself pushed (approved leave, freelance availability, task blocks) already
    # render natively on the Agenda through their own feeds — the mirror is the same item
    # twice. A repeating row mirrors as one event with a recurrence while ``calendarView``
    # expands it into occurrences under ids of their own, so the test is "this event, or the
    # series it belongs to" (the Google feed's rule, one calendar over).
    pushed = _pushed_ids(ctx.org.id)
    rows = (
        (
            await ctx.session.execute(
                select(MicrosoftCalendarEvent).where(
                    MicrosoftCalendarEvent.org_id == ctx.org.id,
                    MicrosoftCalendarEvent.connection_id == connection.id,
                    MicrosoftCalendarEvent.graph_event_id.not_in(pushed),
                    # `NOT IN` answers NULL for a NULL left-hand side, which would drop every
                    # one-off event on the calendar — so the absence of a series is stated.
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
                        & (
                            MicrosoftCalendarEvent.start_date
                            <= datetime.fromisoformat(date_to).date()
                        )
                        & (
                            MicrosoftCalendarEvent.end_date
                            > datetime.fromisoformat(date_from).date()
                        )
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
            # Graph's all-day end is exclusive; the Agenda wants inclusive.
            end = (row.end_date - timedelta(days=1)).isoformat() if row.end_date else start
            title = row.subject or ""
        elif row.start_at is not None:
            local_start = row.start_at.astimezone(zone)
            local_end = (row.end_at or row.start_at).astimezone(zone)
            start = local_start.date().isoformat()
            end_marker = local_end - timedelta(microseconds=1)
            end = max(local_start.date(), end_marker.date()).isoformat()
            title = f"{local_start.strftime('%H:%M')} {row.subject or ''}".strip()
        else:
            continue
        items.append(
            {
                "id": str(row.id),
                "title": title,
                "start": start,
                "end": end,
                "all_day": row.all_day,
                "calendar_id": row.calendar_id,
                "starts_at": row.start_at.isoformat() if row.start_at else None,
                "ends_at": (row.end_at or row.start_at).isoformat() if row.start_at else None,
                "html_link": row.web_link,
                "tentative": row.status == "tentative",
                "cancelled": row.status == "cancelled",
            }
        )
    items.sort(key=lambda item: (item["start"], item["title"]))
    return items


# --------------------------------------------------------------------------- #
# Calendar selection: which of the viewer's calendars sync
# --------------------------------------------------------------------------- #
_CALENDAR_LIST_TTL = 300
#: A selection ceiling, stated rather than discovered: every selected calendar costs a sync
#: loop per connection, and nobody plans work across more than a handful.
MAX_SELECTED_CALENDARS = 10


def _calendar_list_key(org_id: uuid.UUID, user_id: uuid.UUID) -> str:
    return f"mscal:list:{org_id}:{user_id}"


async def _viewer_connection(ctx: RequestContext) -> MicrosoftConnection:
    from app.errors import AppError

    connection = await ctx.session.scalar(
        select(MicrosoftConnection).where(
            MicrosoftConnection.org_id == ctx.org.id,
            MicrosoftConnection.user_id == ctx.user.id,
            MicrosoftConnection.status == ConnectionStatus.ACTIVE.value,
        )
    )
    if connection is None:
        raise AppError(
            "microsoft_not_connected", "errors.microsoft_not_connected", status_code=409
        )
    return connection


async def _fetch_calendar_list(
    ctx: RequestContext, connection: MicrosoftConnection, *, refresh: bool = False
) -> list[dict[str, Any]]:
    """The viewer's ``/me/calendars``, Redis-cached briefly — an account-page read must not
    cost Graph a round trip on every open. ``Calendars.ReadWrite`` covers this read."""
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
        url: str = "/me/calendars"
        params: dict[str, Any] | None = {"$select": "id,name,isDefaultCalendar,canEdit"}
        while True:
            response = await client.get(url, params=params)
            response.raise_for_status()
            body = response.json()
            for item in body.get("value", []):
                if not item.get("id"):
                    continue
                entries.append(
                    {
                        "id": item["id"],
                        "summary": (item.get("name") or "")[:255],
                        "primary": bool(item.get("isDefaultCalendar")),
                        "access_role": "writer" if item.get("canEdit") else "reader",
                    }
                )
            next_link = body.get("@odata.nextLink")
            if not next_link:
                break
            url, params = next_link, None
    try:
        await get_redis().set(cache_key, json.dumps(entries), ex=_CALENDAR_LIST_TTL)
    except Exception:  # noqa: BLE001 — the cache is a convenience
        pass
    return entries


async def list_calendars(ctx: RequestContext) -> list[dict[str, Any]]:
    """The viewer's calendars, each saying whether it syncs. The default always does — kept as
    the floor rather than offered as a checkbox."""
    connection = await _viewer_connection(ctx)
    entries = await _fetch_calendar_list(ctx, connection)
    selected = set(
        (
            await ctx.session.execute(
                select(MicrosoftCalendarChannel.calendar_id).where(
                    MicrosoftCalendarChannel.org_id == ctx.org.id,
                    MicrosoftCalendarChannel.connection_id == connection.id,
                )
            )
        ).scalars()
    )
    return [
        {**entry, "selected": entry["primary"] or entry["id"] in selected} for entry in entries
    ]


async def set_calendars(ctx: RequestContext, calendar_ids: list[str]) -> list[dict[str, Any]]:
    """Reconcile the viewer's selection: a named calendar gains a channel, an unnamed one loses
    its channel *and its cached events*. The default calendar is never up for it.

    Ids are validated against the live ``/me/calendars`` (an id arrives from a form anyone can
    edit, and subscribing a stranger's string would poll Graph forever about a calendar that
    404s); the list also donates the stored ``summary`` the feeds menu prints.
    """
    from app.errors import AppError

    connection = await _viewer_connection(ctx)
    entries = await _fetch_calendar_list(ctx, connection, refresh=True)
    by_id = {entry["id"]: entry for entry in entries}
    primary_ids = {entry["id"] for entry in entries if entry["primary"]}

    wanted: list[str] = []
    for calendar_id in calendar_ids:
        if calendar_id in primary_ids or calendar_id == PRIMARY_CALENDAR:
            continue  # always synced; not a choice to restate
        if calendar_id not in by_id:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"calendar_ids": "errors.microsoft_calendar_unknown"},
            )
        if calendar_id not in wanted:
            wanted.append(calendar_id)
    if len(wanted) > MAX_SELECTED_CALENDARS:
        raise AppError(
            "validation",
            "errors.validation",
            status_code=422,
            fields={"calendar_ids": "errors.microsoft_calendar_too_many"},
            details={"limit": MAX_SELECTED_CALENDARS},
        )

    existing = {
        row.calendar_id: row for row in await channels_for(ctx.session, ctx.org.id, connection.id)
    }
    for calendar_id in wanted:
        if calendar_id in existing:
            existing[calendar_id].summary = by_id[calendar_id]["summary"]
            continue
        ctx.session.add(
            MicrosoftCalendarChannel(
                org_id=ctx.org.id,
                connection_id=connection.id,
                calendar_id=calendar_id,
                summary=by_id[calendar_id]["summary"],
            )
        )
    for calendar_id, row in existing.items():
        if calendar_id == PRIMARY_CALENDAR or calendar_id in wanted:
            continue
        await ctx.session.delete(row)
        await _wipe_channel_cache(ctx.session, ctx.org.id, connection.id, calendar_id)
    await ctx.session.flush()

    from app.core.jobs import enqueue

    try:
        await enqueue("microsoft_calendar_sync_connection", str(ctx.org.id), str(connection.id))
    except Exception:  # noqa: BLE001 — the poll fallback carries it
        logger.warning("mscal selection sync enqueue failed for connection %s", connection.id)

    return await list_calendars(ctx)


async def selected_channels(ctx: RequestContext) -> list[dict[str, Any]]:
    """The viewer's selection off the database alone — what the Agenda's feeds menu reads on
    every open, so it must never cost a Graph call. Read-only: it creates no default row."""
    connection = await ctx.session.scalar(
        select(MicrosoftConnection).where(
            MicrosoftConnection.org_id == ctx.org.id,
            MicrosoftConnection.user_id == ctx.user.id,
        )
    )
    if connection is None:
        return []
    rows = (
        (
            await ctx.session.execute(
                select(MicrosoftCalendarChannel)
                .where(
                    MicrosoftCalendarChannel.org_id == ctx.org.id,
                    MicrosoftCalendarChannel.connection_id == connection.id,
                )
                .order_by(MicrosoftCalendarChannel.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "calendar_id": row.calendar_id,
            "summary": row.summary,
            "primary": row.calendar_id == PRIMARY_CALENDAR,
        }
        for row in rows
    ]


# --------------------------------------------------------------------------- #
# Change-notification subscriptions — renewal lives in jobs.py
# --------------------------------------------------------------------------- #
#: Outlook resources take at most 4230 minutes on a subscription; a round figure under it.
SUBSCRIPTION_MINUTES = 4200


def webhook_address(org: Org) -> str:
    from app.core.auth.sso import org_base_url

    return f"{org_base_url(org)}/api/v1/microsoft/calendar/webhook"


def mint_client_state(org_id: uuid.UUID, connection_id: uuid.UUID) -> str:
    """``org.connection.secret`` — how the webhook maps a notification back to a tenant."""
    return f"{org_id}.{connection_id}.{uuid.uuid4().hex}"


def _expiration(now: datetime) -> str:
    return _iso_utc(now + timedelta(minutes=SUBSCRIPTION_MINUTES))


async def ensure_subscription(
    session: AsyncSession, org: Org, connection: MicrosoftConnection
) -> None:
    """Register (or renew) the change subscription for one connection's default calendar;
    failure parks it on polling. Graph validates the notification URL synchronously at
    creation — a box with no public HTTPS is refused on the spot, and the poll carries it."""
    channel = await channel_for(session, org.id, connection.id)
    now = datetime.now(UTC)
    if (
        channel.watch_status == WatchStatus.ACTIVE.value
        and channel.expires_at is not None
        and channel.expires_at > now + timedelta(hours=24)
    ):
        return

    state = mint_client_state(org.id, connection.id)
    try:
        async with acting_as(session, org, connection) as client:
            renewed = False
            if channel.subscription_id and channel.watch_status == WatchStatus.ACTIVE.value:
                # A live subscription is renewed in place; one Graph no longer has (404) or
                # that was never live starts afresh with a new secret.
                response = await client.patch(
                    f"/subscriptions/{channel.subscription_id}",
                    json={"expirationDateTime": _expiration(now)},
                )
                if response.status_code != 404:
                    response.raise_for_status()
                    renewed = True
            if not renewed:
                response = await client.post(
                    "/subscriptions",
                    json={
                        "changeType": "created,updated,deleted",
                        "notificationUrl": webhook_address(org),
                        "resource": "/me/events",
                        "expirationDateTime": _expiration(now),
                        "clientState": state,
                    },
                )
                response.raise_for_status()
            body = response.json()
    except Exception as exc:  # noqa: BLE001 — a failed subscription is the designed polling fallback
        if await is_oauth_error(exc):
            await mark_connection_error(session, org, connection, str(exc))
        elif channel.watch_status != WatchStatus.FAILED.value:
            logger.info(
                "mscal subscription failed for connection %s (%s); polling carries it",
                connection.id,
                describe_api_error(exc) or exc,
            )
        channel.watch_status = WatchStatus.FAILED.value
        await session.flush()
        return

    if not renewed:
        # The secret is written only once the subscription exists, so it never outlives a
        # failed create — and a renewal keeps the one Graph is already sending.
        channel.subscription_id = (body.get("id") or "")[:128] or None
        channel.client_state = state
    channel.expires_at = parse_graph_datetime(body.get("expirationDateTime"))
    channel.watch_status = WatchStatus.ACTIVE.value
    await session.flush()
