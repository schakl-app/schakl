"""Monday morning: ``time.timesheet_reminder`` for anyone who logged nothing last week.

A timesheet nobody filled in is invisible until invoicing, which is far too late. This runs on
Monday and nudges the staff whose previous ISO week is empty — once, keyed on the week, so a
re-run (or a worker restart) never nags twice.

"Staff" is every membership that is not a ``client``: clients do not log time. A timesheet has
no row of its own, so the *person* is the subject of the event and the week rides in the payload.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import distinct, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import SystemContext, emit
from app.core.models import Org
from app.core.permissions.service import permission_holder_ids
from app.core.timezone import org_zoneinfo, resolve_zoneinfo
from app.modules.time.models import TimeEntry

# No module-level zone (CLAUDE.md §8): ``remind_for_org`` resolves each org's own, and an
# omitted ``tz`` falls back to the *configured* instance default read per call — freezing
# `settings` into an import-time constant is the same mistake one step smaller. "Last week" is a
# local-calendar span, so a cloud tenant east of us must not be nudged on our Monday.


def previous_week_start(today: date | None = None, tz: ZoneInfo | None = None) -> date:
    """The Monday of the ISO week before ``today`` (local, because the cron fires in UTC)."""
    tz = tz if tz is not None else resolve_zoneinfo(None)
    today = today or datetime.now(tz).date()
    this_monday = today - timedelta(days=today.weekday())
    return this_monday - timedelta(days=7)


def _week_bounds(week_start: date, tz: ZoneInfo | None = None) -> tuple[datetime, datetime]:
    """The UTC instants the local week opens and closes — DST-correct, unlike ``+7 days`` in UTC."""
    tz = tz if tz is not None else resolve_zoneinfo(None)
    start = datetime.combine(week_start, time.min, tzinfo=tz).astimezone(UTC)
    end = datetime.combine(week_start + timedelta(days=7), time.min, tzinfo=tz).astimezone(UTC)
    return start, end


async def _fully_on_leave(
    org: Org, session: AsyncSession, candidates: set[uuid.UUID], week_start: date
) -> set[uuid.UUID]:
    """Who among ``candidates`` had every scheduled minute of the week covered by approved
    leave or a holiday. They logged nothing *by design* — the module's own yardstick everywhere
    else is "the schedule minus approved leave" (§14), and nudging someone the Monday after
    their vacation says the timesheet forgot they were on it.

    Through the leave module's service under a system context — the same seam its own December
    crons use — and lazily imported like every cross-module service call. One requests query
    for all candidates; the per-day arithmetic only runs for the few who actually had leave.
    """
    from app.core.jobs import system_context
    from app.modules.leave import schedule as sched
    from app.modules.leave.models import LeaveRequest, LeaveRequestStatus
    from app.modules.leave.service import LeaveService

    week_end = week_start + timedelta(days=6)
    rows = (
        await session.execute(
            select(LeaveRequest).where(
                LeaveRequest.org_id == org.id,
                LeaveRequest.user_id.in_(candidates),
                LeaveRequest.status == LeaveRequestStatus.APPROVED.value,
                LeaveRequest.start_date <= week_end,
                LeaveRequest.end_date >= week_start,
            )
        )
    ).scalars().all()
    if not rows:
        return set()
    by_user: dict[uuid.UUID, list[LeaveRequest]] = {}
    for row in rows:
        by_user.setdefault(row.user_id, []).append(row)

    service = LeaveService(system_context(org, session))
    holidays_off = await service.active_holidays_between(week_start, week_end)
    covered: set[uuid.UUID] = set()
    for user_id, requests in by_user.items():
        resolver = await service.schedule_resolver(user_id)
        fully = True
        for offset in range(7):
            day = week_start + timedelta(days=offset)
            work_day = resolver(day).day(day.weekday())
            scheduled = 0 if day in holidays_off else sched.day_minutes(work_day)
            if scheduled <= 0:
                continue
            # A request's boundary times only bind on its boundary days (#48) — a middle day
            # is covered whole. The same intersect `compute_hours` prices with.
            leave = 0
            for request in requests:
                if not request.start_date <= day <= request.end_date:
                    continue
                low = (
                    sched.to_minutes(request.start_time)
                    if request.start_date == day and request.start_time
                    else 0
                )
                high = (
                    sched.to_minutes(request.end_time)
                    if request.end_date == day and request.end_time
                    else sched.MINUTES_PER_DAY
                )
                leave += sched.day_minutes(work_day, (low, high))
            if leave < scheduled:
                fully = False
                break
        if fully:
            covered.add(user_id)
    return covered


async def remind_for_org(org: Org, session: AsyncSession, *, week_start: date | None = None) -> int:
    """Nudge every staff member with an empty previous week.

    Returns the number of *candidates announced*, not notifications delivered: a re-run
    announces the same people and the notifications module drops the repeat on its dedup key.
    """
    tz = await org_zoneinfo(session, org.id)
    week_start = week_start or previous_week_start(tz=tz)
    start, end = _week_bounds(week_start, tz)

    # Who is expected to log hours: whoever may write a time entry (issue #19). One indexed
    # query, DISTINCT — a user holding two granting roles must not be reminded twice.
    staff = set(
        (await session.execute(permission_holder_ids(org.id, "time.entry.write"))).scalars()
    )
    logged = set(
        (
            await session.execute(
                select(distinct(TimeEntry.user_id)).where(
                    TimeEntry.org_id == org.id,
                    TimeEntry.started_at >= start,
                    TimeEntry.started_at < end,
                )
            )
        ).scalars()
    )

    candidates = staff - logged
    if candidates:
        # An empty week that was entirely approved leave (or holidays) is not a missing
        # timesheet — it is a vacation, and the reminder must know the difference.
        candidates -= await _fully_on_leave(org, session, candidates, week_start)

    ctx = SystemContext(org=org, session=session)
    for user_id in sorted(candidates):
        await emit(
            "time.timesheet_reminder",
            ctx,
            {
                "user_id": user_id,
                "week_start": week_start,
                "_recipients": [user_id],
                "_dedup_key": f"time.timesheet_reminder:{user_id}:{week_start.isoformat()}",
            },
        )
    return len(candidates)


async def send_timesheet_reminders(ctx: dict) -> int:
    """ARQ cron entry point: last week's empty timesheets, for every org."""
    from app.core.entitlements.service import license_state
    from app.core.jobs import run_per_org

    # Licensed module (issue #137): the mount-time 402 gate covers requests, but crons write
    # on a schedule — an expired license must stop the background half too.
    if not (await license_state()).writable("time"):
        return 0

    total = 0

    async def _per_org(org: Org, session: AsyncSession) -> None:
        nonlocal total
        total += await remind_for_org(org, session)

    await run_per_org(_per_org)
    return total
