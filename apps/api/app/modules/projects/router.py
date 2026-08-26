"""REST endpoints for projects under ``/api/v1/projects`` (CLAUDE.md §6, §9)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
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
    burn: str | None = Query(
        None,
        max_length=20,
        description=(
            "'over' keeps only budgeted projects at or past their budget (#437) — the burn "
            "enrichment rides along, and the total counts what survived. Other tokens are "
            "ignored."
        ),
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
        hours=hours,
        count=count,
        burn=burn,
    )
    return Page(
        items=[ProjectRead.model_validate(p) for p in items],
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
    return ProjectRead.model_validate(project)


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
    project = await ProjectService(ctx).get(project_id, hours=hours)
    return ProjectRead.model_validate(project)


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
    return ProjectRead.model_validate(project)


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
