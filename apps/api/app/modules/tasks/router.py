"""REST endpoints for tasks under ``/api/v1/tasks`` (CLAUDE.md §6, §9)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.core.tenancy import RequestContext, require_context
from app.modules.tasks.models import TaskStatus
from app.modules.tasks.schemas import TaskCreate, TaskRead, TaskUpdate
from app.modules.tasks.service import TaskService
from app.schemas import Page

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("", response_model=Page[TaskRead])
async def list_tasks(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    company_id: uuid.UUID | None = Query(None),
    project_id: uuid.UUID | None = Query(None),
    assignee_user_id: uuid.UUID | None = Query(None),
    status: TaskStatus | None = Query(None),
    ctx: RequestContext = Depends(require_context),
) -> Page[TaskRead]:
    items, total = await TaskService(ctx).list(
        limit=limit,
        offset=offset,
        company_id=company_id,
        project_id=project_id,
        assignee_user_id=assignee_user_id,
        status=status,
    )
    return Page(
        items=[TaskRead.model_validate(t) for t in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/mine", response_model=list[TaskRead])
async def my_open_tasks(
    limit: int = Query(20, ge=1, le=100),
    ctx: RequestContext = Depends(require_context),
) -> list[TaskRead]:
    """Open/in-progress tasks assigned to the current user (My Day)."""
    tasks = await TaskService(ctx).my_open(limit=limit)
    return [TaskRead.model_validate(t) for t in tasks]


@router.post("", response_model=TaskRead, status_code=201)
async def create_task(
    payload: TaskCreate,
    ctx: RequestContext = Depends(require_context),
) -> TaskRead:
    task = await TaskService(ctx).create(payload)
    return TaskRead.model_validate(task)


@router.get("/{task_id}", response_model=TaskRead)
async def get_task(
    task_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> TaskRead:
    task = await TaskService(ctx).get(task_id)
    return TaskRead.model_validate(task)


@router.patch("/{task_id}", response_model=TaskRead)
async def update_task(
    task_id: uuid.UUID,
    payload: TaskUpdate,
    ctx: RequestContext = Depends(require_context),
) -> TaskRead:
    task = await TaskService(ctx).update(task_id, payload)
    return TaskRead.model_validate(task)


@router.delete("/{task_id}", status_code=204)
async def delete_task(
    task_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await TaskService(ctx).delete(task_id)
