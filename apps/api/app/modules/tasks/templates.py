"""Task templates — reusable checklists like "client onboarding" (CLAUDE.md §6 automation).

A template is applied to a company either manually or automatically when the company enters
the template's trigger status (via the ``company.created`` / ``company.status_changed``
events). Template CRUD is manager-gated; applying one needs only write access.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select

from app.core.tenancy import RequestContext
from app.errors import AppError
from app.modules.tasks.models import (
    Task,
    TaskActivity,
    TaskChecklist,
    TaskChecklistItem,
    TaskStatus,
    TaskTemplate,
    TaskTemplateItem,
    TemplateTrigger,
)
from app.modules.tasks.recurrence import today_local
from app.modules.tasks.schemas import (
    TemplateCreate,
    TemplateItemBase,
    TemplateItemRead,
    TemplateRead,
    TemplateUpdate,
)


class TemplateService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = ctx.repo(TaskTemplate)
        self.item_repo = ctx.repo(TaskTemplateItem)

    async def _read(self, template: TaskTemplate) -> TemplateRead:
        items = await self.item_repo.list(
            limit=200, order_by=TaskTemplateItem.position.asc(), template_id=template.id
        )
        read = TemplateRead.model_validate(template)
        read.items = [TemplateItemRead.model_validate(i) for i in items]
        return read

    async def list(self) -> list[TemplateRead]:
        templates = await self.repo.list(limit=200, order_by=TaskTemplate.name.asc())
        return [await self._read(t) for t in templates]

    async def get(self, template_id: uuid.UUID) -> TemplateRead:
        return await self._read(await self.repo.get_or_404(template_id))

    async def _replace_items(
        self, template: TaskTemplate, items: list[TemplateItemBase]
    ) -> None:
        existing = await self.item_repo.list(limit=200, template_id=template.id)
        for item in existing:
            await self.item_repo.delete(item)
        for index, item in enumerate(items):
            values = item.model_dump()
            values["priority"] = item.priority.value
            values["position"] = index
            await self.item_repo.create(template_id=template.id, **values)

    async def create(self, data: TemplateCreate) -> TemplateRead:
        self.ctx.ensure_can_manage()
        values = data.model_dump(exclude={"items"})
        values["trigger"] = data.trigger.value
        template = await self.repo.create(**values)
        await self._replace_items(template, data.items)
        return await self._read(template)

    async def update(self, template_id: uuid.UUID, data: TemplateUpdate) -> TemplateRead:
        self.ctx.ensure_can_manage()
        template = await self.repo.get_or_404(template_id)
        values = data.model_dump(exclude_unset=True, exclude={"items"})
        if values.get("trigger") is not None:
            values["trigger"] = data.trigger.value  # type: ignore[union-attr]
        template = await self.repo.update(template, **values)
        if data.items is not None:
            await self._replace_items(template, data.items)
        return await self._read(template)

    async def delete(self, template_id: uuid.UUID) -> None:
        self.ctx.ensure_can_manage()
        template = await self.repo.get_or_404(template_id)
        await self.repo.delete(template)

    # ------------------------------------------------------------------ #
    # Instantiation
    # ------------------------------------------------------------------ #
    async def apply(self, template_id: uuid.UUID, company_id: uuid.UUID) -> list[Task]:
        self.ctx.ensure_can_write()
        template = await self.repo.get_or_404(template_id)
        return await self._instantiate(template, company_id, actor_id=self.ctx.user.id)

    async def _instantiate(
        self, template: TaskTemplate, company_id: uuid.UUID, *, actor_id: uuid.UUID | None
    ) -> list[Task]:
        items = await self.item_repo.list(
            limit=200, order_by=TaskTemplateItem.position.asc(), template_id=template.id
        )
        session = self.ctx.session
        org_id = self.ctx.org.id
        max_position = float(
            await session.scalar(
                select(func.max(Task.position)).where(Task.org_id == org_id)
            )
            or 0.0
        )
        created: list[Task] = []
        for offset, item in enumerate(items, start=1):
            due = (
                today_local() + timedelta(days=item.relative_due_days)
                if item.relative_due_days is not None
                else None
            )
            task = Task(
                org_id=org_id,
                company_id=company_id,
                assignee_user_id=item.assignee_user_id,
                title=item.title,
                description=item.description,
                status=TaskStatus.OPEN.value,
                priority=item.priority,
                due_date=due,
                allocated_minutes=item.allocated_minutes,
                position=max_position + 1024.0 * offset,
            )
            session.add(task)
            await session.flush()

            if item.checklist_items:
                checklist = TaskChecklist(
                    org_id=org_id,
                    task_id=task.id,
                    title=item.checklist_title or template.name,
                    position=0,
                )
                session.add(checklist)
                await session.flush()
                for index, title in enumerate(item.checklist_items):
                    session.add(
                        TaskChecklistItem(
                            org_id=org_id,
                            checklist_id=checklist.id,
                            title=str(title)[:512],
                            position=index,
                        )
                    )

            session.add(
                TaskActivity(
                    org_id=org_id,
                    task_id=task.id,
                    actor_user_id=actor_id,
                    action="template_applied",
                    payload={"template_id": str(template.id), "template_name": template.name},
                )
            )
            created.append(task)
        await session.flush()
        return created

    async def instantiate_for_status(self, company_id: uuid.UUID, status: str) -> None:
        """Apply every active template whose trigger status matches (automation path)."""
        stmt = (
            self.repo.scoped_select()
            .where(TaskTemplate.trigger == TemplateTrigger.COMPANY_STATUS.value)
            .where(TaskTemplate.trigger_status == status)
            .where(TaskTemplate.active.is_(True))
        )
        templates = (await self.ctx.session.execute(stmt)).scalars().all()
        for template in templates:
            await self._instantiate(template, company_id, actor_id=self.ctx.user.id)


async def on_company_status(ctx: RequestContext, payload: dict[str, Any]) -> None:
    """Event handler for ``company.created`` and ``company.status_changed``."""
    company_id = payload["company_id"]
    status = payload["status"]
    if not isinstance(company_id, uuid.UUID):
        raise AppError("validation", "errors.validation")
    await TemplateService(ctx).instantiate_for_status(company_id, str(status))
