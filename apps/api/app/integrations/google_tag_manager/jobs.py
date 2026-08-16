"""The nightly observation. Business-licensed — see LICENSE.

One job, and what it refreshes is the answer to *"is this client's tracking still what we think it
is"*: the container's name, which version is live, how many tags are in it, and whether somebody
has left changes staged and unpublished.

That last one is the reason the job exists rather than leaving everything to a screen. A container
is edited by us, by the client's own marketeer and by whoever set it up in 2019, and the failure an
agency actually meets is silent: a change staged in a workspace weeks ago that nobody published, so
the thing the client was told is measured is not being measured. Nothing surfaces that except
looking, and nobody looks at a container they have no reason to open.

It runs at **05:35**, after ``marketing`` (04:45) and ``google_ads`` (05:15): all three walk every
org making outbound Google calls, and stacking them on one minute is how a box with thirty clients
meets its own rate limits at four in the morning.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.entitlements.service import sku_cron_enabled
from app.core.jobs import run_per_org, system_context
from app.core.models import Org
from app.integrations.google_tag_manager.service import GtmService

logger = logging.getLogger("schakl.gtm")


async def _sync_org(org: Org, session: AsyncSession) -> None:
    ctx = system_context(org, session)
    service = GtmService(ctx)
    containers = await service.list_containers(active_only=True)
    if not containers:
        return
    failed = 0
    for container in containers:
        try:
            # ``observe`` records its own failure on the row and returns it, so an unreachable
            # container costs one red line rather than the other nineteen containers' refresh.
            row = await service.observe(container.id)
        except Exception:  # noqa: BLE001 — one container must never end the loop
            logger.exception("gtm: observe failed for container %s", container.id)
            failed += 1
            continue
        if row.status != "active":
            failed += 1
    logger.info(
        "gtm: observed %s container(s) for org %s (%s failed)",
        len(containers) - failed,
        org.slug,
        failed,
    )


async def gtm_sync_all(ctx: dict) -> None:
    """Nightly entrypoint: refresh what we mirror about every active org's containers.

    The licence check is the cron's own, because the mount-time 402 only covers requests. Expired
    means **read-only, not gone**: what is already on the row keeps rendering, and only the
    refreshing of it stops.
    """
    if not await sku_cron_enabled("google_tag_manager"):
        return
    await run_per_org(_sync_org)
