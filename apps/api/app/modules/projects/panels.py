"""Projects panel on the company detail view (CLAUDE.md §6, the modular hub).

Lists a company's projects (active first) with their budget targets.
"""

from __future__ import annotations

import uuid

from sqlalchemy import case

from app.core.tenancy import RequestContext
from app.modules.projects.models import Project, ProjectStatus
from app.registry import PROMINENCE_PRIMARY, SIZE_HALF, PanelSpec

#: How many projects the client card shows before handing over to the list — the domains and
#: websites panels' number and rule: a panel is the first page of the list it links to.
_PANEL_LIMIT = 5

_STATUS_ORDER = {
    ProjectStatus.ACTIVE.value: 0,
    ProjectStatus.ON_HOLD.value: 1,
    ProjectStatus.COMPLETED.value: 2,
    ProjectStatus.ARCHIVED.value: 3,
}


async def _projects_provider(ctx: RequestContext, company_id: uuid.UUID) -> dict:
    repo = ctx.repo(Project)
    # Active-first is decided in SQL, not after the page has been cut (#364). Taking the newest
    # 50 and *then* sorting them in Python meant a client with 60 projects could lose active ones
    # off a list that claims to lead with them — the truncation `docs/UX.md` forbids, with the
    # ordering promise broken on top of it.
    rank = case(_STATUS_ORDER, value=Project.status, else_=9)
    stmt = (
        repo.scoped_select()
        .where(Project.company_id == company_id)
        .order_by(rank.asc(), Project.created_at.desc())
        .limit(_PANEL_LIMIT)
    )
    projects = (await ctx.session.execute(stmt)).scalars().all()
    total = int(
        await ctx.session.scalar(
            repo.scoped_count_select().where(Project.company_id == company_id)
        )
        or 0
    )
    return {
        # The whole count, never the shown one: five over a client who has sixty reads as the
        # complete answer, which is the one thing a summary must not do.
        "total": total,
        "projects": [
            {
                "id": str(p.id),
                "name": p.name,
                "status": p.status,
                "billable_default": p.billable_default,
                "budget_hours": float(p.budget_hours) if p.budget_hours is not None else None,
            }
            for p in projects
        ],
    }


projects_company_panel = PanelSpec(
    key="projects.company",
    entity_type="company",
    title_key="projects.panel.title",
    provider=_projects_provider,
    position=25,
    requires_permission="projects.project.read",
    prominence=PROMINENCE_PRIMARY,
    size=SIZE_HALF,
    empty_when=lambda data: not data.get("projects"),
)
