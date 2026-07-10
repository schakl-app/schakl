"""Monday morning: ``time.timesheet_reminder`` for anyone who logged nothing last week.

A timesheet nobody filled in is invisible until invoicing, which is far too late. This runs on
Monday and nudges the staff whose previous ISO week is empty — once, keyed on the week, so a
re-run (or a worker restart) never nags twice.

"Staff" is every membership that is not a ``client``: clients do not log time. A timesheet has
no row of its own, so the *person* is the subject of the event and the week rides in the payload.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import distinct, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import SystemContext, emit
from app.core.models import Membership, Org
from app.core.roles import Role
from app.modules.time.models import TimeEntry

_TZ = ZoneInfo("Europe/Amsterdam")


def previous_week_start(today: date | None = None) -> date:
    """The Monday of the ISO week before ``today`` (local, because the cron fires in UTC)."""
    today = today or datetime.now(_TZ).date()
    this_monday = today - timedelta(days=today.weekday())
    return this_monday - timedelta(days=7)


def _week_bounds(week_start: date) -> tuple[datetime, datetime]:
    """The UTC instants the local week opens and closes — DST-correct, unlike ``+7 days`` in UTC."""
    start = datetime.combine(week_start, time.min, tzinfo=_TZ).astimezone(UTC)
    end = datetime.combine(week_start + timedelta(days=7), time.min, tzinfo=_TZ).astimezone(UTC)
    return start, end


async def remind_for_org(org: Org, session: AsyncSession, *, week_start: date | None = None) -> int:
    """Nudge every staff member with an empty previous week.

    Returns the number of *candidates announced*, not notifications delivered: a re-run
    announces the same people and the notifications module drops the repeat on its dedup key.
    """
    week_start = week_start or previous_week_start()
    start, end = _week_bounds(week_start)

    staff = set(
        (
            await session.execute(
                select(Membership.user_id).where(
                    Membership.org_id == org.id,
                    Membership.role != Role.CLIENT.value,
                )
            )
        ).scalars()
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

    ctx = SystemContext(org=org, session=session)
    for user_id in sorted(staff - logged):
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
    return len(staff - logged)


async def send_timesheet_reminders(ctx: dict) -> int:
    """ARQ cron entry point: last week's empty timesheets, for every org."""
    from app.core.jobs import run_per_org

    total = 0

    async def _per_org(org: Org, session: AsyncSession) -> None:
        nonlocal total
        total += await remind_for_org(org, session)

    await run_per_org(_per_org)
    return total
