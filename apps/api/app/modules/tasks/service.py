"""Business logic for tasks — all DB access via the tenant-scoped repository (CLAUDE.md §6).

Besides task CRUD this hosts the card satellites: labels, checklists, comments, and the
append-only activity log. Every mutation records who did what so the detail view can show
a Trello-style history.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, column, func, or_, select, table
from sqlalchemy import text as sql_text
from sqlalchemy.orm import aliased

from app.core.auth.models import User
from app.core.directory import visible_ids
from app.core.entitlements import OrgPlan, refusal_for, sku_writable
from app.core.events import emit
from app.core.models import Membership
from app.core.parent import ensure_parent_in_tenant
from app.core.richtext import (
    extract_contact_mention_ids,
    extract_mention_ids,
    extract_task_mention_ids,
    markdown_excerpt,
    sanitize_markdown,
)
from app.core.sorting import apply_sort, user_sort_name
from app.core.tenancy import RequestContext, TenantScopedRepository
from app.core.timezone import org_today
from app.core.urls import reject_dangerous_url
from app.errors import AppError
from app.modules.tasks import recurrence as rec_mod
from app.modules.tasks.models import (
    RecurrenceMode,
    Task,
    TaskActivity,
    TaskChecklist,
    TaskChecklistItem,
    TaskChecklistTemplate,
    TaskComment,
    TaskLabel,
    TaskLabelLink,
    TaskLink,
    TaskPriority,
    TaskStatusDef,
)
from app.modules.tasks.scheduling import TaskScheduleService
from app.modules.tasks.schemas import (
    ActivityRead,
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
    DashboardTaskGroup,
    DashboardTaskItem,
    LabelCreate,
    LabelRead,
    LabelUpdate,
    LinkCreate,
    LinkRead,
    RecurrencePreview,
    RecurrencePreviewRead,
    StatusCreate,
    StatusUpdate,
    TaskAIStatusRead,
    TaskCreate,
    TaskDetail,
    TaskListItem,
    TaskLogTime,
    TaskRead,
    TaskUpdate,
    TemplateChecklistItem,
)
from app.modules.tasks.statuses import (
    default_key,
    load_statuses,
    non_terminal_keys,
    status_order,
    terminal_keys,
)

# Fields whose change is worth an ``updated`` activity entry (position/derived ones are noise).
_TRACKED_FIELDS = (
    "title",
    "description",
    "priority",
    "due_date",
    "allocated_minutes",
    "assignee_user_id",
    "assignee_contact_id",
    "company_id",
    "project_id",
    "recurrence",
    "requires_interaction",
    "visible_to_client",
)


def _rank(column: Any, order: Sequence[str]) -> Any:
    """Order a small closed vocabulary by *meaning*, not by spelling.

    ``priority`` and ``status`` are stored as strings, so a plain ``ORDER BY`` files them
    alphabetically — ``done, in_progress, open`` for a workflow that runs the other way, and
    ``high, low, normal`` for a scale nobody reads that way. The rank makes ascending mean
    "earliest in the workflow" and "least urgent", so ``-priority`` puts the fires on top.
    """
    return case({value: i for i, value in enumerate(order)}, value=column, else_=len(order))


# Columns a client may sort by; anything else in ``?sort=`` is rejected (app/core/sorting.py).
# ``title`` sorts case-insensitively, or Postgres' collation files every lowercase title after
# every uppercase one. ``assignee`` orders by the employee's display name, never by their user id
# — a list sorted by a person has to read that way (docs/UX.md).
_PRIORITY_ORDER = (TaskPriority.LOW.value, TaskPriority.NORMAL.value, TaskPriority.HIGH.value)
# Newest comments the task card carries. The activity trail beside it has always capped at 50;
# the discussion had no bound at all, so a task people talk on for a year grew its detail
# response without limit (docs/PERFORMANCE.md — bound every read).
_COMMENT_CAP = 200
_dashboard_projects = table(
    "projects",
    column("id"),
    column("org_id"),
    column("name"),
    column("company_id"),
)
_dashboard_companies = table(
    "companies",
    column("id"),
    column("org_id"),
    column("name"),
)
# The client behind a *project* row, joined a second time: a project's own name does not say
# whose it is (see ``DashboardTaskGroup.company_name``).
_dashboard_project_companies = _dashboard_companies.alias("dashboard_project_companies")

# Status is no longer a fixed vocabulary, so its rank is built per request from the org's
# configured order (see ``list``). Everything else is static.
SORTABLE = {
    "title": func.lower(Task.title),
    "due_date": Task.due_date,
    "priority": _rank(Task.priority, _PRIORITY_ORDER),
    "assignee": user_sort_name(Task.assignee_user_id),
    "created_at": Task.created_at,
    "updated_at": Task.updated_at,
}


def _display_name(user: User | None) -> str | None:
    if user is None:
        return None
    return user.full_name or user.email


# The `@[Name](mention:<uuid>)` marker lives in core (`richtext.extract_mention_ids`) since
# #151 — interactions parse the same syntax, and two copies of the regex would drift.
_extract_mentions = extract_mention_ids


def _attribution(live: User | None, snapshot: str | None) -> tuple[str | None, bool]:
    """How a stored row names the person behind it: ``(display name, actor was deleted)``.

    The live account wins while it exists, so a rename shows through the whole history at once.
    Once it is gone the FK reads ``NULL`` (``ON DELETE SET NULL``) and only the snapshot taken at
    write time still knows who acted — which is also the one thing separating a departed human
    from the system, whose rows never carried a name at all (issue #64).
    """
    if live is not None:
        return _display_name(live), False
    return snapshot, snapshot is not None


def _excerpt(body: str, limit: int = 140) -> str:
    """A comment's first line, short enough to read in a notification list.

    The body is markdown now (issue #66), so it is flattened to plain text *before* the length
    cap — otherwise the bell dropdown shows literal ``**bold**`` / ``[label](url)`` syntax, and
    cutting by character count could sever a link mid-``()``. That rule now lives in
    :func:`markdown_excerpt`, shared with the contactmoment timeline's teaser; a comment with no
    words left reads as empty here rather than as "no excerpt".
    """
    return markdown_excerpt(body, limit) or ""


def _rich_items(
    rich: list[dict[str, Any]] | None, legacy: list[str] | None
) -> list[dict[str, Any]]:
    """A checklist template's items in the reshaped ``{title, description}`` form (issue #66).

    Reads the authoritative ``*_rich`` column, falling back to the legacy title-only array only for
    the brief window between the schema add and the backfill — after which ``*_rich`` is always set.
    """
    if rich:
        return rich
    return [{"title": title, "description": None} for title in (legacy or [])]


#: Ceiling on the rows one reorder renumbers. Every read is capped (CLAUDE.md §9); it sits above
#: the payload's own ``max_length`` so a full order is never silently truncated to a prefix.
_ORDER_CAP = 1000


def _renumber[PositionedT: (TaskChecklist, TaskChecklistItem)](
    rows: Sequence[PositionedT], ordered_ids: list[uuid.UUID]
) -> list[PositionedT]:
    """Assign ``position`` 0..n-1 following ``ordered_ids``, then whatever it did not name.

    The payload is a *statement about order*, not a statement about membership: rows the caller
    omitted keep their relative order after the named ones (``ChecklistOrder`` says why), so a
    row created after the page loaded is appended instead of vanishing or 409-ing the save. An id
    that belongs to nothing here is a 404 — the same answer reading it gets, so an ordering call
    cannot probe for another task's checklists (CLAUDE.md §15).
    """
    by_id = {row.id: row for row in rows}
    if any(entity_id not in by_id for entity_id in ordered_ids):
        raise AppError("not_found", "errors.not_found", status_code=404)
    if len(set(ordered_ids)) != len(ordered_ids):
        raise AppError(
            "validation",
            "errors.validation",
            status_code=422,
            fields={"order": "errors.duplicate"},
        )
    named = set(ordered_ids)
    ordered = [by_id[entity_id] for entity_id in ordered_ids]
    ordered += [row for row in rows if row.id not in named]
    for index, row in enumerate(ordered):
        row.position = index
    return ordered


class TaskService:
    class _PortalTaskRepository(TenantScopedRepository):
        """The task repo a portal login gets (#266) — the invoicing/contacts pattern.

        It defers to ``Task.__portal_horizon_clause__``: the client's own companies, **and**
        the ``visible_to_client`` tick.

        It overrides ``horizon_condition``, not ``_scoped``. That distinction is the whole
        point: ``_scoped()`` feeds the reads (``get_or_404``, ``scoped_select``) but
        ``scoped_count_select()`` and ``count()`` build their own statement and AND the
        horizon on directly, so an override one layer too low left every *count* reading the
        looser staff rule. The company panel counted with it — a client saw "Taken (12)" over
        a list of three (#285's failure mode (2), reached through a subclass seam rather than
        a hand-built query).
        """

        def horizon_condition(self):  # noqa: ANN202 — mirrors the base signature
            clause = getattr(self.model, "__portal_horizon_clause__", None)
            if clause is None:  # pragma: no cover — Task declares one; stay strict if it stops
                return super().horizon_condition()
            return clause(self.company_scope)

    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = (
            self._PortalTaskRepository(
                ctx.session, ctx.org.id, Task, company_scope=ctx.company_scope
            )
            if ctx.is_portal
            else ctx.repo(Task)
        )

    # --- access scoping (issue #19) ------------------------------------------ #
    async def _writable_task_or_403(self, task_id: uuid.UUID) -> Task:
        """Load a task the caller may edit.

        ``tasks.task.write:own`` means **assignee** — that is the answer to #12: a person may
        edit the task assigned to them and nothing else. ``:any`` is the manager grant. 403, not
        404: tasks are readable by everyone who can read the module, so nothing is leaked.
        """
        task = await self.repo.get_or_404(task_id)
        self._ensure_task_writable(task)
        return task

    def _ensure_task_writable(self, task: Task) -> None:
        if self.ctx.can("tasks.task.write", scope="any"):
            return
        if task.assignee_user_id == self.ctx.user.id and self.ctx.can(
            "tasks.task.write", scope="own"
        ):
            return
        raise AppError("forbidden", "errors.forbidden", status_code=403)

    # ------------------------------------------------------------------ #
    # Activity
    # ------------------------------------------------------------------ #
    async def _record(
        self, task_id: uuid.UUID, action: str, payload: dict | None = None
    ) -> None:
        # Whoever is really at the keyboard, when the actor is an account they were handed
        # (#296). Snapshotted for the same reason the actor is.
        impersonator = self.ctx.impersonated_by
        self.ctx.session.add(
            TaskActivity(
                org_id=self.ctx.org.id,
                task_id=task_id,
                actor_user_id=self.ctx.user.id,
                # Snapshotted, so deleting the account doesn't hand this line to "System" (#64).
                actor_name=_display_name(self.ctx.user),
                impersonator_user_id=impersonator.id if impersonator else None,
                impersonator_name=_display_name(impersonator),
                action=action,
                payload=payload or {},
            )
        )
        await self.ctx.session.flush()

    async def _emit_task(
        self,
        event: str,
        task: Task,
        recipients: Sequence[uuid.UUID | None],
        params: dict[str, Any] | None = None,
    ) -> None:
        """Announce something that happened to a task (CLAUDE.md §6 — the bus, not an import).

        This module resolves its *own* audience; the notifications module adds the task's
        watchers, drops the actor and anyone who muted it, and applies each recipient's
        delivery preference. ``title`` is snapshotted so the line still reads after a rename.
        """
        payload: dict[str, Any] = {
            "task_id": task.id,
            "title": task.title,
            "_recipients": [r for r in recipients if r is not None],
        }
        payload.update(params or {})
        await emit(event, self.ctx, payload)

    # ------------------------------------------------------------------ #
    # List / aggregates
    # ------------------------------------------------------------------ #
    async def _list_items(self, tasks: Sequence[Task]) -> list[TaskListItem]:
        """Decorate tasks with label chips, checklist progress and comment counts."""
        items = [TaskListItem.model_validate(t) for t in tasks]
        task_ids = [t.id for t in tasks]
        if not task_ids:
            return items

        label_rows = (
            await self.ctx.session.execute(
                select(TaskLabelLink.task_id, TaskLabel)
                .join(TaskLabel, TaskLabel.id == TaskLabelLink.label_id)
                .where(
                    TaskLabelLink.org_id == self.ctx.org.id,
                    TaskLabelLink.task_id.in_(task_ids),
                )
                .order_by(TaskLabel.position.asc(), TaskLabel.name.asc())
            )
        ).all()
        labels_by_task: dict[uuid.UUID, list[LabelRead]] = {}
        for task_id, label in label_rows:
            labels_by_task.setdefault(task_id, []).append(LabelRead.model_validate(label))

        checklist_rows = (
            await self.ctx.session.execute(
                select(
                    TaskChecklist.task_id,
                    func.count(TaskChecklistItem.id),
                    func.count(TaskChecklistItem.id).filter(
                        TaskChecklistItem.done.is_(True)
                    ),
                )
                .join(
                    TaskChecklistItem,
                    TaskChecklistItem.checklist_id == TaskChecklist.id,
                )
                .where(
                    TaskChecklist.org_id == self.ctx.org.id,
                    TaskChecklist.task_id.in_(task_ids),
                )
                .group_by(TaskChecklist.task_id)
            )
        ).all()
        checklist_by_task = {row[0]: (int(row[2]), int(row[1])) for row in checklist_rows}

        comment_rows = (
            await self.ctx.session.execute(
                select(TaskComment.task_id, func.count())
                .where(
                    TaskComment.org_id == self.ctx.org.id,
                    TaskComment.task_id.in_(task_ids),
                )
                .group_by(TaskComment.task_id)
            )
        ).all()
        comments_by_task = {row[0]: int(row[1]) for row in comment_rows}

        for item in items:
            item.labels = labels_by_task.get(item.id, [])
            done, total = checklist_by_task.get(item.id, (0, 0))
            item.checklist_done = done
            item.checklist_total = total
            item.comment_count = comments_by_task.get(item.id, 0)
        return items

    async def _attach_hours(self, items: Sequence[TaskRead]) -> None:
        """The hour budget's burn for a page of tasks, in one grouped query (#313).

        Opt-in (``?hours=true``) because a row carries only what its screen draws, and gated on
        ``time.entry.read`` — **absent rather than refused** for a caller without it. This is an
        enrichment flag on a route they may otherwise call, so a 403 would break the ordinary
        task list for someone who simply may not read hours. The seeded ``client`` role holds
        ``tasks.task.read`` (the portal reads tasks) and never holds this, which is what keeps
        team-wide burned hours off a client's screen.

        Reused for the detail card, which asks for exactly one id. The time module is reached
        through its published service, imported here rather than at module scope: nothing
        outside this branch should drag `time` in, and a module never imports another's
        internals (CLAUDE.md §6).
        """
        if not items or not self.ctx.can("time.entry.read"):
            return
        from app.modules.time.service import TimeService

        logged = await TimeService(self.ctx).minutes_by_task([item.id for item in items])
        for item in items:
            minutes = logged.get(item.id)
            item.logged_minutes = minutes.total if minutes is not None else 0
            item.remaining_minutes = (
                None
                if item.allocated_minutes is None
                else item.allocated_minutes - item.logged_minutes
            )

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        company_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        unlinked: bool = False,
        assignee_user_id: uuid.UUID | None = None,
        assignee_contact_id: uuid.UUID | None = None,
        status: str | None = None,
        label_id: uuid.UUID | None = None,
        due: str | None = None,
        due_from: date | None = None,
        due_to: date | None = None,
        q: str | None = None,
        sort: str | None = None,
        with_meta: bool = True,
        hours: bool = False,
        count: bool = True,
    ) -> tuple[list[TaskListItem], int]:
        stmt = self.repo.scoped_select()
        if q:
            stmt = stmt.where(Task.title.ilike(f"%{q.strip()}%"))
        if company_id is not None:
            stmt = stmt.where(Task.company_id == company_id)
        if project_id is not None:
            stmt = stmt.where(Task.project_id == project_id)
        # The dashboard's own bucket, addressable (#15): the tile counts tasks hanging off no
        # client and no project, so the count has a list to open. An absent ``company_id`` means
        # "any client", which is a different question and could never express this one.
        if unlinked:
            stmt = stmt.where(Task.company_id.is_(None), Task.project_id.is_(None))
        if assignee_user_id is not None:
            stmt = stmt.where(Task.assignee_user_id == assignee_user_id)
        if assignee_contact_id is not None:
            stmt = stmt.where(Task.assignee_contact_id == assignee_contact_id)
        if status is not None:
            stmt = stmt.where(Task.status == status)
        if label_id is not None:
            stmt = stmt.where(
                Task.id.in_(
                    select(TaskLabelLink.task_id).where(
                        TaskLabelLink.org_id == self.ctx.org.id,
                        TaskLabelLink.label_id == label_id,
                    )
                )
            )
        # The status vocabulary is per-org (issue #62): "overdue" means an unfinished task past
        # its date, and the status sort ranks by the tenant's configured order — both read from
        # ``task_statuses`` rather than a hardcoded open/done tuple.
        statuses = await load_statuses(self.ctx.session, self.ctx.org.id)
        today = await org_today(self.ctx.session, self.ctx.org.id)
        if due == "overdue":
            stmt = stmt.where(
                Task.due_date < today, Task.status.in_(non_terminal_keys(statuses))
            )
        elif due == "today":
            stmt = stmt.where(Task.due_date == today)
        elif due == "week":
            stmt = stmt.where(Task.due_date >= today, Task.due_date <= today + timedelta(days=7))
        # An explicit deadline window (#188): the Agenda's deadline feed asks for the visible
        # range's due dates. Independent of the ``due`` shortcuts above.
        if due_from is not None:
            stmt = stmt.where(Task.due_date >= due_from)
        if due_to is not None:
            stmt = stmt.where(Task.due_date <= due_to)

        total = 0
        if count:
            count_stmt = select(func.count()).select_from(stmt.subquery())
            total = int(await self.ctx.session.scalar(count_stmt) or 0)

        # Unsorted, the board keeps its hand-dragged order. A requested sort replaces `position`
        # but keeps `created_at` as the tiebreak, so paging stays deterministic either way. The
        # web groups the rows by status afterwards; a sort therefore orders *within* a section
        # and never reshuffles the sections themselves (#38, #41).
        sortable = {**SORTABLE, "status": _rank(Task.status, status_order(statuses))}
        stmt = (
            apply_sort(stmt, sort, sortable, default=Task.position.asc())
            .order_by(Task.created_at.asc())
            .limit(limit)
            .offset(offset)
        )
        tasks = (await self.ctx.session.execute(stmt)).scalars().all()
        if not count:
            total = len(tasks)
        # Lookup lists (pickers) don't need the aggregate chips — skip three queries. They may
        # still want the burn: the time module's task combobox is exactly that lookup (#313).
        items = (
            await self._list_items(tasks)
            if with_meta
            else [TaskListItem.model_validate(t) for t in tasks]
        )
        if hours:
            await self._attach_hours(items)
        return items, total

    async def _my_open_rows(self, limit: int) -> list[Task]:
        statuses = await load_statuses(self.ctx.session, self.ctx.org.id)
        stmt = (
            self.repo.scoped_select()
            .where(Task.assignee_user_id == self.ctx.user.id)
            .where(Task.status.in_(non_terminal_keys(statuses)))
            .order_by(Task.due_date.asc().nulls_last(), Task.created_at.desc())
            .limit(limit)
        )
        return list((await self.ctx.session.execute(stmt)).scalars().all())

    async def my_open(self, *, limit: int = 20) -> list[TaskListItem]:
        """Unfinished tasks assigned to the current user (My Day)."""
        return await self._list_items(await self._my_open_rows(limit))

    async def dashboard_mine(self, *, limit: int = 20) -> list[DashboardTaskItem]:
        """Personal tile rows — the client joined in, no full-card enrichment, one query."""
        statuses = await load_statuses(self.ctx.session, self.ctx.org.id)
        # Same starting point as ``dashboard_groups``: the repository-scoped relation, so the
        # portal rule and a manager's company horizon hold on the tile as well as on the list.
        visible = self.repo.scoped_select().subquery()
        # A task's client is its own company, or — when it only names a project — that project's.
        company_id = func.coalesce(visible.c.company_id, _dashboard_projects.c.company_id)
        company_name = func.coalesce(
            _dashboard_companies.c.name, _dashboard_project_companies.c.name
        )
        stmt = (
            select(
                visible.c.id,
                visible.c.title,
                visible.c.priority,
                visible.c.due_date,
                company_id.label("company_id"),
                company_name.label("company_name"),
            )
            .select_from(visible)
            .outerjoin(
                _dashboard_companies,
                and_(
                    _dashboard_companies.c.org_id == visible.c.org_id,
                    _dashboard_companies.c.id == visible.c.company_id,
                ),
            )
            .outerjoin(
                _dashboard_projects,
                and_(
                    _dashboard_projects.c.org_id == visible.c.org_id,
                    _dashboard_projects.c.id == visible.c.project_id,
                ),
            )
            .outerjoin(
                _dashboard_project_companies,
                and_(
                    _dashboard_project_companies.c.org_id == _dashboard_projects.c.org_id,
                    _dashboard_project_companies.c.id == _dashboard_projects.c.company_id,
                ),
            )
            .where(visible.c.assignee_user_id == self.ctx.user.id)
            .where(visible.c.status.in_(non_terminal_keys(statuses)))
            .order_by(visible.c.due_date.asc().nulls_last(), visible.c.created_at.desc())
            .limit(limit)
        )
        rows = (await self.ctx.session.execute(stmt)).all()
        return [DashboardTaskItem.model_validate(row) for row in rows]

    async def dashboard_groups(self) -> list[DashboardTaskGroup]:
        """Open task counts by project/company without shipping all source rows to the web."""
        statuses = await load_statuses(self.ctx.session, self.ctx.org.id)
        open_keys = non_terminal_keys(statuses)
        today = await org_today(self.ctx.session, self.ctx.org.id)
        # Start from the repository-scoped relation, not the bare tasks table: portal visibility
        # and a manager's company horizon remain API-boundary guarantees even on an aggregate.
        visible = self.repo.scoped_select().subquery()
        entity_type = case(
            (visible.c.project_id.is_not(None), "project"),
            (visible.c.company_id.is_not(None), "company"),
            else_="none",
        )
        entity_id = func.coalesce(visible.c.project_id, visible.c.company_id)
        label = case(
            (visible.c.project_id.is_not(None), _dashboard_projects.c.name),
            (visible.c.company_id.is_not(None), _dashboard_companies.c.name),
        )
        # A project row names its client too, so the tile can say "Website · Bakkerij Jansen"
        # instead of two indistinguishable "Website" rows. A company row is already its client.
        group_company_id = case(
            (visible.c.project_id.is_not(None), _dashboard_projects.c.company_id),
        )
        group_company_name = case(
            (visible.c.project_id.is_not(None), _dashboard_project_companies.c.name),
        )
        count = func.count()
        overdue = func.count().filter(visible.c.due_date < today)
        stmt = (
            select(
                entity_type.label("entity_type"),
                entity_id.label("entity_id"),
                label.label("label"),
                group_company_id.label("company_id"),
                group_company_name.label("company_name"),
                count.label("count"),
                overdue.label("overdue"),
            )
            .select_from(visible)
            .outerjoin(
                _dashboard_projects,
                and_(
                    _dashboard_projects.c.org_id == visible.c.org_id,
                    _dashboard_projects.c.id == visible.c.project_id,
                ),
            )
            .outerjoin(
                _dashboard_project_companies,
                and_(
                    _dashboard_project_companies.c.org_id == _dashboard_projects.c.org_id,
                    _dashboard_project_companies.c.id == _dashboard_projects.c.company_id,
                ),
            )
            .outerjoin(
                _dashboard_companies,
                and_(
                    _dashboard_companies.c.org_id == visible.c.org_id,
                    _dashboard_companies.c.id == visible.c.company_id,
                ),
            )
            .where(visible.c.status.in_(open_keys))
            .group_by(entity_type, entity_id, label, group_company_id, group_company_name)
            .order_by(count.desc(), label.asc().nulls_last())
        )
        rows = (await self.ctx.session.execute(stmt)).all()
        return [
            DashboardTaskGroup(
                entity_type=row.entity_type,
                entity_id=row.entity_id,
                label=row.label,
                company_id=row.company_id,
                company_name=row.company_name,
                count=int(row.count),
                overdue=int(row.overdue),
            )
            for row in rows
        ]

    # ------------------------------------------------------------------ #
    # Detail
    # ------------------------------------------------------------------ #
    async def ai_status(self, task_id: uuid.UUID) -> TaskAIStatusRead:
        """Just the "is schakl still filling this in?" flag (#327), for the card's live pill.

        Loaded through ``self.repo`` like every other read, so the portal narrowing and the
        company horizon apply exactly as they do to the detail — a one-column endpoint is still
        an endpoint, and answering ``404`` here has to mean what it means everywhere else.
        """
        task = await self.repo.get_or_404(task_id)
        return TaskAIStatusRead(ai_status=task.ai_status)

    async def detail(self, task_id: uuid.UUID) -> TaskDetail:
        task = await self.repo.get_or_404(task_id)
        detail = TaskDetail.model_validate(task)

        list_item = (await self._list_items([task]))[0]
        detail.labels = list_item.labels

        checklists = (
            await self.ctx.session.execute(
                self.ctx.repo(TaskChecklist)
                .scoped_select()
                .where(TaskChecklist.task_id == task_id)
                .order_by(TaskChecklist.position.asc(), TaskChecklist.created_at.asc())
            )
        ).scalars().all()
        checklist_reads = [ChecklistRead.model_validate(c) for c in checklists]
        if checklists:
            items = (
                await self.ctx.session.execute(
                    self.ctx.repo(TaskChecklistItem)
                    .scoped_select()
                    .where(TaskChecklistItem.checklist_id.in_([c.id for c in checklists]))
                    .order_by(
                        TaskChecklistItem.position.asc(), TaskChecklistItem.created_at.asc()
                    )
                )
            ).scalars().all()
            for read in checklist_reads:
                read.items = [
                    ChecklistItemRead.model_validate(i)
                    for i in items
                    if i.checklist_id == read.id
                ]

        # Bounded like the activity trail below it: a long-running task's discussion is otherwise
        # unbounded, and opening the card would load every comment ever written on it. Newest
        # ``_COMMENT_CAP`` selected, then reversed — the card reads oldest-first, so taking the
        # *first* 200 would have shown the oldest and hidden the conversation people came for.
        # A second alias for whoever was signed in as the author (#296) — one statement, not a
        # lookup per comment.
        #
        # The cap is taken **by thread, not by row** (#312). Sorting on the *root's* timestamp
        # keeps a reply adjacent to the comment it answers, so the 200th row falls between two
        # conversations instead of inside one — a plain ``created_at`` cut would strand a January
        # reply above a parent that had dropped off the end, and the client would draw it as a new
        # thread. Reversed, this reads exactly as the card renders: threads oldest-first, each
        # opener followed by its answers in the order they were written.
        comment_impersonator = aliased(User)
        comment_root = aliased(TaskComment)
        comment_rows = list(
            reversed(
                (
                    await self.ctx.session.execute(
                        select(TaskComment, User, comment_impersonator)
                        .outerjoin(User, User.id == TaskComment.author_user_id)
                        .outerjoin(
                            comment_impersonator,
                            comment_impersonator.id == TaskComment.impersonator_user_id,
                        )
                        .outerjoin(comment_root, comment_root.id == TaskComment.parent_id)
                        .where(
                            TaskComment.org_id == self.ctx.org.id,
                            TaskComment.task_id == task_id,
                        )
                        .order_by(
                            func.coalesce(comment_root.created_at, TaskComment.created_at).desc(),
                            TaskComment.created_at.desc(),
                        )
                        .limit(_COMMENT_CAP)
                    )
                ).all()
            )
        )
        detail.comments = []
        for comment, author, wrote_as in comment_rows:
            name, deleted = _attribution(author, comment.author_name)
            via, _ = _attribution(wrote_as, comment.impersonator_name)
            detail.comments.append(
                CommentRead.model_validate(comment).model_copy(
                    update={
                        "author_name": name,
                        "author_deleted": deleted,
                        "impersonator_name": via,
                    }
                )
            )

        # The impersonator resolves like the actor — live name while the account exists, snapshot
        # once it doesn't — on a second alias, so it costs no query per row (#296).
        impersonator_user = aliased(User)
        activity_rows = (
            await self.ctx.session.execute(
                select(TaskActivity, User, impersonator_user)
                .outerjoin(User, User.id == TaskActivity.actor_user_id)
                .outerjoin(
                    impersonator_user,
                    impersonator_user.id == TaskActivity.impersonator_user_id,
                )
                .where(
                    TaskActivity.org_id == self.ctx.org.id,
                    TaskActivity.task_id == task_id,
                )
                .order_by(TaskActivity.created_at.desc())
                .limit(50)
            )
        ).all()
        detail.activities = []
        for activity, actor, impersonator in activity_rows:
            name, deleted = _attribution(actor, activity.actor_name)
            via, _ = _attribution(impersonator, activity.impersonator_name)
            detail.activities.append(
                ActivityRead.model_validate(activity).model_copy(
                    update={
                        "actor_name": name,
                        "actor_deleted": deleted,
                        "impersonator_name": via,
                    }
                )
            )
        links = (
            await self.ctx.session.execute(
                self.ctx.repo(TaskLink)
                .scoped_select()
                .where(TaskLink.task_id == task_id)
                .order_by(TaskLink.created_at.asc())
            )
        ).scalars().all()
        detail.links = [LinkRead.model_validate(link) for link in links]

        # Minutes booked on this task. Through the time module's published aggregate (#313)
        # rather than the raw table read this used to be: that one named `time_entries` by
        # hand (§6) and carried no company horizon, so a group-scoped member read a total over
        # entries they cannot open (§15, failure mode 3).
        await self._attach_hours([detail])

        detail.checklists = checklist_reads
        return detail

    # ------------------------------------------------------------------ #
    # Links (URL attachments)
    # ------------------------------------------------------------------ #
    async def add_link(self, task_id: uuid.UUID, data: LinkCreate) -> TaskLink:
        await self._writable_task_or_403(task_id)
        url = data.url if "://" in data.url else f"https://{data.url}"
        # A ``javascript:``/``data:`` URL survives the "://" heuristic and would render as an
        # executable href (stored XSS). Refuse it at the source (security audit web-XSS-2).
        reject_dangerous_url(url, field="url")
        return await self.ctx.repo(TaskLink).create(
            task_id=task_id, url=url, title=data.title
        )

    async def delete_link(self, task_id: uuid.UUID, link_id: uuid.UUID) -> None:
        await self._writable_task_or_403(task_id)
        repo = self.ctx.repo(TaskLink)
        link = await repo.get_or_404(link_id)
        if link.task_id != task_id:
            raise AppError("not_found", "errors.not_found", status_code=404)
        await repo.delete(link)
        await self._record(task_id, "link_deleted", {"title": link.title or link.url})

    # ------------------------------------------------------------------ #
    # CRUD
    # ------------------------------------------------------------------ #
    async def get(self, task_id: uuid.UUID) -> Task:
        return await self.repo.get_or_404(task_id)

    async def create(self, data: TaskCreate) -> Task:
        self.ctx.require("tasks.task.create")
        values = data.model_dump()
        # A task's company/project FKs must live in this tenant (audit F19).
        for _fk, _tbl in (("company_id", "companies"), ("project_id", "projects")):
            await ensure_parent_in_tenant(self.ctx.session, _tbl, values.get(_fk), self.ctx.org.id)
        # Markdown source is stored; strip any raw HTML on write (issue #66, app/core/richtext).
        values["description"] = sanitize_markdown(values.get("description"))
        # An employee or a client contact, never both, and a contact only from this task's own
        # company (#273). Validate the pair as given before any default fills the employee slot.
        await self._validate_assignee(
            user_id=values.get("assignee_user_id"),
            contact_id=values.get("assignee_contact_id"),
            company_id=values.get("company_id"),
        )
        # Verantwoordelijke defaults down: project's responsible → else the company's, when the
        # task names neither an employee nor a contact (a contact assignee is a deliberate choice
        # that must not be silently overwritten by the client's responsible employee).
        if values.get("assignee_user_id") is None and values.get("assignee_contact_id") is None:
            values["assignee_user_id"] = await self._default_assignee(
                values.get("project_id"), values.get("company_id")
            )
        # Status is a tenant-configured key (issue #62): unset falls to the org's default status,
        # anything else must be one the org actually defined.
        statuses = await load_statuses(self.ctx.session, self.ctx.org.id)
        values["status"] = self._resolve_status(statuses, data.status, allow_default=True)
        values["priority"] = data.priority.value
        values["recurrence"] = data.recurrence.model_dump(mode="json") if data.recurrence else None
        await self._validate_recurrence_plan(values["recurrence"])
        values["recurrence_next_run"] = rec_mod.compute_next_run(
            values["recurrence"],
            data.due_date,
            today=await org_today(self.ctx.session, self.ctx.org.id),
        )
        values["position"] = await self._next_position()
        task = await self.repo.create(**values)
        await self._record(task.id, "created")
        # Automation trigger (issue #27); deliberately not in the notifications vocabulary,
        # so it fans out to nobody. Status/company/project ride along for condition matching.
        await self._emit_task(
            "task.created",
            task,
            [],
            {
                "status": task.status,
                "company_id": task.company_id,
                "project_id": task.project_id,
            },
        )
        if task.assignee_user_id is not None:
            # Assigning yourself is silent — the fan-out drops the actor (issue #16).
            await self._emit_task("task.assigned", task, [task.assignee_user_id])
        return task

    async def _default_assignee(
        self, project_id: uuid.UUID | None, company_id: uuid.UUID | None
    ) -> uuid.UUID | None:
        """Inherit the verantwoordelijke — the parent project's primary assignee, else the
        company's — via their published services (§3 — no model cross-imports). Neither having
        one, or neither existing, means the task starts unassigned."""
        if project_id is not None:
            from app.modules.projects.service import ProjectService

            primary = await ProjectService(self.ctx).primary_assignee(project_id)
            if primary is not None:
                return primary
        if company_id is not None:
            from app.modules.companies.service import CompanyService

            return await CompanyService(self.ctx).primary_assignee(company_id)
        return None

    async def _validate_assignee(
        self,
        *,
        user_id: uuid.UUID | None,
        contact_id: uuid.UUID | None,
        company_id: uuid.UUID | None,
    ) -> None:
        """Guard the two mutually exclusive assignee kinds (#273).

        A task is assigned to an employee **or** to a contact of its own client company, never
        both. And a contact assignee is company-scoped one level deeper than the usual org
        isolation: it must be linked to *this task's* ``company_id`` through ``company_contacts``,
        so staff can never attach an unrelated client's contact. Both checks reject through the
        standard i18n envelope.

        The company-link probe is raw org-scoped SQL against the contacts join — never a
        cross-module import (§6) — and runs *before* the FK insert, so a foreign ``contact_id``
        (another org, or another company in this org) is refused here and never reaches the
        FK check that would otherwise turn into a cross-tenant existence oracle (audit F19).
        """
        if user_id is not None and contact_id is not None:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"assignee_contact_id": "errors.tasks_assignee_conflict"},
            )
        if contact_id is None:
            return
        # A contact assignee needs a client to draw from; an internal task (no company) has none.
        if company_id is not None:
            linked = await self.ctx.session.scalar(
                sql_text(
                    "SELECT 1 FROM company_contacts WHERE org_id = :oid"
                    " AND company_id = :cid AND contact_id = :ctid"
                ),
                {"oid": self.ctx.org.id, "cid": company_id, "ctid": contact_id},
            )
            if linked:
                return
        raise AppError(
            "validation",
            "errors.validation",
            status_code=422,
            fields={"assignee_contact_id": "errors.tasks_assignee_contact_company"},
        )

    async def preview_recurrence(self, data: RecurrencePreview) -> RecurrencePreviewRead:
        """Resolve a *composed* (not yet stored) rule to the dates it will produce (#335).

        After-completion mode gets ``on_completion=True`` rather than a promise: the next
        occurrence appears when this one is finished, and the date shown is the one it *would*
        be given today — no fake certainty, which is the same honesty ``/leave/preview`` shows
        about a manager's override.
        """
        rec = data.recurrence.model_dump(mode="json")
        today = await org_today(self.ctx.session, self.ctx.org.id)
        dates = rec_mod.upcoming(data.due_date, rec, today=today, count=3)
        planned_start = planned_end = None
        if data.recurrence.plan is not None:
            planned_start = data.recurrence.plan.start_time
            planned_end = (
                datetime.combine(dates[0], planned_start)
                + timedelta(minutes=data.recurrence.plan.duration_minutes)
            ).time()
        return RecurrencePreviewRead(
            next_date=dates[0],
            following=dates[1:],
            on_completion=data.recurrence.mode is RecurrenceMode.AFTER_COMPLETION,
            planned_start=planned_start,
            planned_end=planned_end,
        )

    async def _validate_recurrence_plan(self, rec: dict | None) -> None:
        """Gate "herhaal ook de planning" on the keys the *scheduling* module declares (#335).

        The route says ``tasks.task.write`` and the licence gate says ``tasks``; neither is an
        answer to "may this person put a block on a colleague's calendar". So the stored decision
        asks exactly what pressing Inplannen would ask — ``tasks.schedule.write``, at ``:any`` to
        name someone else — because the generator will later *execute* it as the system, and a
        permission checked only at execution time is no permission at all (§18, one layer down).

        The person must also be a member of this org: unlike a hand-planned block, this id is
        stored for months and spent by a cron, so a stale or foreign one would surface as a
        silently skipped occurrence rather than a 403 somebody could read.
        """
        plan = (rec or {}).get("plan")
        if not plan:
            return
        target = plan.get("user_id")
        target_id = uuid.UUID(str(target)) if target else None
        if target_id is not None and target_id != self.ctx.user.id:
            if not self.ctx.can("tasks.schedule.write", scope="any"):
                raise AppError("forbidden", "errors.forbidden", status_code=403)
            known = await self.ctx.session.scalar(
                sql_text(
                    "SELECT 1 FROM memberships WHERE org_id = :oid AND user_id = :uid"
                ),
                {"oid": self.ctx.org.id, "uid": target_id},
            )
            if not known:
                raise AppError(
                    "validation",
                    "errors.validation",
                    status_code=422,
                    fields={"recurrence": "errors.invalid_assignee"},
                )
        elif not self.ctx.can("tasks.schedule.write"):
            raise AppError("forbidden", "errors.forbidden", status_code=403)

    async def _next_position(self) -> float:
        result = await self.ctx.session.scalar(
            select(func.max(Task.position)).where(Task.org_id == self.ctx.org.id)
        )
        return float(result or 0.0) + 1024.0

    async def _closing_interaction_or_422(
        self, task_id: uuid.UUID, interaction_id: uuid.UUID
    ) -> None:
        """The closing contact moment must be linked to *this* task and team-visible (#157).
        Raw org-scoped SQL against the interactions table — never a cross-module import (§6);
        a pending gmail row cannot close anything (its content isn't approved yet)."""
        linked = await self.ctx.session.scalar(
            sql_text(
                "SELECT 1 FROM interactions WHERE id = :iid AND org_id = :oid"
                " AND task_id = :tid AND status = 'logged'"
            ),
            {"iid": interaction_id, "oid": self.ctx.org.id, "tid": task_id},
        )
        if not linked:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"closing_interaction_id": "errors.tasks_closing_interaction_not_linked"},
            )

    def _resolve_status(
        self, statuses: list[TaskStatusDef], key: str | None, *, allow_default: bool
    ) -> str:
        """Validate a requested status key against the org's vocabulary (issue #62).

        ``None`` resolves to the org's default status on create; on update it means "leave it",
        so callers only pass a key there. An unknown key is a 422 like any other bad field.
        """
        if key is None and allow_default:
            return default_key(statuses)
        if key not in {s.key for s in statuses}:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"status": "errors.validation"},
            )
        return key

    async def update(self, task_id: uuid.UUID, data: TaskUpdate) -> Task:
        task = await self._writable_task_or_403(task_id)
        values = data.model_dump(exclude_unset=True)
        # A ride-along, not a column: pop it before anything reaches ``repo.update`` (#314).
        values.pop("log_time", None)
        for _fk, _tbl in (("company_id", "companies"), ("project_id", "projects")):
            if _fk in values:
                await ensure_parent_in_tenant(
                    self.ctx.session, _tbl, values.get(_fk), self.ctx.org.id
                )
        # Re-check the assignee whenever either kind or the company moves (#273): the resulting
        # pair must stay exclusive, and a contact assignee must still belong to the resulting
        # company — so re-homing a task to another client, or clearing its company, is refused
        # while it holds that client's contact rather than silently orphaning the assignment.
        if {"assignee_user_id", "assignee_contact_id", "company_id"} & values.keys():
            await self._validate_assignee(
                user_id=values.get("assignee_user_id", task.assignee_user_id),
                contact_id=values.get("assignee_contact_id", task.assignee_contact_id),
                company_id=values.get("company_id", task.company_id),
            )
        reason = values.pop("due_change_reason", None)
        if "description" in values:
            values["description"] = sanitize_markdown(values["description"])

        # Accountability: pushing an existing deadline back requires a reason, which lands
        # in the activity feed.
        due_extended = (
            "due_date" in values
            and task.due_date is not None
            and values["due_date"] is not None
            and values["due_date"] > task.due_date
        )
        if due_extended and not (reason or "").strip():
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"due_change_reason": "errors.due_reason_required"},
            )

        # A tenant-configured status vocabulary (issue #62): the requested key must be one the org
        # defined, and "finished" is the status's ``is_terminal`` flag, not the literal "done".
        statuses = await load_statuses(self.ctx.session, self.ctx.org.id)
        if values.get("status") is not None:
            values["status"] = self._resolve_status(statuses, data.status, allow_default=False)
        if values.get("priority") is not None:
            values["priority"] = data.priority.value  # type: ignore[union-attr]
        if "recurrence" in values:
            values["recurrence"] = (
                data.recurrence.model_dump(mode="json") if data.recurrence else None
            )
            await self._validate_recurrence_plan(values["recurrence"])

        terminal = terminal_keys(statuses)
        old_status = task.status
        new_status = values.get("status", old_status)

        # A designated closing contact moment (#157) — GitHub's "close with comment", but a
        # contactmoment. It must be linked to *this* task and team-visible. The requirement fires
        # from two independent sources: a status flagged ``requires_interaction`` (tenant policy on
        # the whole status), or this task's own ``requires_interaction`` flag when it enters any
        # finished status (per-task / per-template policy, #157 extended). Either one gates.
        if values.get("closing_interaction_id") is not None:
            await self._closing_interaction_or_422(task.id, values["closing_interaction_id"])
        requires_keys = {s.key for s in statuses if s.requires_interaction}
        task_requires_interaction = values.get(
            "requires_interaction", task.requires_interaction
        )
        needs_closing_moment = new_status in requires_keys or (
            task_requires_interaction and new_status in terminal
        )
        if (
            new_status != old_status
            and needs_closing_moment
            and not (values.get("closing_interaction_id") or task.closing_interaction_id)
        ):
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"status": "errors.tasks_closing_interaction_required"},
            )

        finishing = old_status not in terminal and new_status in terminal
        # "Ook de uren registreren" (#314) is a *completion* ride-along. Refused on anything
        # else — a task already finished, a retitle, a reopen — so ``PATCH /tasks/{id}`` never
        # becomes a second way to write a time entry, with none of the entry endpoint's own
        # rules. Checked before the write so the refusal is about the request, not a rollback.
        if data.log_time is not None and not finishing:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"log_time": "errors.tasks_log_time_not_finishing"},
            )

        if finishing:
            values["completed_at"] = datetime.now(UTC)
        elif old_status in terminal and new_status not in terminal:
            values["completed_at"] = None
            # Reopening clears the designation: the next close picks its own moment, so the
            # gate can never be satisfied by last year's phone call.
            values.setdefault("closing_interaction_id", None)

        if "recurrence" in values or "due_date" in values:
            values["recurrence_next_run"] = rec_mod.compute_next_run(
                values.get("recurrence", task.recurrence),
                values.get("due_date", task.due_date),
                today=await org_today(self.ctx.session, self.ctx.org.id),
            )

        changed = [
            f for f in _TRACKED_FIELDS if f in values and getattr(task, f) != values[f]
        ]
        status_changed = "status" in values and old_status != new_status
        old_due = task.due_date
        # Read the old assignee before the write; the diff drives who is told (issue #16).
        old_assignee = task.assignee_user_id
        assignee_changed = (
            "assignee_user_id" in values and old_assignee != values["assignee_user_id"]
        )

        task = await self.repo.update(task, **values)

        if status_changed:
            status_payload: dict[str, Any] = {"from": old_status, "to": new_status}
            if task.closing_interaction_id is not None and "closing_interaction_id" in values:
                # The trail says *what justified* the close, not only that it happened.
                status_payload["closing_interaction_id"] = str(task.closing_interaction_id)
                status_payload["closing_subject"] = await self.ctx.session.scalar(
                    sql_text(
                        "SELECT subject FROM interactions WHERE id = :iid AND org_id = :oid"
                    ),
                    {"iid": task.closing_interaction_id, "oid": self.ctx.org.id},
                )
            await self._record(task.id, "status_changed", status_payload)
            await self._emit_task(
                "task.status_changed",
                task,
                [task.assignee_user_id],
                {"from": old_status, "to": new_status},
            )
        if assignee_changed:
            if task.assignee_user_id is not None:
                await self._emit_task("task.assigned", task, [task.assignee_user_id])
            if old_assignee is not None:
                await self._emit_task("task.unassigned", task, [old_assignee])
        if due_extended:
            await self._record(
                task.id,
                "due_extended",
                {
                    "from": old_due.isoformat() if old_due else None,
                    "to": task.due_date.isoformat() if task.due_date else None,
                    "reason": (reason or "").strip(),
                },
            )
        if changed:
            await self._record(task.id, "updated", {"changed": changed})

        # The hours the task took, in this same transaction (#314): a finished task with no
        # hours because a second request failed is the exact thing this feature exists to stop.
        if data.log_time is not None:
            await self._log_time(task, data.log_time)

        if (
            status_changed
            and new_status in terminal
            and (task.recurrence or {}).get("mode") == RecurrenceMode.AFTER_COMPLETION.value
        ):
            await rec_mod.spawn_next(
                self.ctx.session,
                self.ctx.org.id,
                task,
                actor_user_id=self.ctx.user.id,
                actor_name=_display_name(self.ctx.user),
                # So a rule carrying a plan books the occurrence's block through the schedule
                # service's one emit site (#335 phase 5) rather than not at all.
                ctx=self.ctx,
            )
            # spawn_next mutates the source (recurrence handed off); reload server-side
            # defaults so serialization never lazy-loads.
            await self.ctx.session.refresh(task)
        return task

    async def _log_time(self, task: Task, log_time: TaskLogTime) -> None:
        """Record the hours a just-finished task took (#314), through the time module's
        published surface (§6) — never its internals, exactly as #175's contact-moment
        ride-along does.

        Three gates, none of them implied by having been allowed to finish the task:

        * ``time.entry.write`` — writing a task is not writing a timesheet. Unscoped, because
          the entry is always the caller's own (§15: ``:any`` satisfies ``:own``).
        * the ``time`` sku must still be writable. The task PATCH carries ``tasks``' licence
          gate, not ``time``'s, and a ride-along must never be the one way an uncovered module
          can still be written to (§18). A 402 refuses the whole request, finish included: the
          user asked for both in one act, and half of it is not what they asked for.
        * a named schedule block is claimed, so #188's panel stops offering the same hours.
        """
        self.ctx.require("time.entry.write")
        if not await sku_writable("time", plan=OrgPlan.of(self.ctx.org)):
            raise AppError(*refusal_for("time"), status_code=402)
        from app.modules.time import system as time_system

        entry = await time_system.record_entry(
            self.ctx,
            user_id=self.ctx.user.id,
            started_at=log_time.started_at,
            ended_at=log_time.ended_at,
            company_id=task.company_id,
            project_id=task.project_id,
            task_id=task.id,
            description=(log_time.description or "").strip() or task.title,
            entry_type_key=log_time.entry_type_key,
            billable=log_time.billable,
        )
        if log_time.schedule_id is not None:
            await TaskScheduleService(self.ctx).mark_logged(
                log_time.schedule_id, task_id=task.id, entry_id=entry.id
            )

    async def delete(self, task_id: uuid.UUID) -> None:
        self.ctx.require("tasks.task.delete")
        task = await self.repo.get_or_404(task_id)
        # The card's planned blocks go with it (``task_schedules.task_id`` is ON DELETE CASCADE),
        # and a cascade tells nobody: without this the Google mirror keeps a *pushed* link to a
        # row that no longer exists and the block sits in someone's calendar forever.
        await TaskScheduleService(self.ctx).remove_for_task(task.id)
        await self.repo.delete(task)

    # ------------------------------------------------------------------ #
    # Labels
    # ------------------------------------------------------------------ #
    async def list_labels(self) -> Sequence[TaskLabel]:
        return await self.ctx.repo(TaskLabel).list(
            limit=200, order_by=TaskLabel.position.asc()
        )

    async def create_label(self, data: LabelCreate) -> TaskLabel:
        self.ctx.require("tasks.label.write")
        repo = self.ctx.repo(TaskLabel)
        if await repo.count(name=data.name):
            raise AppError("conflict", "errors.conflict", status_code=409)
        return await repo.create(**data.model_dump())

    async def update_label(self, label_id: uuid.UUID, data: LabelUpdate) -> TaskLabel:
        self.ctx.require("tasks.label.write")
        repo = self.ctx.repo(TaskLabel)
        label = await repo.get_or_404(label_id)
        return await repo.update(label, **data.model_dump(exclude_unset=True))

    async def delete_label(self, label_id: uuid.UUID) -> None:
        self.ctx.require("tasks.label.write")
        repo = self.ctx.repo(TaskLabel)
        label = await repo.get_or_404(label_id)
        await repo.delete(label)

    async def set_task_labels(
        self, task_id: uuid.UUID, label_ids: list[uuid.UUID]
    ) -> list[TaskLabel]:
        await self._writable_task_or_403(task_id)
        label_repo = self.ctx.repo(TaskLabel)
        labels = [await label_repo.get_or_404(label_id) for label_id in set(label_ids)]

        existing = (
            await self.ctx.session.execute(
                self.ctx.repo(TaskLabelLink)
                .scoped_select()
                .where(TaskLabelLink.task_id == task_id)
            )
        ).scalars().all()
        wanted = {label.id for label in labels}
        for link in existing:
            if link.label_id not in wanted:
                await self.ctx.session.delete(link)
        current = {link.label_id for link in existing}
        for label in labels:
            if label.id not in current:
                self.ctx.session.add(
                    TaskLabelLink(
                        org_id=self.ctx.org.id, task_id=task_id, label_id=label.id
                    )
                )
        await self.ctx.session.flush()
        await self._record(task_id, "updated", {"changed": ["labels"]})
        return sorted(labels, key=lambda label: (label.position, label.name))

    # ------------------------------------------------------------------ #
    # Statuses (org-level, tenant-configurable — issue #62)
    # ------------------------------------------------------------------ #
    async def list_statuses(self) -> list[TaskStatusDef]:
        """The org's status vocabulary in board order, seeding the defaults on first read."""
        return await load_statuses(self.ctx.session, self.ctx.org.id)

    async def _clear_default(self, keep_id: uuid.UUID | None) -> None:
        """At most one status is the default; making one default clears the others."""
        others = await self.ctx.repo(TaskStatusDef).list(limit=200)
        for status in others:
            if status.is_default and status.id != keep_id:
                status.is_default = False

    async def create_status(self, data: StatusCreate) -> TaskStatusDef:
        self.ctx.require("tasks.status.write")
        repo = self.ctx.repo(TaskStatusDef)
        await load_statuses(self.ctx.session, self.ctx.org.id)  # ensure defaults exist first
        if await repo.count(key=data.key):
            raise AppError("conflict", "errors.conflict", status_code=409)
        status = await repo.create(**data.model_dump())
        if status.is_default:
            await self._clear_default(status.id)
        await self.ctx.session.flush()
        return status

    async def update_status(self, status_id: uuid.UUID, data: StatusUpdate) -> TaskStatusDef:
        self.ctx.require("tasks.status.write")
        repo = self.ctx.repo(TaskStatusDef)
        status = await repo.get_or_404(status_id)
        status = await repo.update(status, **data.model_dump(exclude_unset=True))
        if status.is_default:
            await self._clear_default(status.id)
        await self.ctx.session.flush()
        return status

    async def delete_status(self, status_id: uuid.UUID) -> None:
        self.ctx.require("tasks.status.write")
        repo = self.ctx.repo(TaskStatusDef)
        status = await repo.get_or_404(status_id)
        # A status still holding tasks can't be dropped — it would orphan ``Task.status``. Move
        # those tasks first (or delete them). The last status can't go either: a task needs one.
        in_use = await self.ctx.session.scalar(
            select(func.count())
            .select_from(Task)
            .where(Task.org_id == self.ctx.org.id, Task.status == status.key)
        )
        if in_use:
            raise AppError("conflict", "errors.status_in_use", status_code=409)
        if await repo.count() <= 1:
            raise AppError("conflict", "errors.status_last", status_code=409)
        await repo.delete(status)

    # ------------------------------------------------------------------ #
    # Checklists
    # ------------------------------------------------------------------ #
    async def add_checklist(self, task_id: uuid.UUID, data: ChecklistCreate) -> TaskChecklist:
        """A fresh checklist, or a copy of an org checklist template (title + items)."""
        await self._writable_task_or_403(task_id)

        template = None
        if data.template_id is not None:
            template = await self.ctx.repo(TaskChecklistTemplate).get_or_404(data.template_id)
        title = data.title or (template.title if template else None)
        if not title:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"title": "errors.required"},
            )

        repo = self.ctx.repo(TaskChecklist)
        position = await repo.count(task_id=task_id)
        checklist = await repo.create(
            task_id=task_id,
            title=title,
            description=sanitize_markdown(data.description),
            position=position,
        )
        if template is not None:
            # Copy each item's title *and* description from the template's rich shape (issue #66).
            for index, entry in enumerate(_rich_items(template.items_rich, template.items)):
                self.ctx.session.add(
                    TaskChecklistItem(
                        org_id=self.ctx.org.id,
                        checklist_id=checklist.id,
                        title=str(entry.get("title") or "")[:512],
                        description=sanitize_markdown(entry.get("description")),
                        position=index,
                    )
                )
            await self.ctx.session.flush()
        await self._record(task_id, "checklist_created", {"title": checklist.title})
        return checklist

    async def duplicate_checklist(
        self, task_id: uuid.UUID, checklist_id: uuid.UUID, data: ChecklistDuplicate
    ) -> ChecklistRead:
        """Copy a checklist — title, description and every item — beside its source.

        Returns the read shape rather than the row: the items are already in hand, and a
        ``ChecklistRead.model_validate(row)`` would answer a duplicate with an empty list.

        Ticks do **not** travel. A duplicate is the same work to be done again — carrying
        ``done`` across would hand someone a record of work that never happened, and unticking
        a copied list by hand is the friction this feature exists to remove.
        """
        await self._writable_task_or_403(task_id)
        source = await self._checklist_or_404(task_id, checklist_id)

        repo = self.ctx.repo(TaskChecklist)
        siblings = (
            await self.ctx.session.execute(
                repo.scoped_select()
                .where(TaskChecklist.task_id == task_id)
                .order_by(TaskChecklist.position.asc(), TaskChecklist.created_at.asc())
            )
        ).scalars().all()
        source_items = (
            await self.ctx.session.execute(
                self.ctx.repo(TaskChecklistItem)
                .scoped_select()
                .where(TaskChecklistItem.checklist_id == checklist_id)
                .order_by(
                    TaskChecklistItem.position.asc(), TaskChecklistItem.created_at.asc()
                )
            )
        ).scalars().all()

        copy = await repo.create(
            task_id=task_id,
            title=data.title or source.title,
            description=source.description,
            position=source.position,
        )
        # Renumber the whole task through the same helper a drag does, rather than shifting from
        # the source down: positions go stale on every delete, so a copy that merely inherited
        # ``source.position`` would tie with it and fall back to ``created_at`` — landing under
        # whatever else shares that number. Stating the order the card already reads, with the
        # copy spliced in after its source, puts it there by construction.
        ordered = list(siblings)
        ordered.insert(ordered.index(source) + 1, copy)
        _renumber([*siblings, copy], [checklist.id for checklist in ordered])

        items = [
            TaskChecklistItem(
                org_id=self.ctx.org.id,
                checklist_id=copy.id,
                title=item.title,
                description=item.description,
                done=False,
                position=index,
            )
            for index, item in enumerate(source_items)
        ]
        self.ctx.session.add_all(items)
        await self.ctx.session.flush()

        # ``from``/``to``, the shape ``checklist_renamed`` already uses: the trail is only
        # worth reading if it says which list the copy came from.
        await self._record(
            task_id, "checklist_duplicated", {"from": source.title, "to": copy.title}
        )
        read = ChecklistRead.model_validate(copy)
        read.items = [ChecklistItemRead.model_validate(i) for i in items]
        return read

    # ------------------------------------------------------------------ #
    # Checklist templates (org-wide repository)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _checklist_template_read(template: TaskChecklistTemplate) -> ChecklistTemplateRead:
        """Read shape — items come from the authoritative ``items_rich`` column (issue #66)."""
        return ChecklistTemplateRead(
            id=template.id,
            title=template.title,
            items=[
                TemplateChecklistItem(
                    title=str(entry.get("title") or ""), description=entry.get("description")
                )
                for entry in _rich_items(template.items_rich, template.items)
            ],
        )

    @staticmethod
    def _dual_write_items(items: list[TemplateChecklistItem]) -> dict[str, Any]:
        """Expand/contract dual-write (docs/WORKFLOW.md): the sanitized ``{title, description}``
        objects in ``items_rich`` (authoritative) plus the legacy title-only ``items`` a
        rolled-back previous image still reads."""
        return {
            "items_rich": [
                {"title": i.title, "description": sanitize_markdown(i.description)} for i in items
            ],
            "items": [i.title for i in items],
        }

    async def list_checklist_templates(self) -> list[ChecklistTemplateRead]:
        rows = await self.ctx.repo(TaskChecklistTemplate).list(
            limit=200, order_by=TaskChecklistTemplate.title.asc()
        )
        return [self._checklist_template_read(t) for t in rows]

    async def create_checklist_template(
        self, data: ChecklistTemplateCreate
    ) -> ChecklistTemplateRead:
        self.ctx.require("tasks.checklist_template.write")
        template = await self.ctx.repo(TaskChecklistTemplate).create(
            title=data.title, **self._dual_write_items(data.items)
        )
        return self._checklist_template_read(template)

    async def update_checklist_template(
        self, template_id: uuid.UUID, data: ChecklistTemplateUpdate
    ) -> ChecklistTemplateRead:
        self.ctx.require("tasks.checklist_template.write")
        repo = self.ctx.repo(TaskChecklistTemplate)
        template = await repo.get_or_404(template_id)
        values: dict[str, Any] = {}
        if data.title is not None:
            values["title"] = data.title
        if data.items is not None:
            values.update(self._dual_write_items(data.items))
        template = await repo.update(template, **values)
        return self._checklist_template_read(template)

    async def delete_checklist_template(self, template_id: uuid.UUID) -> None:
        self.ctx.require("tasks.checklist_template.write")
        repo = self.ctx.repo(TaskChecklistTemplate)
        template = await repo.get_or_404(template_id)
        await repo.delete(template)

    async def _checklist_or_404(
        self, task_id: uuid.UUID, checklist_id: uuid.UUID
    ) -> TaskChecklist:
        checklist = await self.ctx.repo(TaskChecklist).get_or_404(checklist_id)
        if checklist.task_id != task_id:
            raise AppError("not_found", "errors.not_found", status_code=404)
        return checklist

    async def update_checklist(
        self, task_id: uuid.UUID, checklist_id: uuid.UUID, data: ChecklistUpdate
    ) -> TaskChecklist:
        await self._writable_task_or_403(task_id)
        checklist = await self._checklist_or_404(task_id, checklist_id)
        values = data.model_dump(exclude_unset=True)
        if "description" in values:
            values["description"] = sanitize_markdown(values["description"])
        old_title = checklist.title
        checklist = await self.ctx.repo(TaskChecklist).update(checklist, **values)
        # A reorder is noise, the way `position` is excluded from `_TRACKED_FIELDS`; a rename
        # is a change to what the list *is*, so it belongs in the trail.
        if checklist.title != old_title:
            await self._record(
                task_id, "checklist_renamed", {"from": old_title, "to": checklist.title}
            )
        return checklist

    async def delete_checklist(self, task_id: uuid.UUID, checklist_id: uuid.UUID) -> None:
        await self._writable_task_or_403(task_id)
        checklist = await self._checklist_or_404(task_id, checklist_id)
        await self.ctx.repo(TaskChecklist).delete(checklist)
        await self._record(task_id, "checklist_deleted", {"title": checklist.title})

    async def add_checklist_item(
        self, task_id: uuid.UUID, checklist_id: uuid.UUID, data: ChecklistItemCreate
    ) -> TaskChecklistItem:
        await self._writable_task_or_403(task_id)
        await self._checklist_or_404(task_id, checklist_id)
        repo = self.ctx.repo(TaskChecklistItem)
        position = await repo.count(checklist_id=checklist_id)
        item = await repo.create(
            checklist_id=checklist_id,
            title=data.title,
            description=sanitize_markdown(data.description),
            position=position,
        )
        await self._record(task_id, "checklist_item_added", {"title": item.title})
        return item

    async def _item_or_404(
        self, task_id: uuid.UUID, checklist_id: uuid.UUID, item_id: uuid.UUID
    ) -> TaskChecklistItem:
        await self._checklist_or_404(task_id, checklist_id)
        item = await self.ctx.repo(TaskChecklistItem).get_or_404(item_id)
        if item.checklist_id != checklist_id:
            raise AppError("not_found", "errors.not_found", status_code=404)
        return item

    async def update_checklist_item(
        self,
        task_id: uuid.UUID,
        checklist_id: uuid.UUID,
        item_id: uuid.UUID,
        data: ChecklistItemUpdate,
    ) -> TaskChecklistItem:
        await self._writable_task_or_403(task_id)
        item = await self._item_or_404(task_id, checklist_id, item_id)
        values = data.model_dump(exclude_unset=True)
        if "description" in values:
            values["description"] = sanitize_markdown(values["description"])
        was_done, old_title = item.done, item.title
        item = await self.ctx.repo(TaskChecklistItem).update(item, **values)

        # Ticking an item off is the most routine thing that happens on a task, and it was the
        # one thing the trail never saw (#61) — it arrives here as an ordinary field update.
        if item.done != was_done:
            await self._record(
                task_id,
                "checklist_item_completed" if item.done else "checklist_item_reopened",
                {"title": item.title},
            )
        if item.title != old_title:
            await self._record(
                task_id, "checklist_item_renamed", {"from": old_title, "to": item.title}
            )
        return item

    async def delete_checklist_item(
        self, task_id: uuid.UUID, checklist_id: uuid.UUID, item_id: uuid.UUID
    ) -> None:
        await self._writable_task_or_403(task_id)
        item = await self._item_or_404(task_id, checklist_id, item_id)
        await self.ctx.repo(TaskChecklistItem).delete(item)
        await self._record(task_id, "checklist_item_deleted", {"title": item.title})

    async def reorder_checklists(
        self, task_id: uuid.UUID, data: ChecklistOrder
    ) -> ChecklistOrderRead:
        """Renumber this task's checklists. One call, so a dragged list cannot half-save.

        No activity entry: a reorder is noise, the same reason ``position`` is excluded from
        ``_TRACKED_FIELDS`` and ``update_checklist`` records only a rename.
        """
        await self._writable_task_or_403(task_id)
        rows = (
            await self.ctx.session.execute(
                self.ctx.repo(TaskChecklist)
                .scoped_select()
                .where(TaskChecklist.task_id == task_id)
                # The order the card reads them in, so "what the payload did not name" is
                # appended in the order the user was actually looking at.
                .order_by(TaskChecklist.position.asc(), TaskChecklist.created_at.asc())
                .limit(_ORDER_CAP)
            )
        ).scalars().all()
        ordered = _renumber(rows, data.checklist_ids)
        await self.ctx.session.flush()
        return ChecklistOrderRead(ids=[row.id for row in ordered])

    async def reorder_checklist_items(
        self, task_id: uuid.UUID, checklist_id: uuid.UUID, data: ChecklistItemOrder
    ) -> ChecklistOrderRead:
        """Renumber one checklist's items — same contract as ``reorder_checklists``."""
        await self._writable_task_or_403(task_id)
        await self._checklist_or_404(task_id, checklist_id)
        rows = (
            await self.ctx.session.execute(
                self.ctx.repo(TaskChecklistItem)
                .scoped_select()
                .where(TaskChecklistItem.checklist_id == checklist_id)
                .order_by(TaskChecklistItem.position.asc(), TaskChecklistItem.created_at.asc())
                .limit(_ORDER_CAP)
            )
        ).scalars().all()
        ordered = _renumber(rows, data.item_ids)
        await self.ctx.session.flush()
        return ChecklistOrderRead(ids=[row.id for row in ordered])

    # ------------------------------------------------------------------ #
    # Comments
    # ------------------------------------------------------------------ #
    async def _valid_mentions(self, ids: list[uuid.UUID]) -> list[uuid.UUID]:
        """Keep only the mentioned ids that are members of this org (issue #63)."""
        if not ids:
            return []
        members = set(
            (
                await self.ctx.session.execute(
                    select(Membership.user_id).where(
                        Membership.org_id == self.ctx.org.id, Membership.user_id.in_(ids)
                    )
                )
            ).scalars()
        )
        return [uid for uid in ids if uid in members]

    async def _valid_contact_mentions(self, ids: list[uuid.UUID]) -> list[uuid.UUID]:
        """Keep only the mentioned contact ids **this caller can see** (#165) — a reference
        into the CRM, never a notification: contacts have no inbox here.

        Through the cross-module reference seam (``core/directory.py``), not a bare
        ``WHERE org_id`` read: a contact's client hangs off ``company_contacts``, so "in this
        org" let a company-group-scoped member mention anyone in the tenant and have the
        comment render that person's name back at them.
        """
        if not ids:
            return []
        found = await visible_ids(self.ctx, "contact", ids)
        return [cid for cid in ids if cid in found]

    async def _valid_task_mentions(self, ids: list[uuid.UUID]) -> list[uuid.UUID]:
        """Keep only the referenced task ids that belong to this org (#197) — a deep link into
        the board, never a notification. A cross-tenant uuid silently drops out here, so the
        stored reference list can never point outside the org."""
        if not ids:
            return []
        found = set(
            (
                await self.ctx.session.execute(
                    select(Task.id).where(Task.org_id == self.ctx.org.id, Task.id.in_(ids))
                )
            ).scalars()
        )
        return [tid for tid in ids if tid in found]

    async def _resolve_parent(
        self, task_id: uuid.UUID, parent_id: uuid.UUID | None
    ) -> uuid.UUID | None:
        """Which comment a new one answers, as the thread's *root* (#312).

        Threads are one level deep, so replying to a reply attaches to the same root rather than
        raising: the person clicked "reply" under words that are on their screen, and refusing
        that is a rule the UI would have to explain to be usable. Re-rooting keeps every thread
        readable in one indent and costs the writer nothing.

        A parent on another task — or in another tenant, which the repository already refuses — is
        a 404, not a re-root: it is not a reading order problem, it is a wrong id.
        """
        if parent_id is None:
            return None
        parent = await self._comment_or_404(task_id, parent_id)
        return parent.parent_id or parent.id

    async def add_comment(self, task_id: uuid.UUID, data: CommentCreate) -> CommentRead:
        self.ctx.require("tasks.comment.write")
        task = await self.repo.get_or_404(task_id)
        parent_id = await self._resolve_parent(task_id, data.parent_id)
        body = sanitize_markdown(data.body) or ""
        excerpt = _excerpt(body)
        # Mentions are captured structurally from the `@[Name](mention:<uuid>)` markers, validated
        # against org membership so a stray id can't notify someone in another tenant (issue #63).
        mentioned = await self._valid_mentions(_extract_mentions(body))
        mentioned_contacts = await self._valid_contact_mentions(extract_contact_mention_ids(body))
        mentioned_tasks = await self._valid_task_mentions(extract_task_mention_ids(body))
        impersonator = self.ctx.impersonated_by
        comment = await self.ctx.repo(TaskComment).create(
            task_id=task_id,
            parent_id=parent_id,
            author_user_id=self.ctx.user.id,
            author_name=_display_name(self.ctx.user),
            # Words written through this account by someone else keep both names (#296).
            impersonator_user_id=impersonator.id if impersonator else None,
            impersonator_name=_display_name(impersonator),
            body=body,
            mentioned_user_ids=[str(uid) for uid in mentioned],
            mentioned_contact_ids=[str(cid) for cid in mentioned_contacts],
            mentioned_task_ids=[str(tid) for tid in mentioned_tasks],
        )
        # The excerpt the notification has always carried belongs in the trail too, with the id
        # to reach the comment by — "commented", on its own, sends you hunting for what (#61).
        # A reply says so, and carries the thread it landed in, so the row reads "replied" and
        # deep-links to the answer rather than to the top of a conversation (#312).
        await self._record(
            task_id,
            "replied" if parent_id else "commented",
            {
                "comment_id": str(comment.id),
                "excerpt": excerpt,
                **({"parent_id": str(parent_id)} if parent_id else {}),
            },
        )
        # Three sentences, and nobody hears two of them (issue #63, #312). A mention reads as its
        # own ("X mentioned you"), so it wins outright. A reply is the next most specific: the
        # people already in *that thread* are being answered, not merely told the task was
        # commented on, so they get `task.replied` and drop out of the generic fan-out. Everyone
        # else in the task's audience gets `task.commented`, exactly as before.
        mentioned_set = set(mentioned)
        replied = (
            [
                uid
                for uid in await self._thread_audience(task, parent_id)
                if uid not in mentioned_set
            ]
            if parent_id
            else []
        )
        heard = mentioned_set | set(replied)
        commented = [uid for uid in await self._comment_audience(task) if uid not in heard]
        if commented:
            # Leaving someone out of the recipient list is not enough to stop the general
            # sentence reaching them: the fan-out unions in the task's *watchers*, and commenting
            # auto-watches, so everyone in `heard` is very likely watching. `_exclude` is what
            # actually says "these people already heard it, in better words" (#312) — which also
            # closes the same hole for a mentioned watcher, who used to get both.
            await self._emit_task(
                "task.commented",
                task,
                commented,
                {"excerpt": excerpt, "_exclude": list(heard)},
            )
        if replied:
            # Same rule one rung up: a mentioned person in this thread is watching it, so the
            # reply would reach them as a watcher despite being left out of the list.
            await self._emit_task(
                "task.replied",
                task,
                replied,
                {"excerpt": excerpt, "_exclude": list(mentioned_set)},
            )
        if mentioned:
            await self._emit_task("task.mentioned", task, mentioned, {"excerpt": excerpt})
        return CommentRead.model_validate(comment).model_copy(
            update={"author_name": _display_name(self.ctx.user)}
        )

    async def _comment_audience(self, task: Task) -> list[uuid.UUID]:
        """Who is in this conversation: the assignee and everyone who commented before."""
        authors = set(
            (
                await self.ctx.session.execute(
                    select(TaskComment.author_user_id)
                    .where(
                        TaskComment.org_id == self.ctx.org.id,
                        TaskComment.task_id == task.id,
                        TaskComment.author_user_id.is_not(None),
                    )
                    .distinct()
                )
            ).scalars()
        )
        if task.assignee_user_id is not None:
            authors.add(task.assignee_user_id)
        return list(authors)

    async def _thread_audience(self, task: Task, root_id: uuid.UUID) -> list[uuid.UUID]:
        """Who is in *this thread*: whoever opened it and everyone who has answered in it (#312).

        Deliberately **not** the assignee. A reply answers the people holding the conversation;
        an assignee who has never written in it is being told the task was commented on, which is
        the sentence `task.commented` already says. Folding them in here would turn the whole
        distinction back into one event with two names.
        """
        return list(
            (
                await self.ctx.session.execute(
                    select(TaskComment.author_user_id)
                    .where(
                        TaskComment.org_id == self.ctx.org.id,
                        TaskComment.task_id == task.id,
                        or_(TaskComment.id == root_id, TaskComment.parent_id == root_id),
                        TaskComment.author_user_id.is_not(None),
                    )
                    .distinct()
                )
            ).scalars()
        )

    async def _comment_or_404(self, task_id: uuid.UUID, comment_id: uuid.UUID) -> TaskComment:
        comment = await self.ctx.repo(TaskComment).get_or_404(comment_id)
        if comment.task_id != task_id:
            raise AppError("not_found", "errors.not_found", status_code=404)
        return comment

    async def update_comment(
        self, task_id: uuid.UUID, comment_id: uuid.UUID, data: CommentUpdate
    ) -> CommentRead:
        comment = await self._comment_or_404(task_id, comment_id)
        # Editing (as opposed to deleting) someone else's words is nobody's capability.
        if comment.author_user_id != self.ctx.user.id:
            raise AppError("forbidden", "errors.forbidden", status_code=403)
        self.ctx.require("tasks.comment.write")
        body = sanitize_markdown(data.body) or ""
        # Keep the stored mention set in step with the edited body (issue #63). Editing does not
        # re-notify — a mention notifies once, when it is first written, like the comment itself.
        mentioned = await self._valid_mentions(_extract_mentions(body))
        mentioned_contacts = await self._valid_contact_mentions(extract_contact_mention_ids(body))
        mentioned_tasks = await self._valid_task_mentions(extract_task_mention_ids(body))
        comment = await self.ctx.repo(TaskComment).update(
            comment,
            body=body,
            mentioned_user_ids=[str(uid) for uid in mentioned],
            mentioned_contact_ids=[str(cid) for cid in mentioned_contacts],
            mentioned_task_ids=[str(tid) for tid in mentioned_tasks],
            edited_at=datetime.now(UTC),
        )
        await self._record(
            task_id,
            "comment_edited",
            {"comment_id": str(comment.id), "excerpt": _excerpt(comment.body)},
        )
        return CommentRead.model_validate(comment).model_copy(
            update={"author_name": _display_name(self.ctx.user)}
        )

    async def delete_comment(self, task_id: uuid.UUID, comment_id: uuid.UUID) -> None:
        comment = await self._comment_or_404(task_id, comment_id)
        scope = None if comment.author_user_id == self.ctx.user.id else "any"
        self.ctx.require("tasks.comment.write", scope=scope)
        body = comment.body
        # Deleting a thread opener takes its answers with it (``ON DELETE CASCADE``, #312), so the
        # trail records how many words went with it — otherwise a five-message conversation
        # vanishes behind a line describing one of them. Counted *before* the delete, obviously.
        replies = await self.reply_count(task_id, comment_id) if comment.parent_id is None else 0
        await self.ctx.repo(TaskComment).delete(comment)
        # No id to link to — the row is gone. The excerpt is the only record of what was said.
        await self._record(
            task_id,
            "comment_deleted",
            {"excerpt": _excerpt(body), **({"replies": replies} if replies else {})},
        )

    async def reply_count(self, task_id: uuid.UUID, comment_id: uuid.UUID) -> int:
        """How many answers a thread opener carries — what a delete would take with it (#312)."""
        return int(
            (
                await self.ctx.session.execute(
                    select(func.count())
                    .select_from(TaskComment)
                    .where(
                        TaskComment.org_id == self.ctx.org.id,
                        TaskComment.task_id == task_id,
                        TaskComment.parent_id == comment_id,
                    )
                )
            ).scalar_one()
        )
