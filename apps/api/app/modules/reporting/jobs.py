"""ARQ jobs for reporting (issue #300): the schedule, and the run itself.

* ``reporting_tick`` — hourly, per org. Finds the profiles whose local day and hour have come
  round and enqueues **one job per client**. Hourly rather than daily because the hour is a
  per-org setting and the worker's clock is UTC: a tenant in Lisbon and one in Warsaw asking
  for 08:00 mean two different instants.
* ``reporting_run_report`` — gathers, snapshots, narrates, renders, and (if the profile says
  so) sends. One report per job.

**One job per client, never a loop.** The workflow this replaces ran thirty clients inside one
execution, so a single SE Ranking timeout took the whole month's reporting with it. Here each
client fails alone, keeps its own ``failed`` status and its own warnings, and is retried by
pressing one button.

**The licence gate is repeated here on purpose.** The router's write gate covers requests; a
cron writes on a schedule and would sail straight past it. Same reasoning, same shape as
``marketing/jobs.py``'s ``_licensed()``.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.entitlements.service import license_state
from app.core.jobs import enqueue, run_per_org
from app.core.models import Org, OrgStatus
from app.core.timezone import org_zoneinfo
from app.db import async_session_maker, set_current_org
from app.modules.reporting.models import (
    Report,
    ReportAudience,
    ReportCadence,
    ReportDelivery,
    ReportProfile,
    ReportStatus,
)

logger = logging.getLogger("schakl.reporting")


async def _licensed() -> bool:
    return (await license_state()).writable("reporting")


async def _due(session: AsyncSession, org: Org) -> list[tuple[ReportProfile, dict]]:
    """The profiles whose scheduled moment is now, in the **org's** own calendar."""
    from app.modules.reporting import seeds
    from app.modules.reporting.models import ReportingSettings

    zone = await org_zoneinfo(session, org.id)
    now = datetime.now(zone)
    org_row = await session.scalar(
        select(ReportingSettings).where(ReportingSettings.org_id == org.id)
    )
    org_schedule = {**seeds.DEFAULT_SCHEDULE, **((org_row.schedule if org_row else None) or {})}
    profiles = (
        await session.execute(
            select(ReportProfile).where(
                ReportProfile.org_id == org.id, ReportProfile.active.is_(True)
            )
        )
    ).scalars().all()
    due: list[tuple[ReportProfile, dict]] = []
    for profile in profiles:
        schedule = {**org_schedule, **(profile.schedule or {})}
        cadence = str(schedule.get("cadence") or ReportCadence.MONTHLY.value)
        if cadence == ReportCadence.OFF.value:
            continue
        if cadence == ReportCadence.QUARTERLY.value and now.month not in (1, 4, 7, 10):
            continue
        if now.day != int(schedule.get("day_of_month") or 5):
            continue
        if now.hour != int(schedule.get("hour") or 8):
            continue
        due.append((profile, schedule))
    return due


async def _tick_org(org: Org, session: AsyncSession) -> None:
    stamp = f"{datetime.now(UTC):%Y%m%d%H}"
    for profile, _schedule in await _due(session, org):
        for audience in _audiences(profile):
            # A deterministic job id: an hourly tick that overlaps its predecessor, or a
            # worker restarted mid-hour, must not enqueue a client's report twice.
            await enqueue(
                "reporting_schedule_report",
                str(org.id),
                str(profile.company_id),
                audience,
                _job_id=f"reporting-schedule-{profile.company_id}-{audience}-{stamp}",
            )
    logger.debug("reporting: tick complete for org %s", org.slug)


def _audiences(profile: ReportProfile) -> list[str]:
    audiences = [ReportAudience.CLIENT.value]
    if profile.internal_enabled:
        audiences.append(ReportAudience.INTERNAL.value)
    return audiences


async def reporting_tick(ctx: dict) -> None:  # noqa: ARG001
    """Hourly ARQ entrypoint."""
    if not await _licensed():
        return
    await run_per_org(_tick_org)


async def reporting_schedule_report(
    ctx: dict, org_id: str, company_id: str, audience: str  # noqa: ARG001
) -> None:
    """Create this month's run for one client and hand it to the generator.

    Split from ``reporting_run_report`` so the *scheduling* decision (which period, which
    template) is made once, in a transaction of its own, and a generation that fails can be
    retried without re-deciding it.
    """
    if not await _licensed():
        return
    from app.modules.reporting.runner import schedule_report

    async with async_session_maker() as session:
        org = await _active_org(session, org_id)
        if org is None:
            return
        await set_current_org(session, org.id)
        try:
            report_id = await schedule_report(
                session, org, uuid.UUID(company_id), audience
            )
            await session.commit()
        except Exception:
            logger.exception("reporting: could not schedule %s/%s", company_id, audience)
            await session.rollback()
            return
    if report_id is not None:
        await enqueue(
            "reporting_run_report",
            org_id,
            str(report_id),
            _job_id=f"reporting-run-{report_id}",
        )


async def reporting_run_report(ctx: dict, org_id: str, report_id: str) -> None:  # noqa: ARG001
    """Gather, snapshot, narrate, render — and send when the profile says to."""
    if not await _licensed():
        return
    from app.modules.reporting.runner import run_report

    async with async_session_maker() as session:
        org = await _active_org(session, org_id)
        if org is None:
            return
        await set_current_org(session, org.id)
        await run_report(session, org, uuid.UUID(report_id))


async def _active_org(session: AsyncSession, org_id: str) -> Org | None:
    return await session.scalar(
        select(Org).where(
            Org.id == uuid.UUID(org_id), Org.status == OrgStatus.ACTIVE.value
        )
    )


__all__ = [
    "ReportDelivery",
    "ReportStatus",
    "Report",
    "reporting_run_report",
    "reporting_schedule_report",
    "reporting_tick",
]
