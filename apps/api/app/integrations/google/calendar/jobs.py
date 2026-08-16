"""ARQ jobs for google.calendar: watch renewal, poll fallback, outbox sweep, sync workers.

Every cron first checks the license is still writable for the ``google`` sku: the mount-time
402 gate covers requests, but crons write on a schedule — an expired license must stop the
background half too (issue #137 semantics: expired = read-only, not gone).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text

from app.core.entitlements.service import sku_cron_enabled
from app.core.jobs import enqueue, run_per_org
from app.db import async_session_maker, set_current_org
from app.integrations.google.calendar.models import (
    CalendarEventLink,
    GoogleCalendarChannel,
    LinkStatus,
    WatchStatus,
)
from app.integrations.google.calendar.push import (
    LOCAL_TYPE_TASK_SCHEDULE,
    MAX_ATTEMPTS,
    push_link,
)
from app.integrations.google.calendar.service import ensure_watch, sync_connection
from app.integrations.google.models import ConnectionStatus, GoogleConnection
from app.integrations.google.oauth import google_settings_row, has_calendar_write_scope

logger = logging.getLogger("schakl.google.calendar")

#: The poll fallback's freshness bound — also a safety net under flaky webhook delivery.
_STALE_AFTER = timedelta(minutes=30)


async def _licensed() -> bool:
    return await sku_cron_enabled("google")


async def _calendar_connections(session, org_id: uuid.UUID) -> list[GoogleConnection]:
    row = await google_settings_row(session, org_id)
    if row is None or not row.calendar_enabled:
        return []
    rows = (
        (
            await session.execute(
                select(GoogleConnection).where(
                    GoogleConnection.org_id == org_id,
                    GoogleConnection.status == ConnectionStatus.ACTIVE.value,
                )
            )
        )
        .scalars()
        .all()
    )
    # Accept the broad ``calendar`` scope as well as ``calendar.events`` — both read the
    # Agenda's events; excluding the broader one hid connections from sync entirely (#148).
    return [c for c in rows if has_calendar_write_scope(c.scopes)]


# --------------------------------------------------------------------------- #
# Crons
# --------------------------------------------------------------------------- #
async def google_calendar_renew_channels(ctx: dict) -> None:  # noqa: ARG001
    """Hourly: register watches for new connections, renew ones expiring within a day."""
    if not await _licensed():
        return

    async def _renew(org, session) -> None:
        for connection in await _calendar_connections(session, org.id):
            await ensure_watch(session, org, connection)

    await run_per_org(_renew)


async def google_calendar_poll_fallback(ctx: dict) -> None:  # noqa: ARG001
    """Every 15 min: sync connections that webhooks don't reach (failed watch) or that went
    quiet for too long (missed push)."""
    if not await _licensed():
        return

    async def _poll(org, session) -> None:
        now = datetime.now(UTC)
        for connection in await _calendar_connections(session, org.id):
            channel = await session.scalar(
                select(GoogleCalendarChannel).where(
                    GoogleCalendarChannel.org_id == org.id,
                    GoogleCalendarChannel.connection_id == connection.id,
                )
            )
            fresh = (
                channel is not None
                and channel.watch_status == WatchStatus.ACTIVE.value
                and channel.last_synced_at is not None
                and now - channel.last_synced_at < _STALE_AFTER
            )
            if not fresh:
                await enqueue("google_calendar_sync_connection", str(org.id), str(connection.id))

    await run_per_org(_poll)


#: A pushed task-schedule event whose block no longer exists. The emit sites announce every
#: removal they know about, but a link is the *only* record that an event exists at all, so an
#: orphan is unreachable: nothing will ever name that ``local_id`` again. Kept as a safety net
#: rather than a one-off repair — the failure it cleans up (a block leaving by FK cascade with
#: nobody saying so) is one any future write path can reintroduce, and it also finishes the
#: events already stranded by the task delete that did exactly that. Raw org-scoped SQL, like
#: the leave-type label read: the mirror never imports another module's internals (§6).
_ORPHANED_TASK_LINKS = text(
    """
    UPDATE calendar_event_links AS l
       SET status = :delete_pending, attempts = 0
     WHERE l.org_id = :oid
       AND l.local_type = :local_type
       AND l.status = :pushed
       AND l.google_event_id IS NOT NULL
       AND NOT EXISTS (
             SELECT 1 FROM task_schedules s
              WHERE s.id = l.local_id AND s.org_id = l.org_id
           )
    """
)


async def google_calendar_sweep_outbox(ctx: dict) -> None:  # noqa: ARG001
    """Every 5 min: re-offer links whose enqueue was lost or whose push failed transiently, and
    tombstone any pushed event whose local record has gone without a word."""
    if not await _licensed():
        return

    async def _sweep(org, session) -> None:
        await session.execute(
            _ORPHANED_TASK_LINKS,
            {
                "oid": org.id,
                "local_type": LOCAL_TYPE_TASK_SCHEDULE,
                "pushed": LinkStatus.PUSHED.value,
                "delete_pending": LinkStatus.DELETE_PENDING.value,
            },
        )
        links = (
            (
                await session.execute(
                    select(CalendarEventLink.id).where(
                        CalendarEventLink.org_id == org.id,
                        CalendarEventLink.status.in_(
                            [LinkStatus.PENDING.value, LinkStatus.DELETE_PENDING.value]
                        ),
                        CalendarEventLink.attempts < MAX_ATTEMPTS,
                    )
                )
            )
            .scalars()
            .all()
        )
        for link_id in links:
            await enqueue("google_calendar_push_link", str(org.id), str(link_id))

    await run_per_org(_sweep)


# --------------------------------------------------------------------------- #
# Worker functions (ModuleDescriptor.worker_functions)
# --------------------------------------------------------------------------- #
async def google_calendar_sync_connection(ctx: dict, org_id: str, connection_id: str) -> str:  # noqa: ARG001
    if not await _licensed():
        return "unlicensed"
    async with async_session_maker() as session:
        oid = uuid.UUID(org_id)
        await set_current_org(session, oid)
        from app.core.models import Org

        org = await session.get(Org, oid)
        connection = await session.scalar(
            select(GoogleConnection).where(
                GoogleConnection.org_id == oid,
                GoogleConnection.id == uuid.UUID(connection_id),
            )
        )
        if org is None or connection is None:
            return "gone"
        await sync_connection(session, org, connection)
        await session.commit()
    return "synced"


async def google_calendar_push_link(ctx: dict, org_id: str, link_id: str) -> str:  # noqa: ARG001
    """Push one outbox link. The org id rides along from the enqueue site — RLS is fail-closed,
    so a worker can never look a row's tenant up from the row itself."""
    if not await _licensed():
        return "unlicensed"
    async with async_session_maker() as session:
        lid = uuid.UUID(link_id)
        oid = uuid.UUID(org_id)
        await set_current_org(session, oid)
        from app.core.models import Org

        org = await session.get(Org, oid)
        link = await session.scalar(
            select(CalendarEventLink).where(
                CalendarEventLink.org_id == oid, CalendarEventLink.id == lid
            )
        )
        if org is None or link is None:
            return "gone"
        await push_link(session, org, link)
        await session.commit()
    return "pushed"
