"""ARQ jobs for microsoft.calendar: subscription renewal, poll fallback, outbox sweep, workers.

Every cron first checks the license is still writable for the ``microsoft`` sku: the
mount-time 402 gate covers requests, but crons write on a schedule — an expired license must
stop the background half too (issue #137 semantics: expired = read-only, not gone).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text

from app.core.calendarmirror import LOCAL_TYPE_TASK_SCHEDULE
from app.core.entitlements.service import sku_cron_enabled
from app.core.jobs import enqueue, run_per_org
from app.db import async_session_maker, set_current_org
from app.integrations.microsoft.calendar.models import (
    PRIMARY_CALENDAR,
    LinkStatus,
    MicrosoftCalendarChannel,
    MicrosoftCalendarEventLink,
    WatchStatus,
)
from app.integrations.microsoft.calendar.push import MAX_ATTEMPTS, push_link
from app.integrations.microsoft.calendar.service import ensure_subscription, sync_connection
from app.integrations.microsoft.models import ConnectionStatus, MicrosoftConnection
from app.integrations.microsoft.oauth import has_calendar_write_scope, microsoft_settings_row

logger = logging.getLogger("schakl.microsoft.calendar")

#: The poll fallback's freshness bound — also a safety net under flaky notification delivery.
_STALE_AFTER = timedelta(minutes=30)


async def _licensed() -> bool:
    return await sku_cron_enabled("microsoft")


async def _calendar_connections(session, org_id: uuid.UUID) -> list[MicrosoftConnection]:
    row = await microsoft_settings_row(session, org_id)
    if row is None or not row.calendar_enabled:
        return []
    rows = (
        (
            await session.execute(
                select(MicrosoftConnection).where(
                    MicrosoftConnection.org_id == org_id,
                    MicrosoftConnection.status == ConnectionStatus.ACTIVE.value,
                )
            )
        )
        .scalars()
        .all()
    )
    return [c for c in rows if has_calendar_write_scope(c.scopes)]


# --------------------------------------------------------------------------- #
# Crons
# --------------------------------------------------------------------------- #
async def microsoft_calendar_renew_subscriptions(ctx: dict) -> None:  # noqa: ARG001
    """Hourly: subscribe new connections, renew subscriptions expiring within a day. Graph's
    ceiling for Outlook resources is under three days, so hourly is the honest cadence."""
    if not await _licensed():
        return

    async def _renew(org, session) -> None:
        for connection in await _calendar_connections(session, org.id):
            await ensure_subscription(session, org, connection)

    await run_per_org(_renew)


async def microsoft_calendar_poll_fallback(ctx: dict) -> None:  # noqa: ARG001
    """Every 15 min: sync connections that notifications don't reach (failed subscription) or
    that went quiet for too long (missed delivery)."""
    if not await _licensed():
        return

    async def _poll(org, session) -> None:
        now = datetime.now(UTC)
        for connection in await _calendar_connections(session, org.id):
            channels = (
                (
                    await session.execute(
                        select(MicrosoftCalendarChannel).where(
                            MicrosoftCalendarChannel.org_id == org.id,
                            MicrosoftCalendarChannel.connection_id == connection.id,
                        )
                    )
                )
                .scalars()
                .all()
            )

            def fresh(channel: MicrosoftCalendarChannel) -> bool:
                return (
                    channel.last_synced_at is not None
                    and now - channel.last_synced_at < _STALE_AFTER  # noqa: B023 — read-only loop var
                )

            # Only the default calendar carries a subscription; a secondary rides this poll
            # alone. One stale channel — or none at all — re-syncs the connection.
            primary = next((c for c in channels if c.calendar_id == PRIMARY_CALENDAR), None)
            primary_ok = (
                primary is not None
                and primary.watch_status == WatchStatus.ACTIVE.value
                and fresh(primary)
            )
            secondaries_ok = all(fresh(c) for c in channels if c.calendar_id != PRIMARY_CALENDAR)
            if not (primary_ok and secondaries_ok):
                await enqueue(
                    "microsoft_calendar_sync_connection", str(org.id), str(connection.id)
                )

    await run_per_org(_poll)


#: A pushed task-schedule event whose block no longer exists — the Google sweep's safety net,
#: for the same reason: a link is the only record that an event exists, and a block that left
#: by FK cascade with nobody saying so is otherwise unreachable for ever. Scoped to task
#: schedules on purpose — a leave request is cancelled, never hard-deleted.
_ORPHANED_TASK_LINKS = text(
    """
    UPDATE microsoft_calendar_event_links AS l
       SET status = :delete_pending, attempts = 0
     WHERE l.org_id = :oid
       AND l.local_type = :local_type
       AND l.status = :pushed
       AND l.graph_event_id IS NOT NULL
       AND NOT EXISTS (
             SELECT 1 FROM task_schedules s
              WHERE s.id = l.local_id AND s.org_id = l.org_id
           )
    """
)


async def microsoft_calendar_sweep_outbox(ctx: dict) -> None:  # noqa: ARG001
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
                    select(MicrosoftCalendarEventLink.id).where(
                        MicrosoftCalendarEventLink.org_id == org.id,
                        MicrosoftCalendarEventLink.status.in_(
                            [LinkStatus.PENDING.value, LinkStatus.DELETE_PENDING.value]
                        ),
                        MicrosoftCalendarEventLink.attempts < MAX_ATTEMPTS,
                    )
                )
            )
            .scalars()
            .all()
        )
        for link_id in links:
            await enqueue("microsoft_calendar_push_link", str(org.id), str(link_id))

    await run_per_org(_sweep)


# --------------------------------------------------------------------------- #
# Worker functions (ModuleDescriptor.worker_functions)
# --------------------------------------------------------------------------- #
async def microsoft_calendar_sync_connection(ctx: dict, org_id: str, connection_id: str) -> str:  # noqa: ARG001
    if not await _licensed():
        return "unlicensed"
    async with async_session_maker() as session:
        oid = uuid.UUID(org_id)
        await set_current_org(session, oid)
        from app.core.models import Org

        org = await session.get(Org, oid)
        connection = await session.scalar(
            select(MicrosoftConnection).where(
                MicrosoftConnection.org_id == oid,
                MicrosoftConnection.id == uuid.UUID(connection_id),
            )
        )
        if org is None or connection is None:
            return "gone"
        await sync_connection(session, org, connection)
        await session.commit()
    return "synced"


async def microsoft_calendar_push_link(ctx: dict, org_id: str, link_id: str) -> str:  # noqa: ARG001
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
            select(MicrosoftCalendarEventLink).where(
                MicrosoftCalendarEventLink.org_id == oid, MicrosoftCalendarEventLink.id == lid
            )
        )
        if org is None or link is None:
            return "gone"
        await push_link(session, org, link)
        await session.commit()
    return "pushed"
