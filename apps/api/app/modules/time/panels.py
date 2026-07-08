"""Time panel on the company detail view (CLAUDE.md §6, the modular hub).

Shows total minutes logged against a company plus a few recent entries (across the team).
"""

from __future__ import annotations

import uuid

from app.core.tenancy import RequestContext
from app.modules.time.models import TimeEntry
from app.registry import PanelSpec


async def _time_provider(ctx: RequestContext, company_id: uuid.UUID) -> dict:
    repo = ctx.repo(TimeEntry)
    stmt = (
        repo.scoped_select()
        .where(TimeEntry.company_id == company_id)
        .order_by(TimeEntry.started_at.desc())
    )
    entries = (await ctx.session.execute(stmt)).scalars().all()
    total_minutes = sum(e.minutes for e in entries)
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
            for e in entries[:10]
        ],
    }


time_company_panel = PanelSpec(
    key="time.company",
    entity_type="company",
    title_key="time.panel.title",
    provider=_time_provider,
    position=40,
)
