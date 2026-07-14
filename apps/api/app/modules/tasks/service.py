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

from sqlalchemy import bindparam, case, func, select
from sqlalchemy import text as sql_text

from app.core.auth.models import User
from app.core.events import emit
from app.core.models import Membership
from app.core.richtext import (
    extract_contact_mention_ids,
    extract_mention_ids,
    markdown_to_plaintext,
    sanitize_markdown,
)
from app.core.sorting import apply_sort, user_sort_name
from app.core.tenancy import RequestContext
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
from app.modules.tasks.schemas import (
    ActivityRead,
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
    StatusUpdate,
    TaskCreate,
    TaskDetail,
    TaskListItem,
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
    "company_id",
    "project_id",
    "recurrence",
    "requires_interaction",
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

    The body is markdown now (issue #66), so flatten it to plain text *before* the length cap —
    otherwise the bell dropdown shows literal ``**bold**`` / ``[label](url)`` syntax, and cutting
    by character count could sever a link mid-``()``.
    """
    text = " ".join(markdown_to_plaintext(body).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


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


class TaskService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = ctx.repo(Task)

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
        self.ctx.session.add(
            TaskActivity(
                org_id=self.ctx.org.id,
                task_id=task_id,
                actor_user_id=self.ctx.user.id,
                # Snapshotted, so deleting the account doesn't hand this line to "System" (#64).
                actor_name=_display_name(self.ctx.user),
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

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        company_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        assignee_user_id: uuid.UUID | None = None,
        status: str | None = None,
        label_id: uuid.UUID | None = None,
        due: str | None = None,
        due_from: date | None = None,
        due_to: date | None = None,
        q: str | None = None,
        sort: str | None = None,
        with_meta: bool = True,
        count: bool = True,
    ) -> tuple[list[TaskListItem], int]:
        stmt = self.repo.scoped_select()
        if q:
            stmt = stmt.where(Task.title.ilike(f"%{q.strip()}%"))
        if company_id is not None:
            stmt = stmt.where(Task.company_id == company_id)
        if project_id is not None:
            stmt = stmt.where(Task.project_id == project_id)
        if assignee_user_id is not None:
            stmt = stmt.where(Task.assignee_user_id == assignee_user_id)
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
        today = rec_mod.today_local()
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
        if not with_meta:
            # Lookup lists (pickers) don't need the aggregate chips — skip three queries.
            return [TaskListItem.model_validate(t) for t in tasks], total
        return await self._list_items(tasks), total

    async def my_open(self, *, limit: int = 20) -> list[TaskListItem]:
        """Unfinished tasks assigned to the current user (My Day)."""
        statuses = await load_statuses(self.ctx.session, self.ctx.org.id)
        stmt = (
            self.repo.scoped_select()
            .where(Task.assignee_user_id == self.ctx.user.id)
            .where(Task.status.in_(non_terminal_keys(statuses)))
            .order_by(Task.due_date.asc().nulls_last(), Task.created_at.desc())
            .limit(limit)
        )
        tasks = (await self.ctx.session.execute(stmt)).scalars().all()
        return await self._list_items(tasks)

    # ------------------------------------------------------------------ #
    # Detail
    # ------------------------------------------------------------------ #
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

        comment_rows = (
            await self.ctx.session.execute(
                select(TaskComment, User)
                .outerjoin(User, User.id == TaskComment.author_user_id)
                .where(
                    TaskComment.org_id == self.ctx.org.id,
                    TaskComment.task_id == task_id,
                )
                .order_by(TaskComment.created_at.asc())
            )
        ).all()
        detail.comments = []
        for comment, author in comment_rows:
            name, deleted = _attribution(author, comment.author_name)
            detail.comments.append(
                CommentRead.model_validate(comment).model_copy(
                    update={"author_name": name, "author_deleted": deleted}
                )
            )

        activity_rows = (
            await self.ctx.session.execute(
                select(TaskActivity, User)
                .outerjoin(User, User.id == TaskActivity.actor_user_id)
                .where(
                    TaskActivity.org_id == self.ctx.org.id,
                    TaskActivity.task_id == task_id,
                )
                .order_by(TaskActivity.created_at.desc())
                .limit(50)
            )
        ).all()
        detail.activities = []
        for activity, actor in activity_rows:
            name, deleted = _attribution(actor, activity.actor_name)
            detail.activities.append(
                ActivityRead.model_validate(activity).model_copy(
                    update={"actor_name": name, "actor_deleted": deleted}
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

        # Minutes booked on this task — cross-module read by table name only (FK convention).
        logged = await self.ctx.session.scalar(
            sql_text(
                "SELECT COALESCE(SUM(minutes), 0) FROM time_entries "
                "WHERE org_id = :org_id AND task_id = :task_id AND ended_at IS NOT NULL"
            ),
            {"org_id": str(self.ctx.org.id), "task_id": str(task_id)},
        )
        detail.logged_minutes = int(logged or 0)

        detail.checklists = checklist_reads
        return detail

    # ------------------------------------------------------------------ #
    # Links (URL attachments)
    # ------------------------------------------------------------------ #
    async def add_link(self, task_id: uuid.UUID, data: LinkCreate) -> TaskLink:
        await self._writable_task_or_403(task_id)
        url = data.url if "://" in data.url else f"https://{data.url}"
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
        # Markdown source is stored; strip any raw HTML on write (issue #66, app/core/richtext).
        values["description"] = sanitize_markdown(values.get("description"))
        # Verantwoordelijke defaults down: project's responsible → else the company's,
        # when the task has no explicit assignee (overridable per task).
        if values.get("assignee_user_id") is None:
            values["assignee_user_id"] = await self._default_assignee(
                values.get("project_id"), values.get("company_id")
            )
        # Status is a tenant-configured key (issue #62): unset falls to the org's default status,
        # anything else must be one the org actually defined.
        statuses = await load_statuses(self.ctx.session, self.ctx.org.id)
        values["status"] = self._resolve_status(statuses, data.status, allow_default=True)
        values["priority"] = data.priority.value
        values["recurrence"] = data.recurrence.model_dump(mode="json") if data.recurrence else None
        values["recurrence_next_run"] = rec_mod.compute_next_run(
            values["recurrence"], data.due_date
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

        if old_status not in terminal and new_status in terminal:
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
            )
            # spawn_next mutates the source (recurrence handed off); reload server-side
            # defaults so serialization never lazy-loads.
            await self.ctx.session.refresh(task)
        return task

    async def delete(self, task_id: uuid.UUID) -> None:
        self.ctx.require("tasks.task.delete")
        task = await self.repo.get_or_404(task_id)
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
        """Keep only the mentioned contact ids that belong to this org (#165) — a reference
        into the CRM, never a notification: contacts have no inbox here."""
        if not ids:
            return []
        stmt = sql_text(
            "SELECT id FROM contacts WHERE org_id = :oid AND id IN :ids"
        ).bindparams(bindparam("ids", expanding=True))
        found = set(
            (
                await self.ctx.session.execute(
                    stmt, {"oid": self.ctx.org.id, "ids": list(ids)}
                )
            ).scalars()
        )
        return [cid for cid in ids if cid in found]

    async def add_comment(self, task_id: uuid.UUID, data: CommentCreate) -> CommentRead:
        self.ctx.require("tasks.comment.write")
        task = await self.repo.get_or_404(task_id)
        body = sanitize_markdown(data.body) or ""
        excerpt = _excerpt(body)
        # Mentions are captured structurally from the `@[Name](mention:<uuid>)` markers, validated
        # against org membership so a stray id can't notify someone in another tenant (issue #63).
        mentioned = await self._valid_mentions(_extract_mentions(body))
        mentioned_contacts = await self._valid_contact_mentions(extract_contact_mention_ids(body))
        comment = await self.ctx.repo(TaskComment).create(
            task_id=task_id,
            author_user_id=self.ctx.user.id,
            author_name=_display_name(self.ctx.user),
            body=body,
            mentioned_user_ids=[str(uid) for uid in mentioned],
            mentioned_contact_ids=[str(cid) for cid in mentioned_contacts],
        )
        # The excerpt the notification has always carried belongs in the trail too, with the id
        # to reach the comment by — "commented", on its own, sends you hunting for what (#61).
        await self._record(
            task_id, "commented", {"comment_id": str(comment.id), "excerpt": excerpt}
        )
        # A mention reads as its own sentence ("X mentioned you"), so a mentioned person gets
        # `task.mentioned` even when they are neither the assignee nor a prior commenter — and is
        # dropped from the generic `task.commented` fan-out so they aren't told twice (issue #63).
        mentioned_set = set(mentioned)
        commented = [
            uid for uid in await self._comment_audience(task) if uid not in mentioned_set
        ]
        if commented:
            await self._emit_task("task.commented", task, commented, {"excerpt": excerpt})
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
        comment = await self.ctx.repo(TaskComment).update(
            comment,
            body=body,
            mentioned_user_ids=[str(uid) for uid in mentioned],
            mentioned_contact_ids=[str(cid) for cid in mentioned_contacts],
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
        await self.ctx.repo(TaskComment).delete(comment)
        # No id to link to — the row is gone. The excerpt is the only record of what was said.
        await self._record(task_id, "comment_deleted", {"excerpt": _excerpt(body)})
