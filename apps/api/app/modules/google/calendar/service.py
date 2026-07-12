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

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import Org
from app.core.tenancy import RequestContext
from app.core.timezone import org_zoneinfo
from app.modules.google.calendar.models import (
    CalendarEventLink,
    GoogleCalendarChannel,
    GoogleCalendarEvent,
    WatchStatus,
)
from app.modules.google.client import acting_as, mark_connection_error
from app.modules.google.models import ConnectionStatus, GoogleConnection

logger = logging.getLogger("schakl.google.calendar")

CALENDAR_API = "https://www.googleapis.com/calendar/v3"
#: How far back the *initial* sync reaches; the syncToken carries the constraint forward.
INITIAL_WINDOW_DAYS = 30
_PAGE_SIZE = 250


async def channel_for(
    session: AsyncSession, org_id: uuid.UUID, connection_id: uuid.UUID
) -> GoogleCalendarChannel:
    channel = await session.scalar(
        select(GoogleCalendarChannel).where(
            GoogleCalendarChannel.org_id == org_id,
            GoogleCalendarChannel.connection_id == connection_id,
        )
    )
    if channel is None:
        channel = GoogleCalendarChannel(org_id=org_id, connection_id=connection_id)
        session.add(channel)
        await session.flush()
    return channel


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
            GoogleCalendarEvent.google_event_id == event_id,
        )
    )
    if item.get("status") == "cancelled":
        if row is not None:
            await session.delete(row)
        return
    start_at, start_date = _parse_when(item.get("start"))
    end_at, end_date = _parse_when(item.get("end"))
    updated = item.get("updated")
    values = dict(
        calendar_id=calendar_id,
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
    """One incremental pull for one connection; safe to call as often as webhooks fire."""
    if connection.status != ConnectionStatus.ACTIVE.value:
        return
    channel = await channel_for(session, org.id, connection.id)
    try:
        await _sync_with_token(session, org, connection, channel)
    except SyncTokenExpired:
        # 410 Gone: the cursor died. Reset and refill once — never loop.
        logger.info("gcal syncToken expired for connection %s; full resync", connection.id)
        channel.sync_token = None
        await session.execute(
            delete(GoogleCalendarEvent).where(
                GoogleCalendarEvent.org_id == org.id,
                GoogleCalendarEvent.connection_id == connection.id,
            )
        )
        await _sync_with_token(session, org, connection, channel)
    except Exception as exc:
        from app.modules.google.client import is_oauth_error

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

    # Events schakl itself pushed (approved leave, #148) already render natively on the
    # Agenda through the leave feed — showing the Google mirror too is the same item twice.
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
                "html_link": row.html_link,
                "tentative": row.status == "tentative",
            }
        )
    items.sort(key=lambda item: (item["start"], item["title"]))
    return items


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
        from app.modules.google.client import is_oauth_error

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
