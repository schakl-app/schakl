"""Calendar endpoints: the Agenda's feed (cache-only) and Google's push webhook.

The webhook is the repo's first unauthenticated inbound route (docs/GOOGLE.md): a Google push
carries no tenant hostname and no user, so it authenticates with **our own channel token**
(``{org_id}.{connection_id}.{secret}``, minted at watch registration). The handler resolves
the org from the token, binds RLS, loads the channel row *under* RLS and compares the secret
constant-time. Anything that doesn't line up is a 404 — never a hint of what exists.
"""

from __future__ import annotations

import hmac
import logging
import uuid

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.permissions.deps import no_permission_required, require_permission
from app.core.tenancy import RequestContext, require_context
from app.db import async_session_maker, set_current_org
from app.integrations.google.calendar.models import GoogleCalendarChannel
from app.integrations.google.calendar.service import (
    events_feed,
    list_calendars,
    selected_channels,
    set_calendars,
)

logger = logging.getLogger("schakl.google.calendar")

router = APIRouter(prefix="/calendar", tags=["google"])


class CalendarFeedItem(BaseModel):
    id: str
    title: str
    #: Inclusive date-only ISO range — the Agenda's own event shape.
    start: str
    end: str
    all_day: bool
    #: UTC instants for timed events (#155) — the day/week time grid positions by these;
    #: all-day events leave them unset and land in the pinned all-day row.
    starts_at: str | None = None
    ends_at: str | None = None
    html_link: str | None = None
    tentative: bool = False
    #: The organiser cancelled the meeting and it is still on the viewer's calendar — Google
    #: keeps showing it struck through, so the Agenda mirrors that rather than dropping it.
    cancelled: bool = False
    #: Which calendar it came off (#440): ``primary``, or a selected shared calendar's id —
    #: the feeds menu colours and hides per calendar the way team feeds do per person (#281).
    calendar_id: str = "primary"


class CalendarListEntry(BaseModel):
    """One row of the viewer's Google calendarList, with whether it syncs here."""

    id: str
    summary: str
    primary: bool
    access_role: str
    selected: bool


class CalendarSelection(BaseModel):
    """Which shared calendars sync, whole-list (#440). The primary always syncs and is not in
    the vocabulary; an id not on the viewer's own calendarList is refused."""

    calendar_ids: list[str] = Field(default_factory=list, max_length=50)


class SelectedCalendar(BaseModel):
    calendar_id: str
    summary: str
    primary: bool


@router.get(
    "/events",
    response_model=list[CalendarFeedItem],
    dependencies=[require_permission("google.calendar.read")],
)
async def calendar_events(
    date_from: str = Query(..., min_length=10, max_length=10),
    date_to: str = Query(..., min_length=10, max_length=10),
    ctx: RequestContext = Depends(require_context),
) -> list[CalendarFeedItem]:
    """The viewer's own cached Google events. Reads the local cache, never Google live."""
    items = await events_feed(ctx, date_from, date_to)
    return [CalendarFeedItem(**item) for item in items]


@router.get(
    "/calendars",
    response_model=list[CalendarListEntry],
    dependencies=[require_permission("google.connection.manage")],
)
async def calendar_list(ctx: RequestContext = Depends(require_context)) -> list[CalendarListEntry]:
    """The viewer's own calendarList — the selection UI's read (#440). Briefly cached; the
    existing ``calendar.events`` scope covers it, so no re-consent is needed."""
    return [CalendarListEntry(**entry) for entry in await list_calendars(ctx)]


@router.put(
    "/calendars",
    response_model=list[CalendarListEntry],
    dependencies=[require_permission("google.connection.manage")],
)
async def set_calendar_selection(
    payload: CalendarSelection, ctx: RequestContext = Depends(require_context)
) -> list[CalendarListEntry]:
    """Choose which shared calendars sync for the viewer. Deselecting removes the calendar's
    cached events on the spot; selecting queues a sync so the agenda fills without waiting."""
    return [CalendarListEntry(**entry) for entry in await set_calendars(ctx, payload.calendar_ids)]


@router.get(
    "/channels",
    response_model=list[SelectedCalendar],
    dependencies=[require_permission("google.calendar.read")],
)
async def calendar_channels(
    ctx: RequestContext = Depends(require_context),
) -> list[SelectedCalendar]:
    """The viewer's selection off the database alone — what the Agenda's feeds menu reads on
    every open, so it never costs a Google call."""
    return [SelectedCalendar(**row) for row in await selected_channels(ctx)]


@router.post(
    "/webhook",
    dependencies=[
        no_permission_required(
            "Google Calendar push notification; authenticated by our own per-channel token "
            "(org + connection + secret), never by a user session"
        )
    ],
)
async def calendar_webhook(request: Request) -> Response:
    token = request.headers.get("x-goog-channel-token") or ""
    state = request.headers.get("x-goog-resource-state") or ""
    parts = token.split(".")
    if len(parts) != 3:
        return Response(status_code=404)
    try:
        org_id, connection_id = uuid.UUID(parts[0]), uuid.UUID(parts[1])
    except ValueError:
        return Response(status_code=404)

    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        # A connection may hold several channels now (#440, one per selected calendar); the
        # push authenticates against whichever one minted this token. Watches are registered
        # on the primary only, but matching by token rather than by "the row" keeps that a
        # policy instead of a load-bearing assumption.
        channels = (
            (
                await session.execute(
                    select(GoogleCalendarChannel).where(
                        GoogleCalendarChannel.org_id == org_id,
                        GoogleCalendarChannel.connection_id == connection_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        if not any(
            channel.channel_token and hmac.compare_digest(channel.channel_token, token)
            for channel in channels
        ):
            return Response(status_code=404)

    # The initial "sync" ping just confirms the channel; "exists" means something changed.
    if state != "sync":
        from app.core.jobs import enqueue

        try:
            await enqueue(
                "google_calendar_sync_connection", str(org_id), str(connection_id)
            )
        except Exception:  # noqa: BLE001 — the poll-fallback cron covers a missed push
            logger.warning("gcal webhook enqueue failed for connection %s", connection_id)
    return Response(status_code=200)
