"""Background delivery of external notifications (#17).

The fan-out writes ``notification_deliveries`` rows inside the request transaction; this cron
pushes them to the provider off the hot path, per org (RLS bound), with exponential backoff and a
bounded attempt count. A failure lands back on the row as ``last_error`` for the UI to surface and
re-drive.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.jobs import run_per_org
from app.core.models import Org
from app.modules.notifications.external import MAX_ATTEMPTS, dispatch_delivery
from app.modules.notifications.models import NotificationDelivery

logger = logging.getLogger("schakl.notifications")

#: Cap the batch so one org's backlog can't monopolise a worker tick.
_BATCH = 100


async def _dispatch_for_org(org: Org, session: AsyncSession) -> None:
    rows = (
        (
            await session.execute(
                select(NotificationDelivery)
                .where(
                    NotificationDelivery.org_id == org.id,
                    NotificationDelivery.status == "pending",
                    NotificationDelivery.attempts < MAX_ATTEMPTS,
                )
                .order_by(NotificationDelivery.created_at.asc())
                .limit(_BATCH)
            )
        )
        .scalars()
        .all()
    )
    for delivery in rows:
        await dispatch_delivery(session, delivery)


async def dispatch_notification_deliveries(ctx: dict) -> None:
    """ARQ entrypoint: push all pending external deliveries, every org, with backoff."""
    await run_per_org(_dispatch_for_org)
