"""Daily budget watch: ``project.budget_threshold`` at 75% and 100% (issue #16).

A budget that is only discovered when it is blown is a budget nobody managed. This walks the
active, budgeted projects once a day and tells their assignees when the burn crosses a
threshold — once per threshold, per budget period.

The dedup key carries the period start, so a **monthly** budget can warn again next month
without the cron having to remember anything. A ``total`` budget never resets, so it warns
once for the life of the project.

Spend comes from the time module's published service (``minutes_by_project``), the same one
the budget bar on screen uses — never from a direct read of its tables (CLAUDE.md §6).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import SystemContext, emit
from app.core.models import Org
from app.modules.projects.budget import period_bound, period_start_date
from app.modules.projects.models import Project, ProjectAssignee, ProjectStatus

#: Ascending, so a project that jumps straight past both still reports each once.
THRESHOLDS: tuple[int, ...] = (75, 100)


async def _assignees(session: AsyncSession, org_id, project_id) -> list:
    return list(
        (
            await session.execute(
                select(ProjectAssignee.user_id).where(
                    ProjectAssignee.org_id == org_id,
                    ProjectAssignee.project_id == project_id,
                )
            )
        ).scalars()
    )


async def watch_for_org(org: Org, session: AsyncSession) -> int:
    """Emit a threshold event for every active, budgeted project that has crossed one.

    Returns the number of *candidates announced*, not notifications delivered: a project over
    100% re-announces both thresholds on every tick, and the notifications module drops the
    repeats on their dedup keys.
    """
    from app.modules.time.service import LoggedMinutes, TimeService

    projects = (
        await session.execute(
            select(Project).where(
                Project.org_id == org.id,
                Project.status == ProjectStatus.ACTIVE.value,
                Project.budget_hours.is_not(None),
            )
        )
    ).scalars().all()
    if not projects:
        return 0

    ctx = SystemContext(org=org, session=session)
    periods = {p.id: period_bound(p.budget_period) for p in projects}
    logged = await TimeService(ctx).minutes_by_project(periods)

    emitted = 0
    for project in projects:
        budget = float(project.budget_hours)
        if budget <= 0:  # a zero budget is "unbudgeted", not "instantly over"
            continue
        spent = logged.get(project.id, LoggedMinutes()).total / 60
        percent = round(spent / budget * 100)
        period = period_start_date(project.budget_period)
        recipients = await _assignees(session, org.id, project.id)
        if not recipients:
            continue
        for threshold in THRESHOLDS:
            if percent < threshold:
                continue
            await emit(
                "project.budget_threshold",
                ctx,
                {
                    "project_id": project.id,
                    "title": project.name,
                    "threshold": threshold,
                    "percent": percent,
                    "_recipients": recipients,
                    # Period-scoped: a monthly budget may warn again next month.
                    "_dedup_key": (
                        f"project.budget_threshold:{project.id}:{threshold}:"
                        f"{period.isoformat() if period else 'total'}"
                    ),
                },
            )
            emitted += 1
    return emitted


async def watch_project_budgets(ctx: dict) -> int:
    """ARQ cron entry point: budget threshold warnings for every org."""
    from app.core.jobs import run_per_org

    total = 0

    async def _per_org(org: Org, session: AsyncSession) -> None:
        nonlocal total
        total += await watch_for_org(org, session)

    await run_per_org(_per_org)
    return total
