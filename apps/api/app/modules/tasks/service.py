"""Business logic for tasks — all DB access via the tenant-scoped repository (CLAUDE.md §6).

Besides task CRUD this hosts the card satellites: labels, checklists, comments, and the
append-only activity log. Every mutation records who did what so the detail view can show
a Trello-style history.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy import text as sql_text

from app.core.auth.models import User
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
    TaskStatus,
)
from app.modules.tasks.schemas import (
    ActivityRead,
    ChecklistCreate,
    ChecklistItemCreate,
    ChecklistItemRead,
    ChecklistItemUpdate,
    ChecklistRead,
    ChecklistTemplateCreate,
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
    TaskListItem,
    TaskUpdate,
)

_OPEN_STATUSES = (TaskStatus.OPEN.value, TaskStatus.IN_PROGRESS.value)

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
)


def _display_name(user: User | None) -> str | None:
    if user is None:
        return None
    return user.full_name or user.email


class TaskService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = ctx.repo(Task)

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
                action=action,
                payload=payload or {},
            )
        )
        await self.ctx.session.flush()

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
        status: TaskStatus | None = None,
        label_id: uuid.UUID | None = None,
        due: str | None = None,
        q: str | None = None,
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
            stmt = stmt.where(Task.status == status.value)
        if label_id is not None:
            stmt = stmt.where(
                Task.id.in_(
                    select(TaskLabelLink.task_id).where(
                        TaskLabelLink.org_id == self.ctx.org.id,
                        TaskLabelLink.label_id == label_id,
                    )
                )
            )
        today = rec_mod.today_local()
        if due == "overdue":
            stmt = stmt.where(Task.due_date < today, Task.status.in_(_OPEN_STATUSES))
        elif due == "today":
            stmt = stmt.where(Task.due_date == today)
        elif due == "week":
            stmt = stmt.where(Task.due_date >= today, Task.due_date <= today + timedelta(days=7))

        total = 0
        if count:
            count_stmt = select(func.count()).select_from(stmt.subquery())
            total = int(await self.ctx.session.scalar(count_stmt) or 0)

        stmt = stmt.order_by(Task.position.asc(), Task.created_at.asc()).limit(limit).offset(offset)
        tasks = (await self.ctx.session.execute(stmt)).scalars().all()
        if not count:
            total = len(tasks)
        if not with_meta:
            # Lookup lists (pickers) don't need the aggregate chips — skip three queries.
            return [TaskListItem.model_validate(t) for t in tasks], total
        return await self._list_items(tasks), total

    async def my_open(self, *, limit: int = 20) -> list[TaskListItem]:
        """Open/in-progress tasks assigned to the current user (My Day)."""
        stmt = (
            self.repo.scoped_select()
            .where(Task.assignee_user_id == self.ctx.user.id)
            .where(Task.status.in_(_OPEN_STATUSES))
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
        detail.comments = [
            CommentRead.model_validate(c).model_copy(update={"author_name": _display_name(u)})
            for c, u in comment_rows
        ]

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
        detail.activities = [
            ActivityRead.model_validate(a).model_copy(update={"actor_name": _display_name(u)})
            for a, u in activity_rows
        ]
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
        self.ctx.ensure_can_write()
        await self.repo.get_or_404(task_id)
        url = data.url if "://" in data.url else f"https://{data.url}"
        return await self.ctx.repo(TaskLink).create(
            task_id=task_id, url=url, title=data.title
        )

    async def delete_link(self, task_id: uuid.UUID, link_id: uuid.UUID) -> None:
        self.ctx.ensure_can_write()
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
        self.ctx.ensure_can_write()
        values = data.model_dump()
        # Verantwoordelijke defaults down: project's responsible → else the company's,
        # when the task has no explicit assignee (overridable per task).
        if values.get("assignee_user_id") is None:
            values["assignee_user_id"] = await self._default_assignee(
                values.get("project_id"), values.get("company_id")
            )
        values["status"] = data.status.value
        values["priority"] = data.priority.value
        values["recurrence"] = data.recurrence.model_dump(mode="json") if data.recurrence else None
        values["recurrence_next_run"] = rec_mod.compute_next_run(
            values["recurrence"], data.due_date
        )
        values["position"] = await self._next_position()
        task = await self.repo.create(**values)
        await self._record(task.id, "created")
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

    async def update(self, task_id: uuid.UUID, data: TaskUpdate) -> Task:
        self.ctx.ensure_can_write()
        task = await self.repo.get_or_404(task_id)
        values = data.model_dump(exclude_unset=True)
        reason = values.pop("due_change_reason", None)

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

        if values.get("status") is not None:
            values["status"] = data.status.value  # type: ignore[union-attr]
        if values.get("priority") is not None:
            values["priority"] = data.priority.value  # type: ignore[union-attr]
        if "recurrence" in values:
            values["recurrence"] = (
                data.recurrence.model_dump(mode="json") if data.recurrence else None
            )

        old_status = task.status
        new_status = values.get("status", old_status)
        if old_status != TaskStatus.DONE.value and new_status == TaskStatus.DONE.value:
            values["completed_at"] = datetime.now(UTC)
        elif old_status == TaskStatus.DONE.value and new_status != TaskStatus.DONE.value:
            values["completed_at"] = None

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

        task = await self.repo.update(task, **values)

        if status_changed:
            await self._record(
                task.id, "status_changed", {"from": old_status, "to": new_status}
            )
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
            and new_status == TaskStatus.DONE.value
            and (task.recurrence or {}).get("mode") == RecurrenceMode.AFTER_COMPLETION.value
        ):
            await rec_mod.spawn_next(
                self.ctx.session, self.ctx.org.id, task, actor_user_id=self.ctx.user.id
            )
            # spawn_next mutates the source (recurrence handed off); reload server-side
            # defaults so serialization never lazy-loads.
            await self.ctx.session.refresh(task)
        return task

    async def delete(self, task_id: uuid.UUID) -> None:
        self.ctx.ensure_can_write()
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
        self.ctx.ensure_can_write()
        repo = self.ctx.repo(TaskLabel)
        if await repo.count(name=data.name):
            raise AppError("conflict", "errors.conflict", status_code=409)
        return await repo.create(**data.model_dump())

    async def update_label(self, label_id: uuid.UUID, data: LabelUpdate) -> TaskLabel:
        self.ctx.ensure_can_write()
        repo = self.ctx.repo(TaskLabel)
        label = await repo.get_or_404(label_id)
        return await repo.update(label, **data.model_dump(exclude_unset=True))

    async def delete_label(self, label_id: uuid.UUID) -> None:
        self.ctx.ensure_can_write()
        repo = self.ctx.repo(TaskLabel)
        label = await repo.get_or_404(label_id)
        await repo.delete(label)

    async def set_task_labels(
        self, task_id: uuid.UUID, label_ids: list[uuid.UUID]
    ) -> list[TaskLabel]:
        self.ctx.ensure_can_write()
        await self.repo.get_or_404(task_id)
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
    # Checklists
    # ------------------------------------------------------------------ #
    async def add_checklist(self, task_id: uuid.UUID, data: ChecklistCreate) -> TaskChecklist:
        """A fresh checklist, or a copy of an org checklist template (title + items)."""
        self.ctx.ensure_can_write()
        await self.repo.get_or_404(task_id)

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
        checklist = await repo.create(task_id=task_id, title=title, position=position)
        if template is not None:
            for index, item_title in enumerate(template.items):
                self.ctx.session.add(
                    TaskChecklistItem(
                        org_id=self.ctx.org.id,
                        checklist_id=checklist.id,
                        title=str(item_title)[:512],
                        position=index,
                    )
                )
            await self.ctx.session.flush()
        return checklist

    # ------------------------------------------------------------------ #
    # Checklist templates (org-wide repository)
    # ------------------------------------------------------------------ #
    async def list_checklist_templates(self) -> Sequence[TaskChecklistTemplate]:
        return await self.ctx.repo(TaskChecklistTemplate).list(
            limit=200, order_by=TaskChecklistTemplate.title.asc()
        )

    async def create_checklist_template(
        self, data: ChecklistTemplateCreate
    ) -> TaskChecklistTemplate:
        self.ctx.ensure_can_write()
        return await self.ctx.repo(TaskChecklistTemplate).create(**data.model_dump())

    async def update_checklist_template(
        self, template_id: uuid.UUID, data: ChecklistTemplateUpdate
    ) -> TaskChecklistTemplate:
        self.ctx.ensure_can_write()
        repo = self.ctx.repo(TaskChecklistTemplate)
        template = await repo.get_or_404(template_id)
        return await repo.update(template, **data.model_dump(exclude_unset=True))

    async def delete_checklist_template(self, template_id: uuid.UUID) -> None:
        self.ctx.ensure_can_write()
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
        self.ctx.ensure_can_write()
        checklist = await self._checklist_or_404(task_id, checklist_id)
        return await self.ctx.repo(TaskChecklist).update(
            checklist, **data.model_dump(exclude_unset=True)
        )

    async def delete_checklist(self, task_id: uuid.UUID, checklist_id: uuid.UUID) -> None:
        self.ctx.ensure_can_write()
        checklist = await self._checklist_or_404(task_id, checklist_id)
        await self.ctx.repo(TaskChecklist).delete(checklist)
        await self._record(task_id, "checklist_deleted", {"title": checklist.title})

    async def add_checklist_item(
        self, task_id: uuid.UUID, checklist_id: uuid.UUID, data: ChecklistItemCreate
    ) -> TaskChecklistItem:
        self.ctx.ensure_can_write()
        await self._checklist_or_404(task_id, checklist_id)
        repo = self.ctx.repo(TaskChecklistItem)
        position = await repo.count(checklist_id=checklist_id)
        return await repo.create(checklist_id=checklist_id, title=data.title, position=position)

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
        self.ctx.ensure_can_write()
        item = await self._item_or_404(task_id, checklist_id, item_id)
        return await self.ctx.repo(TaskChecklistItem).update(
            item, **data.model_dump(exclude_unset=True)
        )

    async def delete_checklist_item(
        self, task_id: uuid.UUID, checklist_id: uuid.UUID, item_id: uuid.UUID
    ) -> None:
        self.ctx.ensure_can_write()
        item = await self._item_or_404(task_id, checklist_id, item_id)
        await self.ctx.repo(TaskChecklistItem).delete(item)
        await self._record(task_id, "checklist_item_deleted", {"title": item.title})

    # ------------------------------------------------------------------ #
    # Comments
    # ------------------------------------------------------------------ #
    async def add_comment(self, task_id: uuid.UUID, data: CommentCreate) -> CommentRead:
        self.ctx.ensure_can_write()
        await self.repo.get_or_404(task_id)
        comment = await self.ctx.repo(TaskComment).create(
            task_id=task_id, author_user_id=self.ctx.user.id, body=data.body
        )
        await self._record(task_id, "commented")
        return CommentRead.model_validate(comment).model_copy(
            update={"author_name": _display_name(self.ctx.user)}
        )

    async def _comment_or_404(self, task_id: uuid.UUID, comment_id: uuid.UUID) -> TaskComment:
        comment = await self.ctx.repo(TaskComment).get_or_404(comment_id)
        if comment.task_id != task_id:
            raise AppError("not_found", "errors.not_found", status_code=404)
        return comment

    async def update_comment(
        self, task_id: uuid.UUID, comment_id: uuid.UUID, data: CommentUpdate
    ) -> CommentRead:
        self.ctx.ensure_can_write()
        comment = await self._comment_or_404(task_id, comment_id)
        if comment.author_user_id != self.ctx.user.id:
            raise AppError("forbidden", "errors.forbidden", status_code=403)
        comment = await self.ctx.repo(TaskComment).update(
            comment, body=data.body, edited_at=datetime.now(UTC)
        )
        await self._record(task_id, "comment_edited")
        return CommentRead.model_validate(comment).model_copy(
            update={"author_name": _display_name(self.ctx.user)}
        )

    async def delete_comment(self, task_id: uuid.UUID, comment_id: uuid.UUID) -> None:
        self.ctx.ensure_can_write()
        comment = await self._comment_or_404(task_id, comment_id)
        if comment.author_user_id != self.ctx.user.id and not self.ctx.role.can_manage:
            raise AppError("forbidden", "errors.forbidden", status_code=403)
        await self.ctx.repo(TaskComment).delete(comment)
        await self._record(task_id, "comment_deleted")
