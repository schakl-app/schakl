"""CSV import/export shape for tasks (issue #77, settings hub round).

**Create-only import** (``natural_keys=()``): task titles legitimately repeat, so there is
no honest column to upsert on — every valid row creates, through the module's own service
(same status validation, activity line and events as the form). ``status`` is tenant data
(#62), so it is a plain text column the service validates, never a frozen options list.
"""

from __future__ import annotations

from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

from sqlalchemy import column, select, table

from app.core.impex import ImpexColumn, ImpexDescriptor
from app.core.impex.resolvers import name_or_id_resolver, no_natural_key, resolve_member_email
from app.core.tenancy import RequestContext
from app.modules.tasks.models import TaskPriority
from app.modules.tasks.schemas import TaskCreate
from app.modules.tasks.service import TaskService

_companies = table("companies", column("id"), column("name"), column("org_id"))
_projects = table("projects", column("id"), column("name"), column("org_id"))
_users = table("users", column("id"), column("email"))


async def _names(ctx: RequestContext, ref, ids: set) -> dict:
    if not ids:
        return {}
    label = ref.c.name if "name" in ref.c else ref.c.email
    rows = await ctx.session.execute(
        select(ref.c.id, label).where(ref.c.id.in_(ids))
        if "org_id" not in ref.c
        else select(ref.c.id, label).where(ref.c.org_id == ctx.org.id, ref.c.id.in_(ids))
    )
    return dict(list(rows))


async def _fetch_page(
    ctx: RequestContext, *, limit: int, offset: int, filters: dict[str, Any]
) -> Sequence[Any]:
    items, _ = await TaskService(ctx).list(
        limit=limit,
        offset=offset,
        q=filters.get("q"),
        status=filters.get("status"),
        company_id=filters.get("company_id"),
        project_id=filters.get("project_id"),
        assignee_user_id=ctx.user.id if filters.get("mine") else None,
        sort=filters.get("sort"),
        with_meta=False,
        count=False,
    )
    companies = await _names(ctx, _companies, {t.company_id for t in items if t.company_id})
    projects = await _names(ctx, _projects, {t.project_id for t in items if t.project_id})
    users = await _names(
        ctx, _users, {t.assignee_user_id for t in items if t.assignee_user_id}
    )
    # TaskListItem is a pydantic model, not ORM — wrap each row for the export getters.
    return [
        SimpleNamespace(
            **item.model_dump(),
            company=companies.get(item.company_id),
            project=projects.get(item.project_id),
            assignee=users.get(item.assignee_user_id),
        )
        for item in items
    ]


async def _create(ctx: RequestContext, values: dict[str, Any]) -> Any:
    return await TaskService(ctx).create(
        TaskCreate(
            title=values["title"],
            description=values.get("description"),
            status=values.get("status") or "open",
            priority=TaskPriority(values["priority"])
            if values.get("priority")
            else TaskPriority.NORMAL,
            company_id=values.get("company_id"),
            project_id=values.get("project_id"),
            assignee_user_id=values.get("assignee_user_id"),
            due_date=values["due_date"],
            allocated_minutes=int(float(values["allocated_minutes"]))
            if values.get("allocated_minutes")
            else None,
            custom=values.get("custom") or {},
        )
    )


async def _update(ctx: RequestContext, task: Any, values: dict[str, Any]) -> None:
    raise NotImplementedError  # unreachable: create-only (natural_key=None)


TASK_IMPEX = ImpexDescriptor(
    entity_type="task",
    read_permission="tasks.task.read",
    write_permission="tasks.task.write",
    natural_keys=(),
    filters=("q", "status", "company_id", "project_id", "mine", "sort"),
    columns=(
        ImpexColumn("title", required=True),
        # Tenant-defined statuses (#62): validated by the service, not a frozen list here.
        ImpexColumn("status", clearable=False),
        ImpexColumn(
            "priority",
            data_type="select",
            clearable=False,
            options=tuple(priority.value for priority in TaskPriority),
            option_label_key="tasks.priority.{option}",
        ),
        # Required, the way the deadline below is: a task is always a client's, and a sheet
        # that names none for a row is refused *on that row* in the preview rather than as a
        # request-level 422 the report cannot point at (CLAUDE.md §17, #289). A project column
        # alone would do at the service (it takes the client off the project), but a preview
        # cannot promise that for a project cell it has not resolved yet, so the client is
        # asked for outright.
        ImpexColumn(
            "company",
            data_type="fk",
            field="company_id",
            required=True,
            getter=lambda t: getattr(t, "company", None),
        ),
        ImpexColumn(
            "project",
            data_type="fk",
            field="project_id",
            getter=lambda t: getattr(t, "project", None),
        ),
        # An empty cell is not "nobody": a task always has someone on it, and the service hands
        # a row that names no one to the project's responsible, else the client's, else the
        # person doing the import (`TaskService.create`). Deliberately not `required=True` like
        # the deadline below — a spreadsheet from a client's own tracker rarely knows our
        # colleagues by e-mail, and refusing every such row would make the importer type their
        # own address five hundred times to say what the default already says.
        ImpexColumn(
            "assignee",
            data_type="fk",
            field="assignee_user_id",
            getter=lambda t: getattr(t, "assignee", None),
        ),
        # Required (#392), which for an import means *present in the header and non-empty in
        # every row* — and therefore named, per row, in the preview rather than surfacing as a
        # request-level 422 the report cannot point at (CLAUDE.md §17, #289). A default would
        # have been the wrong answer here: unlike the recurrence generator or an automation
        # rule, an import has a human reading a preview, so the honest move is to refuse and
        # say which rows.
        ImpexColumn("due_date", data_type="date", required=True),
        ImpexColumn("allocated_minutes", data_type="number"),
        ImpexColumn("description"),
    ),
    fetch_page=_fetch_page,
    find_existing=no_natural_key,
    create_row=_create,
    update_row=_update,
    fk_resolvers={
        "company": name_or_id_resolver("companies"),
        "project": name_or_id_resolver("projects"),
        "assignee": resolve_member_email,
    },
)
