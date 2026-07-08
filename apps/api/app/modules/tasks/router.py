"""REST endpoints for tasks under ``/api/v1/tasks`` (CLAUDE.md §6, §9).

Route order matters: literal paths (``/mine``, ``/labels``, ``/templates``) are registered
before ``/{task_id}`` because Starlette matches in registration order.
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query

from app.core.tenancy import RequestContext, require_context
from app.modules.tasks.models import TaskStatus
from app.modules.tasks.schemas import (
    ChecklistCreate,
    ChecklistItemCreate,
    ChecklistItemRead,
    ChecklistItemUpdate,
    ChecklistRead,
    ChecklistTemplateCreate,
    ChecklistTemplateRead,
    ChecklistTemplateUpdate,
    ChecklistUpdate,
    CommentCreate,
    CommentRead,
    CommentUpdate,
    LabelCreate,
    LabelRead,
    LabelUpdate,
    LinkCreate,
    LinkRead,
    TaskCreate,
    TaskDetail,
    TaskLabelsSet,
    TaskListItem,
    TaskRead,
    TaskUpdate,
    TemplateApply,
    TemplateCreate,
    TemplateRead,
    TemplateUpdate,
)
from app.modules.tasks.service import TaskService
from app.modules.tasks.templates import TemplateService
from app.schemas import Page

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("", response_model=Page[TaskListItem])
async def list_tasks(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    company_id: uuid.UUID | None = Query(None),
    project_id: uuid.UUID | None = Query(None),
    assignee_user_id: uuid.UUID | None = Query(None),
    status: TaskStatus | None = Query(None),
    label_id: uuid.UUID | None = Query(None),
    due: Literal["overdue", "today", "week"] | None = Query(None),
    q: str | None = Query(None, max_length=200),
    meta: bool = Query(True, description="Include label/checklist/comment aggregates"),
    count: bool = Query(True, description="Compute total; set false for name-only lookups"),
    ctx: RequestContext = Depends(require_context),
) -> Page[TaskListItem]:
    items, total = await TaskService(ctx).list(
        limit=limit,
        offset=offset,
        company_id=company_id,
        project_id=project_id,
        assignee_user_id=assignee_user_id,
        status=status,
        label_id=label_id,
        due=due,
        q=q,
        with_meta=meta,
        count=count,
    )
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/mine", response_model=list[TaskListItem])
async def my_open_tasks(
    limit: int = Query(20, ge=1, le=100),
    ctx: RequestContext = Depends(require_context),
) -> list[TaskListItem]:
    """Open/in-progress tasks assigned to the current user (My Day)."""
    return await TaskService(ctx).my_open(limit=limit)


# --------------------------------------------------------------------------- #
# Labels (org-level vocabulary)
# --------------------------------------------------------------------------- #
@router.get("/labels", response_model=list[LabelRead])
async def list_labels(ctx: RequestContext = Depends(require_context)) -> list[LabelRead]:
    labels = await TaskService(ctx).list_labels()
    return [LabelRead.model_validate(label) for label in labels]


@router.post("/labels", response_model=LabelRead, status_code=201)
async def create_label(
    payload: LabelCreate, ctx: RequestContext = Depends(require_context)
) -> LabelRead:
    return LabelRead.model_validate(await TaskService(ctx).create_label(payload))


@router.patch("/labels/{label_id}", response_model=LabelRead)
async def update_label(
    label_id: uuid.UUID,
    payload: LabelUpdate,
    ctx: RequestContext = Depends(require_context),
) -> LabelRead:
    return LabelRead.model_validate(await TaskService(ctx).update_label(label_id, payload))


@router.delete("/labels/{label_id}", status_code=204)
async def delete_label(
    label_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> None:
    await TaskService(ctx).delete_label(label_id)


# --------------------------------------------------------------------------- #
# Checklist templates (org-wide repository, staff-writable)
# --------------------------------------------------------------------------- #
@router.get("/checklist-templates", response_model=list[ChecklistTemplateRead])
async def list_checklist_templates(
    ctx: RequestContext = Depends(require_context),
) -> list[ChecklistTemplateRead]:
    templates = await TaskService(ctx).list_checklist_templates()
    return [ChecklistTemplateRead.model_validate(t) for t in templates]


@router.post("/checklist-templates", response_model=ChecklistTemplateRead, status_code=201)
async def create_checklist_template(
    payload: ChecklistTemplateCreate, ctx: RequestContext = Depends(require_context)
) -> ChecklistTemplateRead:
    return ChecklistTemplateRead.model_validate(
        await TaskService(ctx).create_checklist_template(payload)
    )


@router.patch("/checklist-templates/{template_id}", response_model=ChecklistTemplateRead)
async def update_checklist_template(
    template_id: uuid.UUID,
    payload: ChecklistTemplateUpdate,
    ctx: RequestContext = Depends(require_context),
) -> ChecklistTemplateRead:
    return ChecklistTemplateRead.model_validate(
        await TaskService(ctx).update_checklist_template(template_id, payload)
    )


@router.delete("/checklist-templates/{template_id}", status_code=204)
async def delete_checklist_template(
    template_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> None:
    await TaskService(ctx).delete_checklist_template(template_id)


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #
@router.get("/templates", response_model=list[TemplateRead])
async def list_templates(
    ctx: RequestContext = Depends(require_context),
) -> list[TemplateRead]:
    return await TemplateService(ctx).list()


@router.post("/templates", response_model=TemplateRead, status_code=201)
async def create_template(
    payload: TemplateCreate, ctx: RequestContext = Depends(require_context)
) -> TemplateRead:
    return await TemplateService(ctx).create(payload)


@router.get("/templates/{template_id}", response_model=TemplateRead)
async def get_template(
    template_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> TemplateRead:
    return await TemplateService(ctx).get(template_id)


@router.patch("/templates/{template_id}", response_model=TemplateRead)
async def update_template(
    template_id: uuid.UUID,
    payload: TemplateUpdate,
    ctx: RequestContext = Depends(require_context),
) -> TemplateRead:
    return await TemplateService(ctx).update(template_id, payload)


@router.delete("/templates/{template_id}", status_code=204)
async def delete_template(
    template_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> None:
    await TemplateService(ctx).delete(template_id)


@router.post("/templates/{template_id}/apply", response_model=list[TaskRead], status_code=201)
async def apply_template(
    template_id: uuid.UUID,
    payload: TemplateApply,
    ctx: RequestContext = Depends(require_context),
) -> list[TaskRead]:
    tasks = await TemplateService(ctx).apply(template_id, payload.company_id)
    return [TaskRead.model_validate(t) for t in tasks]


# --------------------------------------------------------------------------- #
# Task CRUD
# --------------------------------------------------------------------------- #
@router.post("", response_model=TaskRead, status_code=201)
async def create_task(
    payload: TaskCreate,
    ctx: RequestContext = Depends(require_context),
) -> TaskRead:
    task = await TaskService(ctx).create(payload)
    return TaskRead.model_validate(task)


@router.get("/{task_id}", response_model=TaskDetail)
async def get_task(
    task_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> TaskDetail:
    """The full card: labels, checklists, comments and recent activity included."""
    return await TaskService(ctx).detail(task_id)


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


@router.put("/{task_id}/labels", response_model=list[LabelRead])
async def set_task_labels(
    task_id: uuid.UUID,
    payload: TaskLabelsSet,
    ctx: RequestContext = Depends(require_context),
) -> list[LabelRead]:
    labels = await TaskService(ctx).set_task_labels(task_id, payload.label_ids)
    return [LabelRead.model_validate(label) for label in labels]


# --------------------------------------------------------------------------- #
# Links (URL attachments)
# --------------------------------------------------------------------------- #
@router.post("/{task_id}/links", response_model=LinkRead, status_code=201)
async def add_link(
    task_id: uuid.UUID,
    payload: LinkCreate,
    ctx: RequestContext = Depends(require_context),
) -> LinkRead:
    return LinkRead.model_validate(await TaskService(ctx).add_link(task_id, payload))


@router.delete("/{task_id}/links/{link_id}", status_code=204)
async def delete_link(
    task_id: uuid.UUID,
    link_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await TaskService(ctx).delete_link(task_id, link_id)


# --------------------------------------------------------------------------- #
# Comments
# --------------------------------------------------------------------------- #
@router.post("/{task_id}/comments", response_model=CommentRead, status_code=201)
async def add_comment(
    task_id: uuid.UUID,
    payload: CommentCreate,
    ctx: RequestContext = Depends(require_context),
) -> CommentRead:
    return await TaskService(ctx).add_comment(task_id, payload)


@router.patch("/{task_id}/comments/{comment_id}", response_model=CommentRead)
async def update_comment(
    task_id: uuid.UUID,
    comment_id: uuid.UUID,
    payload: CommentUpdate,
    ctx: RequestContext = Depends(require_context),
) -> CommentRead:
    return await TaskService(ctx).update_comment(task_id, comment_id, payload)


@router.delete("/{task_id}/comments/{comment_id}", status_code=204)
async def delete_comment(
    task_id: uuid.UUID,
    comment_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await TaskService(ctx).delete_comment(task_id, comment_id)


# --------------------------------------------------------------------------- #
# Checklists
# --------------------------------------------------------------------------- #
@router.post("/{task_id}/checklists", response_model=ChecklistRead, status_code=201)
async def add_checklist(
    task_id: uuid.UUID,
    payload: ChecklistCreate,
    ctx: RequestContext = Depends(require_context),
) -> ChecklistRead:
    return ChecklistRead.model_validate(await TaskService(ctx).add_checklist(task_id, payload))


@router.patch("/{task_id}/checklists/{checklist_id}", response_model=ChecklistRead)
async def update_checklist(
    task_id: uuid.UUID,
    checklist_id: uuid.UUID,
    payload: ChecklistUpdate,
    ctx: RequestContext = Depends(require_context),
) -> ChecklistRead:
    return ChecklistRead.model_validate(
        await TaskService(ctx).update_checklist(task_id, checklist_id, payload)
    )


@router.delete("/{task_id}/checklists/{checklist_id}", status_code=204)
async def delete_checklist(
    task_id: uuid.UUID,
    checklist_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await TaskService(ctx).delete_checklist(task_id, checklist_id)


@router.post(
    "/{task_id}/checklists/{checklist_id}/items",
    response_model=ChecklistItemRead,
    status_code=201,
)
async def add_checklist_item(
    task_id: uuid.UUID,
    checklist_id: uuid.UUID,
    payload: ChecklistItemCreate,
    ctx: RequestContext = Depends(require_context),
) -> ChecklistItemRead:
    return ChecklistItemRead.model_validate(
        await TaskService(ctx).add_checklist_item(task_id, checklist_id, payload)
    )


@router.patch(
    "/{task_id}/checklists/{checklist_id}/items/{item_id}",
    response_model=ChecklistItemRead,
)
async def update_checklist_item(
    task_id: uuid.UUID,
    checklist_id: uuid.UUID,
    item_id: uuid.UUID,
    payload: ChecklistItemUpdate,
    ctx: RequestContext = Depends(require_context),
) -> ChecklistItemRead:
    return ChecklistItemRead.model_validate(
        await TaskService(ctx).update_checklist_item(task_id, checklist_id, item_id, payload)
    )


@router.delete("/{task_id}/checklists/{checklist_id}/items/{item_id}", status_code=204)
async def delete_checklist_item(
    task_id: uuid.UUID,
    checklist_id: uuid.UUID,
    item_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await TaskService(ctx).delete_checklist_item(task_id, checklist_id, item_id)
