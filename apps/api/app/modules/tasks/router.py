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
    ChecklistDuplicate,
    ChecklistItemCreate,
    ChecklistItemOrder,
    ChecklistItemRead,
    ChecklistItemUpdate,
    ChecklistOrder,
    ChecklistOrderRead,
    ChecklistRead,
    ChecklistTemplateCreate,
    ChecklistTemplateRead,
    ChecklistTemplateUpdate,
    ChecklistUpdate,
    CommentCreate,
    CommentRead,
    CommentUpdate,
    DashboardMineSummary,
    DashboardTaskGroups,
    LabelCreate,
    LabelRead,
    LabelUpdate,
    LinkCreate,
    LinkRead,
    RecurrencePreview,
    RecurrencePreviewRead,
    StatusCreate,
    StatusRead,
    StatusUpdate,
    TaskAIStatusRead,
    TaskChecklistGenerateRequest,
    TaskCreate,
    TaskDetail,
    TaskLabelsSet,
    TaskListItem,
    TaskRead,
    TaskReviseRequest,
    TaskReviseResult,
    TaskUpdate,
    TemplateApply,
    TemplateCreate,
    TemplateRead,
    TemplateUpdate,
)
from app.modules.tasks.service import DASHBOARD_GROUP_ROWS, TaskService
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
    unlinked: bool = Query(False, description="Only tasks with no client and no project"),
    assignee_user_id: uuid.UUID | None = Query(None),
    assignee_contact_id: uuid.UUID | None = Query(None),
    assigned_to: Literal["contact", "agency"] | None = Query(
        None,
        description=(
            "Whose work it is: `contact` — assigned to one of the client's own people; "
            "`agency` — everything else (assigned to staff, or to nobody yet). The client's "
            "homepage draws the two as separate tiles, and a specific person is a different "
            "question (`assignee_contact_id` / `assignee_user_id`)."
        ),
    ),
    status: str | None = Query(None, max_length=50, description="A configured status key"),
    open_only: bool = Query(
        False,
        alias="open",
        description="Only tasks in a non-terminal status — the working set, any status key",
    ),
    label_id: uuid.UUID | None = Query(None),
    due: Literal["overdue", "today", "week", "later"] | None = Query(None),
    due_from: date | None = Query(None, description="Deadline window start (the Agenda feed)"),
    due_to: date | None = Query(None, description="Deadline window end (inclusive)"),
    q: str | None = Query(None, max_length=200),
    unnamed: bool | None = Query(
        None,
        description=(
            "Only tasks nobody named (create-then-edit rows never finished), or only named "
            "ones. Omitted returns both."
        ),
    ),
    undated: bool | None = Query(
        None,
        description=(
            "Only tasks with no deadline (rows written before the date became required, "
            "#392), or only dated ones. Omitted returns both."
        ),
    ),
    sort: str | None = Query(
        None,
        description=(
            "due | title | due_date | priority | status | assignee | …, '-' desc. "
            "`due` is the urgency reading the board opens on: deadline first, then priority, "
            "highest first."
        ),
    ),
    meta: bool = Query(True, description="Include label/checklist/comment aggregates"),
    hours: bool = Query(
        False,
        description=(
            "Include the hour budget's burn (logged/remaining minutes); costs one grouped "
            "query. Omitted for a caller without time.entry.read rather than refused."
        ),
    ),
    count: bool = Query(True, description="Compute total; set false for name-only lookups"),
    ctx: RequestContext = Depends(require_context),
) -> Page[TaskListItem]:
    items, total = await TaskService(ctx).list(
        limit=limit,
        offset=offset,
        company_id=company_id,
        project_id=project_id,
        unlinked=unlinked,
        assignee_user_id=assignee_user_id,
        assignee_contact_id=assignee_contact_id,
        assigned_to=assigned_to,
        status=status,
        open_only=open_only,
        label_id=label_id,
        due=due,
        due_from=due_from,
        due_to=due_to,
        q=q,
        unnamed=unnamed,
        undated=undated,
        sort=sort,
        with_meta=meta,
        hours=hours,
        count=count,
    )
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get(
    "/dashboard-groups",
    response_model=DashboardTaskGroups,
    dependencies=[require_permission("tasks.task.read")],
)
async def dashboard_groups(
    limit: int = Query(DASHBOARD_GROUP_ROWS, ge=1, le=100),
    ctx: RequestContext = Depends(require_context),
) -> DashboardTaskGroups:
    """Open-task counts grouped by project, then company, ranked by urgency (#398, #407).

    Capped, and the envelope says by how much: a dashboard tile listing every project an
    agency runs is a scroll rather than a summary.
    """
    return await TaskService(ctx).dashboard_groups(limit=limit)


