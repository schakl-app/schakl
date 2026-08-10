"""ARQ jobs for reporting (issue #300): the schedule, and the run itself.

* ``reporting_tick`` — hourly, per org. Finds the profiles whose local day and hour have come
  round and enqueues **one job per client**. Hourly rather than daily because the hour is a
  per-org setting and the worker's clock is UTC: a tenant in Lisbon and one in Warsaw asking
  for 08:00 mean two different instants.
* ``reporting_run_report`` — gathers, snapshots, narrates, renders, and (if the profile says
  so) sends. One report per job.
* ``reporting_reap_stale_runs`` — every quarter of an hour, per org. Fails the runs that are
  in flight with nobody flying them.

**A status a process owns needs a process-independent way back.** ``generating`` says "a worker
has this", and the row itself cannot tell the difference between a worker that is busy and a
worker that was restarted, OOM-killed, or shut down between the flush and the first ``await``.
Every in-process guard — ``run_report``'s ``except BaseException``, the model call's own
timeout, the API's write-back when nothing queued — narrows the window and none of them closes
it, because the failure mode is *the process is not there any more*. The reaper is the answer
that does not run in the process it is answering for.

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
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
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
    from app.modules.reporting.runner import run_job_id, schedule_report

    async with async_session_maker() as session:
        org = await _active_org(session, org_id)
        if org is None:
            return
        await set_current_org(session, org.id)
        try:
            scheduled = await schedule_report(
                session, org, uuid.UUID(company_id), audience
            )
            # Committed here rather than after the enqueue below, for the reason the request
            # path commits early too: the run job opens its own session, and a row it cannot
            # see yet is a run it silently declines to do.
            await session.commit()
        except Exception:
            logger.exception("reporting: could not schedule %s/%s", company_id, audience)
            await session.rollback()
            return
    if scheduled is None:
        return
    report_id, started_at = scheduled
    if await enqueue(
        "reporting_run_report", org_id, str(report_id), _job_id=run_job_id(report_id, started_at)
    ) is None:
        # The row now claims a worker has it and none does; the reaper would eventually say so,
        # but twenty minutes of "bezig met genereren" for something we know right now is worse.
        logger.warning("reporting: run for %s was not queued; failing it", report_id)
        await _fail_unqueued(org_id, report_id)


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


async def _fail_unqueued(org_id: str, report_id: uuid.UUID) -> None:
    """Undo a ``generating`` nothing is going to act on. Best effort; the reaper is the backstop."""
    async with async_session_maker() as session:
        org = await _active_org(session, org_id)
        if org is None:
            return
        await set_current_org(session, org.id)
        report = await session.scalar(
            select(Report).where(Report.org_id == org.id, Report.id == report_id)
        )
        if report is None or report.status != ReportStatus.GENERATING.value:
            return
        report.status = ReportStatus.FAILED.value
        report.warnings = [
            *(report.warnings or []),
            {"code": "reporting.warning.not_queued", "detail": ""},
        ]
        await session.commit()


async def _reap_org(org: Org, session: AsyncSession) -> None:
    """Fail this org's runs that have been ``generating`` longer than a run can possibly take.

    ``COALESCE(generation_started_at, updated_at)`` on purpose: reports generated before that
    column existed carry ``NULL``, and those are exactly the ones stuck right now. Reading
    ``updated_at`` for them is what makes the first tick after the upgrade clean up the backlog
    instead of leaving it to a hand-written ``UPDATE``.
    """
    from app.modules.reporting.runner import STALE_RUN_SECONDS

    cutoff = datetime.now(UTC) - timedelta(seconds=STALE_RUN_SECONDS)
    stale = (
        await session.execute(
            select(Report).where(
                Report.org_id == org.id,
                Report.status == ReportStatus.GENERATING.value,
                func.coalesce(Report.generation_started_at, Report.updated_at) < cutoff,
            )
        )
    ).scalars().all()
    for report in stale:
        report.status = ReportStatus.FAILED.value
        report.warnings = [
            *(report.warnings or []),
            {"code": "reporting.warning.run_timeout", "detail": ""},
        ]
    if stale:
        logger.warning(
            "reporting: reaped %d stale run(s) for org %s", len(stale), org.slug
        )


async def reporting_reap_stale_runs(ctx: dict) -> None:  # noqa: ARG001
    """Quarter-hourly ARQ entrypoint for the sweep above.

    **Deliberately not licence-gated.** The other two jobs write new work and must stand down
    when a licence lapses (#140); this one only corrects a status the platform itself set and
    then failed to finish. Refusing to do that would leave an unlicensed tenant staring at
    "bezig met genereren" until they renewed — punishing them for our crash.
    """
    await run_per_org(_reap_org)


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
    "reporting_reap_stale_runs",
    "reporting_run_report",
    "reporting_schedule_report",
    "reporting_tick",
]
