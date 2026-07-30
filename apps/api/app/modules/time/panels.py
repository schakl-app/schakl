"""Time panel on the company detail view (CLAUDE.md §6, the modular hub).

Shows total minutes logged against a company plus a few recent entries (across the team).
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.core.tenancy import RequestContext
from app.modules.time.models import TimeEntry
from app.registry import PanelSpec

# How many recent entries the panel shows. The panel used to load the client's *entire*
# timesheet to display this handful and one total — the total is an aggregate now, and the list
# is bounded, so a client with ten years of history costs the same as a new one.
_RECENT = 10


async def _time_provider(ctx: RequestContext, company_id: uuid.UUID) -> dict:
    repo = ctx.repo(TimeEntry)
    total_stmt = (
        select(func.coalesce(func.sum(TimeEntry.minutes), 0))
        .select_from(TimeEntry)
        .where(
            TimeEntry.org_id == ctx.org.id,
            TimeEntry.company_id == company_id,
        )
    )
    # A hand-built aggregate leaves the repository's path, so it asks for the horizon by name
    # (§15) — the count above a filtered list is exactly where it goes missing.
    horizon = repo.horizon_condition()
    if horizon is not None:
        total_stmt = total_stmt.where(horizon)
    total_minutes = int(await ctx.session.scalar(total_stmt) or 0)

    entries = (
        await ctx.session.execute(
            repo.scoped_select()
            .where(TimeEntry.company_id == company_id)
            .order_by(TimeEntry.started_at.desc())
            .limit(_RECENT)
        )
    ).scalars().all()
    return {
        "total_minutes": total_minutes,
        "recent": [
            {
                "id": str(e.id),
                "description": e.description,
                "minutes": e.minutes,
                "started_at": e.started_at.isoformat(),
                "billable": e.billable,
            }
            for e in entries
        ],
    }


time_company_panel = PanelSpec(
    key="time.company",
    entity_type="company",
    title_key="time.panel.title",
    provider=_time_provider,
    position=40,
)
