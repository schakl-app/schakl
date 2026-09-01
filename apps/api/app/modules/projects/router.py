"""REST endpoints for projects under ``/api/v1/projects`` (CLAUDE.md §6, §9)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.modules.projects.models import Project
from app.modules.projects.schemas import (
    DashboardBudgets,
    ProjectCreate,
    ProjectRead,
    ProjectSettingsRead,
    ProjectSettingsUpdate,
    ProjectUpdate,
)
from app.modules.projects.service import ProjectService
from app.schemas import Page

router = APIRouter(prefix="/projects", tags=["projects"])


def _read(project: Project, ctx: RequestContext) -> ProjectRead:
    """One project, as *this* reader may see it (#449).

    A client-portal login reads its own projects — the name, the status, who is on it, the
    dates — and never the agency's economics: the hour budget, the amount, and the burn are
    what the agency agreed with itself about the work, not a fact the client is party to.
    Decided here, on the way out of every read, so the list, the detail and an MCP client
    answer alike; the web mirrors it by not drawing the column or the block (§15: the guard
    there is UX, the boundary is here). ``budget_watch`` already keeps a client off the burn
    *alerts* for the same reason — this is the screen catching up with the mail.
    """
    read = ProjectRead.model_validate(project)
    if ctx.is_portal:
        read.budget_hours = None
        read.budget_amount = None
        read.hours = None
        read.budget_sources = []
    return read


# Literal path before the dynamic ``/{project_id}``, or the segment swallows it.
@router.get(
    "/dashboard-budgets",
    response_model=DashboardBudgets,
    dependencies=[require_permission("projects.project.read")],
)
async def dashboard_budgets(
    # 50, not 20: the budgets donut (#437) draws more of the book than the My Day tile's four,
    # and the honest tail bucket takes whatever the cap leaves.
    limit: int = Query(4, ge=1, le=50),
    ctx: RequestContext = Depends(require_context),
) -> DashboardBudgets:
    """The budgeted active projects burning hottest — the My Day tile, already sorted and cut.

    Mirrors ``/tasks/dashboard-groups``: the widget asked for 200 rows and kept four
    (docs/PERFORMANCE.md).
    """
    return await ProjectService(ctx).dashboard_budgets(limit=limit)


# Literal path before the dynamic ``/{project_id}``, like ``/dashboard-budgets`` above.
@router.get(
    "/settings",
    response_model=ProjectSettingsRead,
    dependencies=[require_permission("projects.settings.manage")],
)
async def get_settings(ctx: RequestContext = Depends(require_context)) -> ProjectSettingsRead:
    """The org's projects settings (the budget alert). No saved row means the defaults."""
    return await ProjectService(ctx).settings()


@router.put(
    "/settings",
    response_model=ProjectSettingsRead,
    dependencies=[require_permission("projects.settings.manage")],
)
async def update_settings(
    payload: ProjectSettingsUpdate,
    ctx: RequestContext = Depends(require_context),
) -> ProjectSettingsRead:
    return await ProjectService(ctx).update_settings(payload)


@router.get(
    "",
    response_model=Page[ProjectRead],
    dependencies=[require_permission("projects.project.read")],
)
async def list_projects(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    company_id: uuid.UUID | None = Query(None),
    status: str | None = Query(
        None,
        max_length=200,
        description=(
            "Lifecycle status; comma-separate for several ('active,on_hold'). Absent means "
            "every status, the archived ones included — the screen picks its own default, "
            "this endpoint does not."
        ),
    ),
    q: str | None = Query(None, max_length=200),
    unnamed: bool | None = Query(
        None,
        description=(
            "Only projects nobody named (create-then-edit rows never finished), or only named "
            "ones. Omitted returns both."
        ),
    ),
    mine: bool = Query(False, description="Only projects I'm assigned to (primary or not)"),
    sort: str | None = Query(
        None, description="name | status | start_date | end_date | budget_hours | …, '-' desc"
    ),
    hours: bool = Query(
        False, description="Include the budget burn-down; costs one grouped query"
    ),
    count: bool = Query(True, description="Compute total; set false for name-only lookups"),
    # Description kept terse on purpose: this operation is in the MCP compact profile, whose
    # whole tool budget is pinned in bytes (test_mcp_compact_profile_fits_a_chat_client).
    burn: str | None = Query(
        None,
        max_length=20,
        description="'over' keeps only projects at or past their budget; other tokens ignored",
    ),
    ctx: RequestContext = Depends(require_context),
) -> Page[ProjectRead]:
    items, total = await ProjectService(ctx).list(
        limit=limit,
        offset=offset,
        company_id=company_id,
        status=status,
        q=q,
        unnamed=unnamed,
        mine=mine,
        sort=sort,
        # A client never pays for the burn aggregate it is not shown (#449, `_read`).
        hours=hours and not ctx.is_portal,
        count=count,
        burn=burn,
    )
    return Page(
        items=[_read(p, ctx) for p in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "",
    response_model=ProjectRead,
    status_code=201,
    dependencies=[require_permission("projects.project.write")],
)
async def create_project(
    payload: ProjectCreate,
    ctx: RequestContext = Depends(require_context),
) -> ProjectRead:
    project = await ProjectService(ctx).create(payload)
    return _read(project, ctx)


@router.get(
    "/{project_id}",
    response_model=ProjectRead,
    dependencies=[require_permission("projects.project.read")],
)
async def get_project(
    project_id: uuid.UUID,
    hours: bool = Query(
        False, description="Include the budget burn-down for the current period; one extra query"
    ),
    ctx: RequestContext = Depends(require_context),
) -> ProjectRead:
    project = await ProjectService(ctx).get(project_id, hours=hours and not ctx.is_portal)
    return _read(project, ctx)


@router.patch(
    "/{project_id}",
    response_model=ProjectRead,
    dependencies=[require_permission("projects.project.write")],
)
async def update_project(
    project_id: uuid.UUID,
    payload: ProjectUpdate,
    ctx: RequestContext = Depends(require_context),
) -> ProjectRead:
    project = await ProjectService(ctx).update(project_id, payload)
    return _read(project, ctx)


@router.delete(
    "/{project_id}",
    status_code=204,
    dependencies=[require_permission("projects.project.delete")],
)
async def delete_project(
    project_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await ProjectService(ctx).delete(project_id)
