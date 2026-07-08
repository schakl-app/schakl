"""REST endpoints for projects under ``/api/v1/projects`` (CLAUDE.md §6, §9)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.core.tenancy import RequestContext, require_context
from app.modules.projects.models import ProjectStatus
from app.modules.projects.schemas import ProjectCreate, ProjectRead, ProjectUpdate
from app.modules.projects.service import ProjectService
from app.schemas import Page

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=Page[ProjectRead])
async def list_projects(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    company_id: uuid.UUID | None = Query(None),
    status: ProjectStatus | None = Query(None),
    q: str | None = Query(None, max_length=200),
    count: bool = Query(True, description="Compute total; set false for name-only lookups"),
    ctx: RequestContext = Depends(require_context),
) -> Page[ProjectRead]:
    items, total = await ProjectService(ctx).list(
        limit=limit, offset=offset, company_id=company_id, status=status, q=q, count=count
    )
    return Page(
        items=[ProjectRead.model_validate(p) for p in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=ProjectRead, status_code=201)
async def create_project(
    payload: ProjectCreate,
    ctx: RequestContext = Depends(require_context),
) -> ProjectRead:
    project = await ProjectService(ctx).create(payload)
    return ProjectRead.model_validate(project)


@router.get("/{project_id}", response_model=ProjectRead)
async def get_project(
    project_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> ProjectRead:
    project = await ProjectService(ctx).get(project_id)
    return ProjectRead.model_validate(project)


@router.patch("/{project_id}", response_model=ProjectRead)
async def update_project(
    project_id: uuid.UUID,
    payload: ProjectUpdate,
    ctx: RequestContext = Depends(require_context),
) -> ProjectRead:
    project = await ProjectService(ctx).update(project_id, payload)
    return ProjectRead.model_validate(project)


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await ProjectService(ctx).delete(project_id)
