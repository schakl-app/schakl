"""Outlook calendar endpoints: the Agenda's feed (cache-only), the selection, and Graph's webhook.

The webhook is an unauthenticated inbound route in the shape the Google one set: a Graph
change notification carries no tenant hostname and no user, so it authenticates with **our own
``clientState``** (``{org_id}.{connection_id}.{secret}``, minted at subscription). The handler
resolves the org from it, binds RLS, loads the channel rows *under* RLS and compares the secret
constant-time. Graph additionally validates a new subscription's URL with a ``validationToken``
handshake that must be echoed back in plain text — that answer discloses nothing and needs no
authentication, because it says only "this URL is ours", which the subscription request
already asserted.

Every notification is answered ``202`` whether or not it matched: Graph retries a non-2xx
delivery for hours and then drops the subscription, and a mismatch is a stale subscription's
noise rather than something to teach a prober about by answering differently.
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
from app.integrations.microsoft.calendar.models import MicrosoftCalendarChannel
from app.integrations.microsoft.calendar.service import (
    events_feed,
    list_calendars,
    selected_channels,
    set_calendars,
)

logger = logging.getLogger("schakl.microsoft.calendar")

router = APIRouter(prefix="/calendar", tags=["microsoft"])


class MicrosoftCalendarFeedItem(BaseModel):
    id: str
    title: str
    #: Inclusive date-only ISO range — the Agenda's own event shape.
    start: str
    end: str
    all_day: bool
    #: UTC instants for timed events — the day/week time grid positions by these.
    starts_at: str | None = None
    ends_at: str | None = None
    #: Outlook's own link to the event. Named as the Google feed names it, so the two Agenda
    #: sources are one shape.
    html_link: str | None = None
    tentative: bool = False
    #: The organiser cancelled the meeting and it is still on the viewer's calendar.
    cancelled: bool = False
    calendar_id: str = "primary"


class MicrosoftCalendarListEntry(BaseModel):
    """One row of the viewer's ``/me/calendars``, with whether it syncs here."""

    id: str
    summary: str
    primary: bool
    access_role: str
    selected: bool


class MicrosoftCalendarSelection(BaseModel):
    """Which extra calendars sync, whole-list. The default calendar always syncs and is not in
    the vocabulary; an id not on the viewer's own list is refused."""

    calendar_ids: list[str] = Field(default_factory=list, max_length=50)


class MicrosoftSelectedCalendar(BaseModel):
    calendar_id: str
    summary: str
    primary: bool


@router.get(
    "/events",
    response_model=list[MicrosoftCalendarFeedItem],
    dependencies=[require_permission("microsoft.calendar.read")],
)
async def microsoft_calendar_events(
    date_from: str = Query(..., min_length=10, max_length=10),
    date_to: str = Query(..., min_length=10, max_length=10),
    ctx: RequestContext = Depends(require_context),
) -> list[MicrosoftCalendarFeedItem]:
    """The viewer's own cached Outlook events. Reads the local cache, never Graph live."""
    items = await events_feed(ctx, date_from, date_to)
    return [MicrosoftCalendarFeedItem(**item) for item in items]


@router.get(
    "/calendars",
    response_model=list[MicrosoftCalendarListEntry],
    dependencies=[require_permission("microsoft.connection.manage")],
)
async def microsoft_calendar_list(
    ctx: RequestContext = Depends(require_context),
) -> list[MicrosoftCalendarListEntry]:
    """The viewer's own calendars — the selection UI's read. Briefly cached; the existing
    ``Calendars.ReadWrite`` scope covers it, so no re-consent is needed."""
    return [MicrosoftCalendarListEntry(**entry) for entry in await list_calendars(ctx)]


@router.put(
    "/calendars",
    response_model=list[MicrosoftCalendarListEntry],
    dependencies=[require_permission("microsoft.connection.manage")],
)
async def microsoft_set_calendar_selection(
    payload: MicrosoftCalendarSelection, ctx: RequestContext = Depends(require_context)
) -> list[MicrosoftCalendarListEntry]:
    """Choose which extra calendars sync for the viewer. Deselecting removes the calendar's
    cached events on the spot; selecting queues a sync so the agenda fills without waiting."""
    return [
        MicrosoftCalendarListEntry(**entry)
        for entry in await set_calendars(ctx, payload.calendar_ids)
    ]


@router.get(
    "/channels",
    response_model=list[MicrosoftSelectedCalendar],
    dependencies=[require_permission("microsoft.calendar.read")],
)
async def microsoft_calendar_channels(
    ctx: RequestContext = Depends(require_context),
) -> list[MicrosoftSelectedCalendar]:
    """The viewer's selection off the database alone — what the Agenda's feeds menu reads on
    every open, so it never costs a Graph call."""
    return [MicrosoftSelectedCalendar(**row) for row in await selected_channels(ctx)]


@router.post(
    "/webhook",
    dependencies=[
        no_permission_required(
            "Microsoft Graph change notification; authenticated by our own per-subscription "
            "clientState (org + connection + secret), never by a user session. The "
            "validationToken handshake echoes a value Graph chose and discloses nothing."
        )
    ],
)
async def microsoft_calendar_webhook(
    request: Request,
    validation_token: str | None = Query(None, alias="validationToken"),
) -> Response:
    if validation_token is not None:
        # Subscription validation: Graph POSTs its token and expects it back verbatim, as
        # text, within ten seconds — before the subscription exists at all.
        return Response(content=validation_token, media_type="text/plain", status_code=200)

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — not JSON: nothing to act on, nothing to say
        return Response(status_code=202)
    notifications = body.get("value") if isinstance(body, dict) else None
    if not isinstance(notifications, list):
        return Response(status_code=202)

    # One sync per connection however many notifications arrived for it in this delivery.
    matched: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for notification in notifications:
        if not isinstance(notification, dict):
            continue
        state = str(notification.get("clientState") or "")
        parts = state.split(".")
        if len(parts) != 3:
            continue
        try:
            org_id, connection_id = uuid.UUID(parts[0]), uuid.UUID(parts[1])
        except ValueError:
            continue
        if (org_id, connection_id) in matched:
            continue
        async with async_session_maker() as session:
            await set_current_org(session, org_id)
            channels = (
                (
                    await session.execute(
                        select(MicrosoftCalendarChannel).where(
                            MicrosoftCalendarChannel.org_id == org_id,
                            MicrosoftCalendarChannel.connection_id == connection_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
        if any(
            channel.client_state and hmac.compare_digest(channel.client_state, state)
            for channel in channels
        ):
            matched.add((org_id, connection_id))

    if matched:
        from app.core.jobs import enqueue

        for org_id, connection_id in matched:
            try:
                await enqueue(
                    "microsoft_calendar_sync_connection", str(org_id), str(connection_id)
                )
            except Exception:  # noqa: BLE001 — the poll-fallback cron covers a missed push
                logger.warning("mscal webhook enqueue failed for connection %s", connection_id)
    return Response(status_code=202)
