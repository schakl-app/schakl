"""Executable AI tools this module contributes (#127, #129; CLAUDE.md §6, §12).

Each entry is an :class:`AIToolSpec` whose handler runs under the caller's ``RequestContext``:
every query goes through the tenant-scoped repository and the same permission set as the REST
endpoint it shadows, so a tool can never answer across tenants or beyond the caller's role.
Read-only by design — three grounding lookups (find / mine / for_company) plus the
day-reconstruction feed (#129) behind "what did I do on tasks that day".
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import Select, and_, or_, select

from app.core.ai.tools import AIToolSpec, Source, ToolResult
from app.core.tenancy import RequestContext
from app.errors import AppError
from app.modules.tasks.models import Task, TaskActivity
from app.modules.tasks.statuses import load_statuses, non_terminal_keys

_READ = "tasks.task.read"


def _validation_error() -> AppError:
    return AppError("validation", "errors.validation", status_code=422)


def _uuid_arg(args: dict[str, Any], key: str) -> uuid.UUID | None:
    """A UUID argument the model supplied, or ``None``; garbage is a 422 like any bad field."""
    value = args.get(key)
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise _validation_error() from exc


def _readable(ctx: RequestContext, stmt: Select[tuple[Task]]) -> Select[tuple[Task]]:
    """Refine a task select to what the caller may read (§15).

    ``:own`` on the read grant means **assignee** — the same word ``_ensure_task_writable``
    gives the write grant. ``tasks.task.read`` is unscoped today, so every holder passes the
    ``any`` check; if the catalog ever scopes it, an ``:own``-only reader is narrowed to the
    tasks assigned to them here instead of seeing the whole board.
    """
    if ctx.can(_READ, scope="any"):
        return stmt
    return stmt.where(Task.assignee_user_id == ctx.user.id)


def _row(task: Task) -> dict[str, Any]:
    return {
        "id": str(task.id),
        "title": task.title,
        "status": task.status,
        "project_id": str(task.project_id) if task.project_id else None,
        "company_id": str(task.company_id) if task.company_id else None,
        "due": task.due_date.isoformat() if task.due_date else None,
    }


def _result(tasks: Sequence[Task]) -> ToolResult:
    return ToolResult(
        data={"tasks": [_row(t) for t in tasks]},
        sources=tuple(Source(type="task", id=str(t.id), label=t.title) for t in tasks),
    )


async def _find(ctx: RequestContext, args: dict[str, Any]) -> ToolResult:
    stmt = _readable(ctx, ctx.repo(Task).scoped_select())
    query = (args.get("query") or "").strip()
    if query:
        stmt = stmt.where(Task.title.ilike(f"%{query}%"))
    company_id = _uuid_arg(args, "company_id")
    if company_id is not None:
        stmt = stmt.where(Task.company_id == company_id)
    project_id = _uuid_arg(args, "project_id")
    if project_id is not None:
        stmt = stmt.where(Task.project_id == project_id)
    # "Finished" is the org's own vocabulary (issue #62), never the literal string "done".
    open_keys = non_terminal_keys(await load_statuses(ctx.session, ctx.org.id))
    stmt = stmt.order_by(
        Task.status.in_(open_keys).desc(),  # unfinished work first
        Task.due_date.asc().nulls_last(),
        Task.created_at.desc(),
    ).limit(10)
    tasks = (await ctx.session.execute(stmt)).scalars().all()
    return _result(tasks)


async def _mine(ctx: RequestContext, args: dict[str, Any]) -> ToolResult:
    # ``TaskService.my_open`` (the /tasks/mine endpoint) minus the label/checklist/comment
    # aggregate queries the tool's answer shape doesn't carry.
    open_keys = non_terminal_keys(await load_statuses(ctx.session, ctx.org.id))
    stmt = (
        ctx.repo(Task)
        .scoped_select()
        .where(Task.assignee_user_id == ctx.user.id, Task.status.in_(open_keys))
        .order_by(Task.due_date.asc().nulls_last(), Task.created_at.desc())
        .limit(20)
    )
    tasks = (await ctx.session.execute(stmt)).scalars().all()
    return _result(tasks)


async def _for_company(ctx: RequestContext, args: dict[str, Any]) -> ToolResult:
    company_id = _uuid_arg(args, "company_id")
    if company_id is None:
        raise _validation_error()
    open_keys = non_terminal_keys(await load_statuses(ctx.session, ctx.org.id))
    stmt = (
        _readable(ctx, ctx.repo(Task).scoped_select())
        .where(Task.company_id == company_id, Task.status.in_(open_keys))
        .order_by(Task.due_date.asc().nulls_last(), Task.created_at.desc())
        .limit(20)
    )
    tasks = (await ctx.session.execute(stmt)).scalars().all()
    return _result(tasks)


async def _my_activity(ctx: RequestContext, args: dict[str, Any]) -> ToolResult:
    try:
        day = date.fromisoformat(str(args.get("date") or ""))
    except ValueError as exc:
        raise _validation_error() from exc
    # A plain UTC calendar day: ``created_at`` is TIMESTAMPTZ, and a rough day window is enough
    # for reconstructing a working day — this is a memory aid, not a balance calculation.
    start = datetime.combine(day, time.min, tzinfo=UTC)
    end = start + timedelta(days=1)
    # One query, so a row matching both arms (they changed the status of their own task) shows
    # up exactly once: everything the user did themselves, plus status changes on their tasks.
    rows = (
        await ctx.session.execute(
            select(TaskActivity, Task)
            .join(Task, Task.id == TaskActivity.task_id)
            .where(
                TaskActivity.org_id == ctx.org.id,
                TaskActivity.created_at >= start,
                TaskActivity.created_at < end,
                or_(
                    TaskActivity.actor_user_id == ctx.user.id,
                    and_(
                        TaskActivity.action == "status_changed",
                        Task.assignee_user_id == ctx.user.id,
                    ),
                ),
            )
            .order_by(TaskActivity.created_at.asc())
            .limit(40)
        )
    ).all()
    activity = [
        {
            "task_id": str(task.id),
            "task_title": task.title,
            "company_id": str(task.company_id) if task.company_id else None,
            "project_id": str(task.project_id) if task.project_id else None,
            "action": entry.action,
            "at": entry.created_at.isoformat(),
        }
        for entry, task in rows
    ]
    return ToolResult(data={"activity": activity})


TASK_MCP_TOOLS: list[AIToolSpec] = [
    AIToolSpec(
        name="tasks.find",
        description=(
            "Search this workspace's tasks by title. Optionally filter by company_id and/or "
            "project_id (UUIDs). Returns up to 10 matches, unfinished tasks first. Use this to "
            "locate a task before answering questions about it."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": ["string", "null"]},
                "company_id": {"type": ["string", "null"]},
                "project_id": {"type": ["string", "null"]},
            },
            "required": [],
            "additionalProperties": False,
        },
        handler=_find,
        permission=_READ,
    ),
    AIToolSpec(
        name="tasks.mine",
        description=(
            "List the current user's open (unfinished) tasks, nearest due date first, up to 20. "
            "Use this for questions like 'what is on my plate' or 'what should I do today'."
        ),
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        handler=_mine,
        permission=_READ,
    ),
    AIToolSpec(
        name="tasks.for_company",
        description=(
            "List the open (unfinished) tasks attached to one company, nearest due date first, "
            "up to 20. Pass company_id as a UUID; resolve a company name to its id first."
        ),
        input_schema={
            "type": "object",
            "properties": {"company_id": {"type": "string"}},
            "required": ["company_id"],
            "additionalProperties": False,
        },
        handler=_for_company,
        permission=_READ,
    ),
    AIToolSpec(
        name="tasks.my_activity",
        description=(
            "Reconstruct what the current user did on tasks during one UTC calendar day: every "
            "task action they performed (status changes, comments, checklist ticks, edits), "
            "plus status changes on tasks assigned to them. Pass date as YYYY-MM-DD. Use this "
            "to help draft a daily log or fill in a timesheet."
        ),
        input_schema={
            "type": "object",
            "properties": {"date": {"type": "string", "description": "YYYY-MM-DD"}},
            "required": ["date"],
            "additionalProperties": False,
        },
        handler=_my_activity,
        permission=_READ,
    ),
]
