"""ARQ jobs for the marketing sync (#133).

- ``marketing_sync_all`` — the nightly cron, fanned out per active org via ``run_per_org`` (RLS
  GUC bound per tenant, one transaction each). It re-pulls a **trailing window** for every active
  link and upserts: GSC finalizes 2-3 days late and GA4/Ads attribution keeps moving for a few
  days, so re-pulling the last week and upserting lets late data self-heal.
- ``marketing_sync_link`` — a one-off single-link trailing sync (enqueued on demand).
- ``marketing_backfill_link`` — the 13-month backfill kicked off when a link is first created, so
  sparklines and year-over-year work from day one. Chunked by month and committed per chunk so a
  failure keeps the progress it made.
- ``marketing_warm_dashboards`` — the nightly warm of the marketing tab (docs/MARKETING.md): the
  leads dashboard and the drill-down tables are live Google reads behind a day-long Redis key,
  so the first open of every view paid Google's latency. The warm reads each client's presets,
  single-value filters and drill-downs once, before anyone is at their desk;
  ``marketing_warm_company`` is the same for one client, enqueued when its profile changes (a
  changed role changes the requests, and with them the key).

Every link syncs independently: ``sync_link_range`` swallows its own errors and records them on
the link, so one broken connection never stops the other links' sync.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.entitlements.service import sku_cron_enabled
from app.core.jobs import enqueue, run_per_org, system_context
from app.core.models import Org, OrgStatus
from app.core.timezone import org_zoneinfo
from app.db import async_session_maker, set_current_org
from app.errors import AppError
from app.modules.marketing.models import MarketingCompanySettings, MarketingLink
from app.modules.marketing.service import sync_link_range

logger = logging.getLogger("schakl.marketing")


async def _licensed() -> bool:
    """Whether the ``marketing`` sku is still writable (issue #137): the mount-time 402 gate
    covers requests, but crons write on a schedule — an expired license must stop the background
    sync too (expired = read-only, not gone; the panel/tab/overview keep reading synced data)."""
    return await sku_cron_enabled("marketing")

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


# --- the dashboard's warm (docs/MARKETING.md) ------------------------------------------------ #

#: The rolling presets the dashboard's tab row offers — the same list as the web's
#: ``PERIOD_PRESETS`` (``yoy`` there is this list's ``365d``). Each is a different set of
#: requests and therefore a different key; a named month or quarter is not warmed, since a
#: picker's option list is open-ended and its first reader is the person who chose it.
LEADS_WARM_PERIODS: tuple[str, ...] = ("30d", "90d", "month", "last_month", "quarter", "365d")
#: The period the tab opens on: the one whose single-value filters and drill-down tables are
#: warmed too. The other presets warm the leads dashboard only — a drill-down table is one
#: Google read per kind per link, and eleven of those six times over for every client every
#: night is a quota spent on tabs most mornings nobody opens.
WARM_DEFAULT_PERIOD = "30d"


async def _warmable_companies(session: AsyncSession, org: Org) -> list[uuid.UUID]:
    """Every client with a measurement profile or an active link — the ones with a tab."""
    profiled = await session.execute(
        select(MarketingCompanySettings.company_id).where(
            MarketingCompanySettings.org_id == org.id,
            MarketingCompanySettings.lead_profile.is_not(None),
        )
    )
    linked = await session.execute(
        select(MarketingLink.company_id)
        .where(MarketingLink.org_id == org.id, MarketingLink.active.is_(True))
        .distinct()
    )
    return list(dict.fromkeys([*profiled.scalars(), *linked.scalars()]))


async def warm_company(org: Org, session: AsyncSession, company_id: uuid.UUID) -> int:
    """Read one client's marketing tab the way a person would, so that every key lands in Redis:
    the leads dashboard for every preset and, on the default preset, once per single-value
    filter; and every drill-down table of every active link on the default preset. Returns how
    many reads were made.

    Through the services themselves — the keys are theirs, computed from the exact requests
    they send, and a second copy of that computation here is how a warm comes to fill a key
    nobody reads. A view already in Redis costs nothing (the service takes it from there), so
    re-running this is cheap. Only single-value filters: the options a reader is offered are
    the period's own values, and one click on one chip is the common case; a second chip on a
    warm view still costs the reader one read, which is the cold path's price rather than the
    warm's problem. A refusal from Google is the service's to record on the answer it returns;
    a drill-down the client's layout hides is a 422 and simply skipped. Nothing here raises
    past one client.
    """
    from app.modules.marketing.leads.service import LeadsService  # noqa: PLC0415 — flat graph
    from app.modules.marketing.service import MarketingService  # noqa: PLC0415
    from app.modules.marketing.sources import source_for  # noqa: PLC0415

    ctx = system_context(org, session)
    reads = 0

    leads = LeadsService(ctx)
    for period in LEADS_WARM_PERIODS:
        dashboard = await leads.dashboard(company_id, period, {})
        reads += 1
        if not dashboard.configured:
            break
        if period != WARM_DEFAULT_PERIOD:
            continue
        for control in dashboard.filters:
            for option in control.options:
                await leads.dashboard(company_id, period, {control.dimension: [option.key]})
                reads += 1

    marketing = MarketingService(ctx)
    links = (
        (
            await session.execute(
                select(MarketingLink).where(
                    MarketingLink.org_id == org.id,
                    MarketingLink.company_id == company_id,
                    MarketingLink.active.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    for link in links:
        for kind in source_for(link.source).drilldowns:
            try:
                await marketing.drilldown(company_id, link.id, kind, 30, WARM_DEFAULT_PERIOD)
            except AppError as exc:
                if exc.status_code != 422:
                    raise
                continue  # hidden by this client's layout: no table, no key
            reads += 1
    return reads


async def _warm_dashboards_org(org: Org, session: AsyncSession) -> None:
    companies = await _warmable_companies(session, org)
    reads = 0
    for company_id in companies:
        try:
            reads += await warm_company(org, session, company_id)
        except Exception:  # noqa: BLE001 — one client's failure must not cool the next
            logger.exception("marketing warm failed for company %s (org %s)", company_id, org.slug)
    if companies:
        logger.info(
            "marketing: warmed the tab for %s clients in %s reads (org %s)",
            len(companies),
            reads,
            org.slug,
        )


async def marketing_warm_dashboards(ctx: dict) -> None:
    """Nightly ARQ entrypoint, after the sync: fill the day's keys for every client's tab."""
    if not await _licensed():
        return
    await run_per_org(_warm_dashboards_org)


async def marketing_warm_company(ctx: dict, org_id: str, company_id: str) -> None:
    """One client's warm, enqueued when its measurement profile is saved."""
    if not await _licensed():
        return
    async with async_session_maker() as session:
        org = await session.get(Org, uuid.UUID(org_id))
        if org is None or org.status != OrgStatus.ACTIVE.value:
            return
        await set_current_org(session, org.id)
        try:
            await warm_company(org, session, uuid.UUID(company_id))
        except Exception:  # noqa: BLE001
            logger.exception("marketing warm failed for company %s (org %s)", company_id, org.slug)
