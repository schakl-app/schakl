"""REST endpoints for tasks under ``/api/v1/tasks`` (CLAUDE.md §6, §9).

Route order matters: literal paths (``/mine``, ``/labels``, ``/templates``) are registered
before ``/{task_id}`` because Starlette matches in registration order.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query

from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.modules.tasks.scheduling import scheduling_router
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
    StatusCreate,
    StatusRead,
    StatusUpdate,
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

# Scheduling routes (#188) must register before ``/{task_id}`` — Starlette matches in order, so
# ``/tasks/schedules`` would otherwise be parsed as a task id and 422 on the UUID.
router.include_router(scheduling_router)


@router.get(
    "",
    response_model=Page[TaskListItem],
    dependencies=[require_permission("tasks.task.read")],
)
async def list_tasks(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    company_id: uuid.UUID | None = Query(None),
    project_id: uuid.UUID | None = Query(None),
    assignee_user_id: uuid.UUID | None = Query(None),
    assignee_contact_id: uuid.UUID | None = Query(None),
    status: str | None = Query(None, max_length=50, description="A configured status key"),
    label_id: uuid.UUID | None = Query(None),
    due: Literal["overdue", "today", "week"] | None = Query(None),
    due_from: date | None = Query(None, description="Deadline window start (the Agenda feed)"),
    due_to: date | None = Query(None, description="Deadline window end (inclusive)"),
    q: str | None = Query(None, max_length=200),
    sort: str | None = Query(
        None, description="title | due_date | priority | status | assignee | …, '-' desc"
    ),
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
        assignee_contact_id=assignee_contact_id,
        status=status,
        label_id=label_id,
        due=due,
        due_from=due_from,
        due_to=due_to,
        q=q,
        sort=sort,
        with_meta=meta,
        count=count,
    )
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get(
    "/mine",
    response_model=list[TaskListItem],
    dependencies=[require_permission("tasks.task.read")],
)
async def my_open_tasks(
    limit: int = Query(20, ge=1, le=100),
    ctx: RequestContext = Depends(require_context),
) -> list[TaskListItem]:
    """Open/in-progress tasks assigned to the current user (My Day)."""
    return await TaskService(ctx).my_open(limit=limit)


# --------------------------------------------------------------------------- #
# Labels (org-level vocabulary)
# --------------------------------------------------------------------------- #
@router.get(
    "/labels",
    response_model=list[LabelRead],
    dependencies=[require_permission("tasks.task.read")],
)
async def list_labels(ctx: RequestContext = Depends(require_context)) -> list[LabelRead]:
    labels = await TaskService(ctx).list_labels()
    return [LabelRead.model_validate(label) for label in labels]


@router.post(
    "/labels",
    response_model=LabelRead,
    status_code=201,
    dependencies=[require_permission("tasks.label.write")],
)
async def create_label(
    payload: LabelCreate, ctx: RequestContext = Depends(require_context)
) -> LabelRead:
    return LabelRead.model_validate(await TaskService(ctx).create_label(payload))


@router.patch(
    "/labels/{label_id}",
    response_model=LabelRead,
    dependencies=[require_permission("tasks.label.write")],
)
async def update_label(
    label_id: uuid.UUID,
    payload: LabelUpdate,
    ctx: RequestContext = Depends(require_context),
) -> LabelRead:
    return LabelRead.model_validate(await TaskService(ctx).update_label(label_id, payload))


@router.delete(
    "/labels/{label_id}",
    status_code=204,
    dependencies=[require_permission("tasks.label.write")],
)
async def delete_label(
    label_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> None:
    await TaskService(ctx).delete_label(label_id)


# --------------------------------------------------------------------------- #
# Statuses (org-level, tenant-configurable — issue #62)
# --------------------------------------------------------------------------- #
@router.get(
    "/statuses",
    response_model=list[StatusRead],
    dependencies=[require_permission("tasks.task.read")],
)
async def list_statuses(ctx: RequestContext = Depends(require_context)) -> list[StatusRead]:
    statuses = await TaskService(ctx).list_statuses()
    return [StatusRead.model_validate(status) for status in statuses]


@router.post(
    "/statuses",
    response_model=StatusRead,
    status_code=201,
    dependencies=[require_permission("tasks.status.write")],
)
async def create_status(
    payload: StatusCreate, ctx: RequestContext = Depends(require_context)
) -> StatusRead:
    return StatusRead.model_validate(await TaskService(ctx).create_status(payload))


@router.patch(
    "/statuses/{status_id}",
    response_model=StatusRead,
    dependencies=[require_permission("tasks.status.write")],
)
async def update_status(
    status_id: uuid.UUID,
    payload: StatusUpdate,
    ctx: RequestContext = Depends(require_context),
) -> StatusRead:
    return StatusRead.model_validate(await TaskService(ctx).update_status(status_id, payload))


@router.delete(
    "/statuses/{status_id}",
    status_code=204,
    dependencies=[require_permission("tasks.status.write")],
)
async def delete_status(
    status_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> None:
    await TaskService(ctx).delete_status(status_id)


# --------------------------------------------------------------------------- #
# Checklist templates (org-wide repository, staff-writable)
# --------------------------------------------------------------------------- #
@router.get(
    "/checklist-templates",
    response_model=list[ChecklistTemplateRead],
    dependencies=[require_permission("tasks.task.read")],
)
async def list_checklist_templates(
    ctx: RequestContext = Depends(require_context),
) -> list[ChecklistTemplateRead]:
    return await TaskService(ctx).list_checklist_templates()


@router.post(
    "/checklist-templates",
    response_model=ChecklistTemplateRead,
    status_code=201,
    dependencies=[require_permission("tasks.checklist_template.write")],
)
async def create_checklist_template(
    payload: ChecklistTemplateCreate, ctx: RequestContext = Depends(require_context)
) -> ChecklistTemplateRead:
    return await TaskService(ctx).create_checklist_template(payload)


@router.patch(
    "/checklist-templates/{template_id}",
    response_model=ChecklistTemplateRead,
    dependencies=[require_permission("tasks.checklist_template.write")],
)
async def update_checklist_template(
    template_id: uuid.UUID,
    payload: ChecklistTemplateUpdate,
    ctx: RequestContext = Depends(require_context),
) -> ChecklistTemplateRead:
    return await TaskService(ctx).update_checklist_template(template_id, payload)


@router.delete(
    "/checklist-templates/{template_id}",
    status_code=204,
    dependencies=[require_permission("tasks.checklist_template.write")],
)
async def delete_checklist_template(
    template_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> None:
    await TaskService(ctx).delete_checklist_template(template_id)


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #
@router.get(
    "/templates",
    response_model=list[TemplateRead],
    dependencies=[require_permission("tasks.task.read")],
)
async def list_templates(
    ctx: RequestContext = Depends(require_context),
) -> list[TemplateRead]:
    return await TemplateService(ctx).list()


@router.post(
    "/templates",
    response_model=TemplateRead,
    status_code=201,
    dependencies=[require_permission("tasks.template.write")],
)
async def create_template(
    payload: TemplateCreate, ctx: RequestContext = Depends(require_context)
) -> TemplateRead:
    return await TemplateService(ctx).create(payload)


@router.get(
    "/templates/{template_id}",
    response_model=TemplateRead,
    dependencies=[require_permission("tasks.task.read")],
)
async def get_template(
    template_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> TemplateRead:
    return await TemplateService(ctx).get(template_id)


@router.patch(
    "/templates/{template_id}",
    response_model=TemplateRead,
    dependencies=[require_permission("tasks.template.write")],
)
async def update_template(
    template_id: uuid.UUID,
    payload: TemplateUpdate,
    ctx: RequestContext = Depends(require_context),
) -> TemplateRead:
    return await TemplateService(ctx).update(template_id, payload)


@router.delete(
    "/templates/{template_id}",
    status_code=204,
    dependencies=[require_permission("tasks.template.write")],
)
async def delete_template(
    template_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> None:
    await TemplateService(ctx).delete(template_id)


@router.post(
    "/templates/{template_id}/apply",
    response_model=list[TaskRead],
    status_code=201,
    dependencies=[require_permission("tasks.template.apply")],
)
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
@router.post(
    "",
    response_model=TaskRead,
    status_code=201,
    dependencies=[require_permission("tasks.task.create")],
)
async def create_task(
    payload: TaskCreate,
    ctx: RequestContext = Depends(require_context),
) -> TaskRead:
    task = await TaskService(ctx).create(payload)
    return TaskRead.model_validate(task)


@router.get(
    "/{task_id}",
    response_model=TaskDetail,
    dependencies=[require_permission("tasks.task.read")],
)
async def get_task(
    task_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> TaskDetail:
    """The full card: labels, checklists, comments and recent activity included."""
    return await TaskService(ctx).detail(task_id)


@router.patch(
    "/{task_id}",
    response_model=TaskRead,
    dependencies=[require_permission("tasks.task.write")],
)
async def update_task(
    task_id: uuid.UUID,
    payload: TaskUpdate,
    ctx: RequestContext = Depends(require_context),
) -> TaskRead:
    task = await TaskService(ctx).update(task_id, payload)
    return TaskRead.model_validate(task)


@router.delete(
    "/{task_id}",
    status_code=204,
    dependencies=[require_permission("tasks.task.delete")],
)
async def delete_task(
    task_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await TaskService(ctx).delete(task_id)


@router.put(
    "/{task_id}/labels",
    response_model=list[LabelRead],
    dependencies=[require_permission("tasks.task.write")],
)
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
@router.post(
    "/{task_id}/links",
    response_model=LinkRead,
    status_code=201,
    dependencies=[require_permission("tasks.task.write")],
)
async def add_link(
    task_id: uuid.UUID,
    payload: LinkCreate,
    ctx: RequestContext = Depends(require_context),
) -> LinkRead:
    return LinkRead.model_validate(await TaskService(ctx).add_link(task_id, payload))


@router.delete(
    "/{task_id}/links/{link_id}",
    status_code=204,
    dependencies=[require_permission("tasks.task.write")],
)
async def delete_link(
    task_id: uuid.UUID,
    link_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await TaskService(ctx).delete_link(task_id, link_id)


# --------------------------------------------------------------------------- #
# Comments
# --------------------------------------------------------------------------- #
@router.post(
    "/{task_id}/comments",
    response_model=CommentRead,
    status_code=201,
    dependencies=[require_permission("tasks.comment.write")],
)
async def add_comment(
    task_id: uuid.UUID,
    payload: CommentCreate,
    ctx: RequestContext = Depends(require_context),
) -> CommentRead:
    return await TaskService(ctx).add_comment(task_id, payload)


@router.patch(
    "/{task_id}/comments/{comment_id}",
    response_model=CommentRead,
    dependencies=[require_permission("tasks.comment.write")],
)
async def update_comment(
    task_id: uuid.UUID,
    comment_id: uuid.UUID,
    payload: CommentUpdate,
    ctx: RequestContext = Depends(require_context),
) -> CommentRead:
    return await TaskService(ctx).update_comment(task_id, comment_id, payload)


@router.delete(
    "/{task_id}/comments/{comment_id}",
    status_code=204,
    dependencies=[require_permission("tasks.comment.write")],
)
async def delete_comment(
    task_id: uuid.UUID,
    comment_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await TaskService(ctx).delete_comment(task_id, comment_id)


# --------------------------------------------------------------------------- #
# Checklists
# --------------------------------------------------------------------------- #
@router.post(
    "/{task_id}/checklists",
    response_model=ChecklistRead,
    status_code=201,
    dependencies=[require_permission("tasks.task.write")],
)
async def add_checklist(
    task_id: uuid.UUID,
    payload: ChecklistCreate,
    ctx: RequestContext = Depends(require_context),
) -> ChecklistRead:
    return ChecklistRead.model_validate(await TaskService(ctx).add_checklist(task_id, payload))


@router.patch(
    "/{task_id}/checklists/{checklist_id}",
    response_model=ChecklistRead,
    dependencies=[require_permission("tasks.task.write")],
)
async def update_checklist(
    task_id: uuid.UUID,
    checklist_id: uuid.UUID,
    payload: ChecklistUpdate,
    ctx: RequestContext = Depends(require_context),
) -> ChecklistRead:
    return ChecklistRead.model_validate(
        await TaskService(ctx).update_checklist(task_id, checklist_id, payload)
    )


@router.delete(
    "/{task_id}/checklists/{checklist_id}",
    status_code=204,
    dependencies=[require_permission("tasks.task.write")],
)
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
    dependencies=[require_permission("tasks.task.write")],
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
    dependencies=[require_permission("tasks.task.write")],
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


@router.delete(
    "/{task_id}/checklists/{checklist_id}/items/{item_id}",
    status_code=204,
    dependencies=[require_permission("tasks.task.write")],
)
async def delete_checklist_item(
    task_id: uuid.UUID,
    checklist_id: uuid.UUID,
    item_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await TaskService(ctx).delete_checklist_item(task_id, checklist_id, item_id)