@router.get(
    "/dashboard-mine",
    response_model=DashboardMineSummary,
    dependencies=[require_permission("tasks.task.read")],
)
async def dashboard_mine(
    limit: int = Query(20, ge=1, le=100),
    ctx: RequestContext = Depends(require_context),
) -> DashboardMineSummary:
    """The personal task tile: a page of rows, plus the bucket counts of the whole set (#407)."""
    return await TaskService(ctx).dashboard_mine(limit=limit)


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
    # The repository exists to be poured into a task's checklist, so *editing a task* is the read
    # bar — not `tasks.task.read`, which a portal client holds (#193). A client may read the tasks
    # of their own companies; the agency's internal process library is not part of that, and it is
    # the surface whose write controls the portal was rendering (#244).
    # The floor admits `:own`, so a member still fills the "van sjabloon" picker on their own task.
    dependencies=[require_permission("tasks.task.write")],
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
    # Same rule as the checklist repository above: a task template spells out the agency's
    # internal process — item titles, descriptions, who gets assigned — and reading it is what
    # applying one needs (#253 already gates the applier UI on this key). `tasks.task.read` put
    # that list within reach of a portal client, which it never should have been.
    dependencies=[require_permission("tasks.template.apply")],
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
    dependencies=[require_permission("tasks.template.apply")],
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
# Recurrence preview (#335) — a literal path, so registered before ``/{task_id}``
# --------------------------------------------------------------------------- #
@router.post(
    "/recurrence/preview",
    response_model=RecurrencePreviewRead,
    # `tasks.task.write`, not `.read`: the only place this is ever reached from is the rule
    # editor, which is edit mode, which is a write. Declaring the read would have widened the
    # one POST surface a client-role login can reach (`tests/test_rbac_deny_by_default.py`'s
    # sweep) for a route no client will ever open — deny-by-default is about the tightest key
    # the caller genuinely needs, and anyone composing a repeat rule holds this one.
    dependencies=[require_permission("tasks.task.write")],
)
async def preview_recurrence(
    payload: RecurrencePreview,
    ctx: RequestContext = Depends(require_context),
) -> RecurrencePreviewRead:
    """What the rule being composed resolves to — "Volgende taak: za 13 sep 2026".

    ``POST /leave/requests/preview``'s precedent (#48): show the number that will be stored, and
    why, rather than letting the browser re-derive it. Clamping, leap years, the anchor rules and
    the org's own "today" all live server-side; a preview that re-implemented them in TypeScript
    would be a second opinion about a question the API already answers (#312).

    A read — it stores nothing. It exists so a rule can be *checked* before it is saved.
    """
    return await TaskService(ctx).preview_recurrence(payload)


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


@router.get(
    "/{task_id}/ai-status",
    response_model=TaskAIStatusRead,
    dependencies=[require_permission("tasks.task.read")],
)
async def get_task_ai_status(
    task_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> TaskAIStatusRead:
    """Just the "is schakl still filling this in?" flag (#327).

    Its own endpoint because it is *polled*. The card shows a live pill while an email is being
    read, and re-fetching ``GET /{task_id}`` every few seconds to learn one short string would
    drag the whole detail — labels, checklists, every comment and the activity trail — across
    the wire each time, for a screen that already has all of it. One indexed row, one column
    (docs/PERFORMANCE.md: a row carries only what its screen draws).
    """
    return await TaskService(ctx).ai_status(task_id)


@router.post(
    "/{task_id}/ai/revise",
    response_model=TaskReviseResult,
    dependencies=[require_permission("tasks.task.write")],
)
async def revise_task_with_ai(
    task_id: uuid.UUID,
    payload: TaskReviseRequest,
    ctx: RequestContext = Depends(require_context),
) -> TaskReviseResult:
    """Change this task in words: one typed instruction, applied as the caller.

    "Add a step for the DNS change, move the deadline to Friday, note that the client wants it
    in blue." The route is the task write it is (§15); the service asks ``ai.use`` and the
    ``:own`` rule before a token is spent (``tasks/assist.py``).
    """
    from app.modules.tasks.assist import revise_task

    return await revise_task(ctx, task_id, payload)


@router.post(
    "/{task_id}/ai/checklist",
    response_model=ChecklistRead,
    status_code=201,
    dependencies=[require_permission("tasks.task.write")],
)
async def generate_checklist_with_ai(
    task_id: uuid.UUID,
    payload: TaskChecklistGenerateRequest,
    ctx: RequestContext = Depends(require_context),
) -> ChecklistRead:
    """Write this task's steps from its title and notes, as one new checklist."""
    from app.modules.tasks.assist import generate_checklist

    return await generate_checklist(ctx, task_id, payload)


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


@router.post(
    "/{task_id}/checklists/order",
    response_model=ChecklistOrderRead,
    dependencies=[require_permission("tasks.task.write")],
)
async def reorder_checklists(
    task_id: uuid.UUID,
    payload: ChecklistOrder,
    ctx: RequestContext = Depends(require_context),
) -> ChecklistOrderRead:
    """Set the order of a task's checklists in one call (``ChecklistOrder`` for the contract).

    ``/order`` rather than a ``PATCH`` per row: the two sibling paths that carry a
    ``{checklist_id}`` segment are ``PATCH`` and ``DELETE``, so no ``POST`` can be ambiguous
    with it, and a whole order is what both the drag and the arrow buttons produce.
    """
    return await TaskService(ctx).reorder_checklists(task_id, payload)


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


@router.post(
    "/{task_id}/checklists/{checklist_id}/duplicate",
    response_model=ChecklistRead,
    status_code=201,
    dependencies=[require_permission("tasks.task.write")],
)
async def duplicate_checklist(
    task_id: uuid.UUID,
    checklist_id: uuid.UUID,
    payload: ChecklistDuplicate,
    ctx: RequestContext = Depends(require_context),
) -> ChecklistRead:
    """Copy a checklist beside its source, items and all — a second run of the same steps."""
    return await TaskService(ctx).duplicate_checklist(task_id, checklist_id, payload)


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


@router.post(
    "/{task_id}/checklists/{checklist_id}/items/order",
    response_model=ChecklistOrderRead,
    dependencies=[require_permission("tasks.task.write")],
)
async def reorder_checklist_items(
    task_id: uuid.UUID,
    checklist_id: uuid.UUID,
    payload: ChecklistItemOrder,
    ctx: RequestContext = Depends(require_context),
) -> ChecklistOrderRead:
    """Set the order of one checklist's items in one call."""
    return await TaskService(ctx).reorder_checklist_items(task_id, checklist_id, payload)


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
