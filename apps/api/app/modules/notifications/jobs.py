"""Background delivery of external notifications (#17).

The fan-out writes ``notification_deliveries`` rows inside the request transaction; this cron
pushes them to the provider off the hot path, per org (RLS bound), with exponential backoff and a
bounded attempt count. A failure lands back on the row as ``last_error`` for the UI to surface and
re-drive.

Both sweeps are **digest sweeps** (#283): each sends one message per group of due rows rather than
one per row. E-mail groups by recipient, external transports by channel — a shared room has no
single recipient, so grouping it by user would be meaningless. A channel on the ``immediate``
cadence therefore bundles whatever accumulated within one tick, which is a group of one in
practice and exactly how personal e-mail has behaved since #17.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.jobs import run_per_org
from app.core.models import Org
from app.modules.notifications.external import (
    dispatch_email_deliveries,
    dispatch_external_deliveries,
)
from app.modules.notifications.webpush import dispatch_webpush_deliveries

logger = logging.getLogger("schakl.notifications")


async def _dispatch_for_org(org: Org, session: AsyncSession) -> None:
    # Slack / Teams / Discord / webhook: grouped per channel, one message per sweep (#283).
    await dispatch_external_deliveries(session, org)
    # Personal e-mail rides its own path: grouped per recipient, one mail per sweep (#17).
    await dispatch_email_deliveries(session, org)
    # Browser push: grouped per recipient like e-mail, then fanned out to that person's own
    # devices at send time — the cadence is theirs, the devices are how you reach them (#309).
    await dispatch_webpush_deliveries(session, org)


async def dispatch_notification_deliveries(ctx: dict) -> None:
    """ARQ entrypoint: push all pending external deliveries, every org, with backoff."""
    await run_per_org(_dispatch_for_org)
