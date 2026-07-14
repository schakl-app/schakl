"""ARQ jobs for the marketing sync (#133).

- ``marketing_sync_all`` — the nightly cron, fanned out per active org via ``run_per_org`` (RLS
  GUC bound per tenant, one transaction each). It re-pulls a **trailing window** for every active
  link and upserts: GSC finalizes 2-3 days late and GA4/Ads attribution keeps moving for a few
  days, so re-pulling the last week and upserting lets late data self-heal.
- ``marketing_sync_link`` — a one-off single-link trailing sync (enqueued on demand).
- ``marketing_backfill_link`` — the 13-month backfill kicked off when a link is first created, so
  sparklines and year-over-year work from day one. Chunked by month and committed per chunk so a
  failure keeps the progress it made.

Every link syncs independently: ``sync_link_range`` swallows its own errors and records them on
the link, so one broken connection never stops the other links' sync.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.entitlements.service import license_state
from app.core.jobs import enqueue, run_per_org
from app.core.models import Org, OrgStatus
from app.core.timezone import org_zoneinfo
from app.db import async_session_maker, set_current_org
from app.modules.marketing.models import MarketingLink
from app.modules.marketing.service import sync_link_range

logger = logging.getLogger("schakl.marketing")


async def _licensed() -> bool:
    """Whether the ``marketing`` sku is still writable (issue #137): the mount-time 402 gate
    covers requests, but crons write on a schedule — an expired license must stop the background
    sync too (expired = read-only, not gone; the panel/tab/overview keep reading synced data)."""
    return (await license_state()).writable("marketing")

#: How many trailing days the nightly run re-pulls (covers GSC's 2-3 day finalization lag and a
#: few days of GA4/Ads attribution drift).
_TRAILING_DAYS = 7
#: ~13 months, so a first backfill spans a full year plus the current partial month.
_BACKFILL_DAYS = 400
_CHUNK_DAYS = 30


async def _org_today(session: AsyncSession, org: Org):
    zone = await org_zoneinfo(session, org.id)
    return datetime.now(zone).date()


async def _sync_org(org: Org, session: AsyncSession) -> None:
    today = await _org_today(session, org)
    end = today - timedelta(days=1)
    start = end - timedelta(days=_TRAILING_DAYS - 1)
    links = (
        (
            await session.execute(
                select(MarketingLink).where(
                    MarketingLink.org_id == org.id, MarketingLink.active.is_(True)
                )
            )
        )
        .scalars()
        .all()
    )
    resumed = 0
    for link in links:
        if not link.backfill_done:
            # An incomplete backfill (e.g. one interrupted before the RLS-GUC fix, or halted on a
            # since-fixed connection error) resumes as its own chunked job — which also covers the
            # trailing window, so skip the direct sync. The deterministic job id dedups, so a
            # backfill already queued/running is not piled onto every night.
            try:
                await enqueue(
                    "marketing_backfill_link",
                    str(org.id),
                    str(link.id),
                    _job_id=f"marketing-backfill-{link.id}",
                )
                resumed += 1
            except Exception:
                logger.warning("could not enqueue backfill resume for link %s", link.id)
            continue
        await sync_link_range(session, org, link, start, end)
    if links:
        logger.info(
            "marketing: synced %s links for org %s (%s backfills resumed)",
            len(links),
            org.slug,
            resumed,
        )


async def marketing_sync_all(ctx: dict) -> None:
    """Nightly ARQ entrypoint: re-pull the trailing window for every active org's links."""
    if not await _licensed():
        return
    await run_per_org(_sync_org)


async def _load_org_and_link(
    session: AsyncSession, org_id: str, link_id: str
) -> tuple[Org, MarketingLink] | None:
    org = (
        await session.execute(
            select(Org).where(
                Org.id == uuid.UUID(org_id), Org.status == OrgStatus.ACTIVE.value
            )
        )
    ).scalar_one_or_none()
    if org is None:
        return None
    await set_current_org(session, org.id)
    link = (
        await session.execute(
            select(MarketingLink).where(
                MarketingLink.org_id == org.id, MarketingLink.id == uuid.UUID(link_id)
            )
        )
    ).scalar_one_or_none()
    if link is None:
        return None
    return org, link


async def marketing_sync_link(ctx: dict, org_id: str, link_id: str) -> None:
    """One-off trailing sync for a single link. A missing org/link is a quiet no-op."""
    if not await _licensed():
        return
    async with async_session_maker() as session:
        loaded = await _load_org_and_link(session, org_id, link_id)
        if loaded is None:
            return
        org, link = loaded
        today = await _org_today(session, org)
        end = today - timedelta(days=1)
        start = end - timedelta(days=_TRAILING_DAYS - 1)
        await sync_link_range(session, org, link, start, end)
        await session.commit()


async def marketing_backfill_link(ctx: dict, org_id: str, link_id: str) -> None:
    """13-month backfill for a freshly linked property, chunked by month (#133).

    Committed per chunk so a failure mid-way keeps the days it already fetched; ``backfill_done``
    flips only after the whole span lands, which is what the panel's "eerste synchronisatie loopt"
    state reads.
    """
    if not await _licensed():
        return
    async with async_session_maker() as session:
        loaded = await _load_org_and_link(session, org_id, link_id)
        if loaded is None:
            return
        org, link = loaded
        if link.backfill_done:
            return
        today = await _org_today(session, org)
        end = today - timedelta(days=1)
        window_start = end - timedelta(days=_BACKFILL_DAYS - 1)
        chunk_start = window_start
        while chunk_start <= end:
            # The RLS GUC is transaction-local (``set_config(..., is_local=true)``), so the
            # previous chunk's commit cleared it. Re-bind before this chunk's reads, its upsert
            # and its commit — otherwise an RLS-scoped write on ``marketing_links`` matches zero
            # rows and SQLAlchemy raises StaleDataError (the whole job then crashes and retries).
            await set_current_org(session, org.id)
            chunk_end = min(chunk_start + timedelta(days=_CHUNK_DAYS - 1), end)
            await sync_link_range(session, org, link, chunk_start, chunk_end)
            await session.commit()
            # A persistent auth/config error (revoked grant, no OAuth client, missing scope,
            # Ads token) won't fix itself across chunks — stop, leave ``backfill_done`` False and
            # ``last_error`` visible (health = "error"). The nightly sync resumes automatically
            # once the connection is fixed; a full re-backfill is a relink away.
            if link.last_error:
                logger.info("marketing backfill halted (%s) for link %s", link.last_error, link.id)
                return
            chunk_start = chunk_end + timedelta(days=1)
        await set_current_org(session, org.id)
        link.backfill_done = True
        await session.commit()
        logger.info("marketing backfill complete for link %s (org %s)", link.id, org.slug)
