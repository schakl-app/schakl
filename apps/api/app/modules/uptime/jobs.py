"""Background work for the uptime module (docs/UPTIME.md §16).

One job, and it exists because the heartbeat table is a **bounded rolling window** rather than
a warehouse. Uptime Kuma keeps the real history and answers questions about it better than a
mirror would; what we hold is what a panel and a report section draw. Left unpruned, a
five-second monitor writes seventeen thousand rows a day per client and the panel that reads
them gets slower every week.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.jobs import run_per_org
from app.core.models import Org
from app.modules.uptime.models import UptimeHeartbeat

logger = logging.getLogger("schakl.uptime")

#: How much history the mirror keeps. Ninety days covers a quarterly report's window with room
#: to spare, and is short enough that the table stays a window rather than becoming a liability
#: nobody decided to take on.
RETENTION_DAYS = 90


async def prune_heartbeats() -> None:
    """Drop heartbeats older than the retention window, per org.

    ``run_per_org`` binds the RLS GUC and gives each org its own transaction, so one tenant's
    failure never strands another's prune (CLAUDE.md §6).
    """
    await run_per_org(_prune_org)


async def _prune_org(org: Org, session: AsyncSession) -> None:
    cutoff = datetime.now(UTC) - timedelta(days=RETENTION_DAYS)
    result = await session.execute(
        delete(UptimeHeartbeat).where(
            UptimeHeartbeat.org_id == org.id, UptimeHeartbeat.observed_at < cutoff
        )
    )
    if result.rowcount:
        logger.info("uptime: pruned %s heartbeats for org %s", result.rowcount, org.slug)


async def latest_states(session: AsyncSession, org_id, monitor_ids: list) -> dict:
    """The newest heartbeat per monitor, in **one** query.

    ``DISTINCT ON`` rather than a subquery per monitor: a company panel folding forty monitors
    is exactly the endpoint that is one query at three rows and one-per-row at three hundred,
    and the JSON looks identical either way (docs/PERFORMANCE.md).
    """
    if not monitor_ids:
        return {}
    stmt = (
        select(UptimeHeartbeat)
        .where(UptimeHeartbeat.org_id == org_id, UptimeHeartbeat.monitor_id.in_(monitor_ids))
        .distinct(UptimeHeartbeat.monitor_id)
        .order_by(UptimeHeartbeat.monitor_id, UptimeHeartbeat.observed_at.desc())
    )
    return {row.monitor_id: row for row in (await session.execute(stmt)).scalars().all()}
