"""System-actor task writes — the tasks module's published surface for automation (issue #27).

The request-facing ``TaskService`` authorizes against ``ctx.user`` and records that person in
the activity trail. An automation run has no person: the worker executes with a
``SystemContext`` (``user=None`` ⇒ the actor is the system, §16), and *authorization already
happened* when a permission-gated rule author saved the rule. These helpers are that path:
tenant-scoped writes (the context's session carries the RLS GUC), an activity line naming the
rule instead of a person, and the same bus events the interactive path emits — with the
caller's extra payload merged in, which is how the automation depth counter rides along.

Deliberately *not* here: recurrence hand-off on completion and due-date accountability —
request-side flows that reason about a human's intent. Everything a v1 rule action needs is.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from app.core.events import EmitContext, emit
from app.errors import AppError
from app.modules.tasks.models import Task, TaskActivity, TaskPriority, TaskStatus


async def _record(
    ctx: EmitContext, task_id: uuid.UUID, action: str, actor_name: str | None, payload: dict
) -> None:
    ctx.session.add(
        TaskActivity(
            org_id=ctx.org.id,
            task_id=task_id,
            actor_user_id=None,  # NULL actor = the system; the name says which automation.
            actor_name=actor_name,
            action=action,
            payload=payload,
        )
    )
    await ctx.session.flush()


async def _emit(
    ctx: EmitContext,
    event: str,
    task: Task,
    recipients: list[uuid.UUID],
    params: dict[str, Any] | None = None,
    extra_payload: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "task_id": task.id,
        "title": task.title,
        "_recipients": recipients,
    }
    payload.update(params or {})
    payload.update(extra_payload or {})
    await emit(event, ctx, payload)


async def _task_or_error(ctx: EmitContext, task_id: uuid.UUID) -> Task:
    task = await ctx.session.scalar(
        select(Task).where(Task.org_id == ctx.org.id, Task.id == task_id)
    )
    if task is None:
        raise AppError("not_found", "errors.not_found", status_code=404)
    return task


async def create_task_system(
    ctx: EmitContext,
    *,
    title: str,
    company_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    assignee_user_id: uuid.UUID | None = None,
    description: str | None = None,
    priority: str = TaskPriority.NORMAL.value,
    actor_name: str | None = None,
    extra_payload: dict[str, Any] | None = None,
) -> Task:
    if priority not in {p.value for p in TaskPriority}:
        raise AppError("validation", "errors.validation", status_code=422)
    max_position = float(
        await ctx.session.scalar(
            select(func.max(Task.position)).where(Task.org_id == ctx.org.id)
        )
        or 0.0
    )
    task = Task(
        org_id=ctx.org.id,
        title=title,
        description=description,
        company_id=company_id,
        project_id=project_id,
        assignee_user_id=assignee_user_id,
        status=TaskStatus.OPEN.value,
        priority=priority,
        position=max_position + 1024.0,
    )
    ctx.session.add(task)
    await ctx.session.flush()
    await _record(ctx, task.id, "created", actor_name, {})
    await _emit(
        ctx,
        "task.created",
        task,
        [],
        {
            "status": task.status,
            "company_id": task.company_id,
            "project_id": task.project_id,
        },
        extra_payload,
    )
    if task.assignee_user_id is not None:
        await _emit(ctx, "task.assigned", task, [task.assignee_user_id], None, extra_payload)
    return task


async def set_task_status_system(
    ctx: EmitContext,
    task_id: uuid.UUID,
    status: str,
    *,
    actor_name: str | None = None,
    extra_payload: dict[str, Any] | None = None,
) -> Task:
    if status not in {s.value for s in TaskStatus}:
        raise AppError("validation", "errors.validation", status_code=422)
    task = await _task_or_error(ctx, task_id)
    old_status = task.status
    if old_status == status:
        return task
    task.status = status
    if status == TaskStatus.DONE.value:
        task.completed_at = datetime.now(UTC)
    elif old_status == TaskStatus.DONE.value:
        task.completed_at = None
    await ctx.session.flush()
    await _record(ctx, task.id, "status_changed", actor_name, {"from": old_status, "to": status})
    await _emit(
        ctx,
        "task.status_changed",
        task,
        [task.assignee_user_id] if task.assignee_user_id else [],
        {"from": old_status, "to": status},
        extra_payload,
    )
    return task


async def assign_task_system(
    ctx: EmitContext,
    task_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    actor_name: str | None = None,
    extra_payload: dict[str, Any] | None = None,
) -> Task:
    task = await _task_or_error(ctx, task_id)
    if task.assignee_user_id == user_id:
        return task
    task.assignee_user_id = user_id
    await ctx.session.flush()
    await _record(ctx, task.id, "updated", actor_name, {"changed": ["assignee_user_id"]})
    await _emit(ctx, "task.assigned", task, [user_id], None, extra_payload)
    return task
