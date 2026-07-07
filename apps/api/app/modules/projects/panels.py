"""Projects panel on the company detail view (CLAUDE.md §6, the modular hub).

Lists a company's projects (active first) with their budget targets.
"""

from __future__ import annotations

import uuid

from app.core.tenancy import RequestContext
from app.modules.projects.models import Project, ProjectStatus
from app.registry import PanelSpec

_STATUS_ORDER = {
    ProjectStatus.ACTIVE.value: 0,
    ProjectStatus.ON_HOLD.value: 1,
    ProjectStatus.COMPLETED.value: 2,
    ProjectStatus.ARCHIVED.value: 3,
}


async def _projects_provider(ctx: RequestContext, company_id: uuid.UUID) -> dict:
    repo = ctx.repo(Project)
    stmt = (
        repo.scoped_select()
        .where(Project.company_id == company_id)
        .order_by(Project.created_at.desc())
        .limit(50)
    )
    projects = (await ctx.session.execute(stmt)).scalars().all()
    projects = sorted(projects, key=lambda p: _STATUS_ORDER.get(p.status, 9))
    return {
        "projects": [
            {
                "id": str(p.id),
                "name": p.name,
                "status": p.status,
                "billable_default": p.billable_default,
                "budget_hours": float(p.budget_hours) if p.budget_hours is not None else None,
            }
            for p in projects
        ]
    }


projects_company_panel = PanelSpec(
    key="projects.company",
    entity_type="company",
    title_key="projects.panel.title",
    provider=_projects_provider,
    position=25,
)
