"""Business logic for tasks — all DB access via the tenant-scoped repository (CLAUDE.md §6)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from app.core.tenancy import RequestContext
from app.modules.tasks.models import Task, TaskStatus
from app.modules.tasks.schemas import TaskCreate, TaskUpdate

_OPEN_STATUSES = (TaskStatus.OPEN.value, TaskStatus.IN_PROGRESS.value)


class TaskService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = ctx.repo(Task)

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        company_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        assignee_user_id: uuid.UUID | None = None,
        status: TaskStatus | None = None,
    ) -> tuple[Sequence[Task], int]:
        filters: dict = {}
        if company_id is not None:
            filters["company_id"] = company_id
        if project_id is not None:
            filters["project_id"] = project_id
        if assignee_user_id is not None:
            filters["assignee_user_id"] = assignee_user_id
        if status is not None:
            filters["status"] = status.value
        order = Task.due_date.asc().nulls_last()
        items = await self.repo.list(limit=limit, offset=offset, order_by=order, **filters)
        total = await self.repo.count(**filters)
        return items, total

    async def my_open(self, *, limit: int = 20) -> Sequence[Task]:
        """Open/in-progress tasks assigned to the current user (My Day)."""
        stmt = (
            self.repo.scoped_select()
            .where(Task.assignee_user_id == self.ctx.user.id)
            .where(Task.status.in_(_OPEN_STATUSES))
            .order_by(Task.due_date.asc().nulls_last(), Task.created_at.desc())
            .limit(limit)
        )
        return (await self.ctx.session.execute(stmt)).scalars().all()

    async def get(self, task_id: uuid.UUID) -> Task:
        return await self.repo.get_or_404(task_id)

    async def create(self, data: TaskCreate) -> Task:
        self.ctx.ensure_can_write()
        return await self.repo.create(**data.model_dump())

    async def update(self, task_id: uuid.UUID, data: TaskUpdate) -> Task:
        self.ctx.ensure_can_write()
        task = await self.repo.get_or_404(task_id)
        return await self.repo.update(task, **data.model_dump(exclude_unset=True))

    async def delete(self, task_id: uuid.UUID) -> None:
        self.ctx.ensure_can_write()
        task = await self.repo.get_or_404(task_id)
        await self.repo.delete(task)
