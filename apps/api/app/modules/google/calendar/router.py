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
from pydantic import BaseModel
from sqlalchemy import select

from app.core.permissions.deps import no_permission_required, require_permission
from app.core.tenancy import RequestContext, require_context
from app.db import async_session_maker, set_current_org
from app.modules.google.calendar.models import GoogleCalendarChannel
from app.modules.google.calendar.service import events_feed

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
        channel = await session.scalar(
            select(GoogleCalendarChannel).where(
                GoogleCalendarChannel.org_id == org_id,
                GoogleCalendarChannel.connection_id == connection_id,
            )
        )
        if channel is None or not channel.channel_token:
            return Response(status_code=404)
        if not hmac.compare_digest(channel.channel_token, token):
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
