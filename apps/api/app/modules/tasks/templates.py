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

from app.core.richtext import sanitize_markdown
from app.core.tenancy import RequestContext
from app.errors import AppError
from app.modules.tasks.models import (
    Task,
    TaskActivity,
    TaskChecklist,
    TaskChecklistItem,
    TaskTemplate,
    TaskTemplateItem,
    TemplateTrigger,
)
from app.modules.tasks.recurrence import today_local
from app.modules.tasks.schemas import (
    TemplateChecklistItem,
    TemplateCreate,
    TemplateItemBase,
    TemplateItemRead,
    TemplateRead,
    TemplateUpdate,
)
from app.modules.tasks.service import _display_name, _rich_items
from app.modules.tasks.statuses import default_key, load_statuses


class TemplateService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = ctx.repo(TaskTemplate)
        self.item_repo = ctx.repo(TaskTemplateItem)

    @staticmethod
    def _item_read(item: TaskTemplateItem) -> TemplateItemRead:
        # Constructed by hand rather than ``model_validate`` because ``checklist_items`` is served
        # from the authoritative ``checklist_items_rich`` column, not the model attribute of the
        # same name (the legacy title-only array kept only for rollback — issue #66).
        return TemplateItemRead(
            id=item.id,
            title=item.title,
            description=item.description,
            priority=item.priority,
            relative_due_days=item.relative_due_days,
            allocated_minutes=item.allocated_minutes,
            assignee_user_id=item.assignee_user_id,
            position=item.position,
            checklist_title=item.checklist_title,
            checklist_items=[
                TemplateChecklistItem(
                    title=str(entry.get("title") or ""), description=entry.get("description")
                )
                for entry in _rich_items(item.checklist_items_rich, item.checklist_items)
            ],
        )

    async def _read(self, template: TaskTemplate) -> TemplateRead:
        items = await self.item_repo.list(
            limit=200, order_by=TaskTemplateItem.position.asc(), template_id=template.id
        )
        read = TemplateRead.model_validate(template)
        read.items = [self._item_read(i) for i in items]
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
            values = item.model_dump(exclude={"checklist_items"})
            values["priority"] = item.priority.value
            values["position"] = index
            # Markdown source, sanitized on write (issue #66).
            values["description"] = sanitize_markdown(item.description)
            # Dual-write the checklist items expand/contract: the authoritative rich objects and the
            # legacy title-only array a rolled-back previous image still reads (docs/WORKFLOW.md).
            values["checklist_items_rich"] = [
                {"title": ci.title, "description": sanitize_markdown(ci.description)}
                for ci in item.checklist_items
            ]
            values["checklist_items"] = [ci.title for ci in item.checklist_items]
            await self.item_repo.create(template_id=template.id, **values)

    async def create(self, data: TemplateCreate) -> TemplateRead:
        self.ctx.require("tasks.template.write")
        values = data.model_dump(exclude={"items"})
        values["trigger"] = data.trigger.value
        template = await self.repo.create(**values)
        await self._replace_items(template, data.items)
        return await self._read(template)

    async def update(self, template_id: uuid.UUID, data: TemplateUpdate) -> TemplateRead:
        self.ctx.require("tasks.template.write")
        template = await self.repo.get_or_404(template_id)
        values = data.model_dump(exclude_unset=True, exclude={"items"})
        if values.get("trigger") is not None:
            values["trigger"] = data.trigger.value  # type: ignore[union-attr]
        template = await self.repo.update(template, **values)
        if data.items is not None:
            await self._replace_items(template, data.items)
        return await self._read(template)

    async def delete(self, template_id: uuid.UUID) -> None:
        self.ctx.require("tasks.template.write")
        template = await self.repo.get_or_404(template_id)
        await self.repo.delete(template)

    # ------------------------------------------------------------------ #
    # Instantiation
    # ------------------------------------------------------------------ #
    async def apply(self, template_id: uuid.UUID, company_id: uuid.UUID) -> list[Task]:
        self.ctx.require("tasks.template.apply")
        template = await self.repo.get_or_404(template_id)
        return await self._instantiate(
            template,
            company_id,
            actor_id=self.ctx.user.id,
            actor_name=_display_name(self.ctx.user),
        )

    async def _instantiate(
        self,
        template: TaskTemplate,
        company_id: uuid.UUID,
        *,
        actor_id: uuid.UUID | None,
        # Snapshotted beside the id: the FK is SET NULL, so without this the applier's name is
        # gone the day their account is (issue #64).
        actor_name: str | None = None,
    ) -> list[Task]:
        items = await self.item_repo.list(
            limit=200, order_by=TaskTemplateItem.position.asc(), template_id=template.id
        )
        session = self.ctx.session
        org_id = self.ctx.org.id
        # New tasks land in the org's default status (issue #62), not a hardcoded "open".
        default_status = default_key(await load_statuses(session, org_id))
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
                status=default_status,
                priority=item.priority,
                due_date=due,
                allocated_minutes=item.allocated_minutes,
                position=max_position + 1024.0 * offset,
            )
            session.add(task)
            await session.flush()

            checklist_items = _rich_items(item.checklist_items_rich, item.checklist_items)
            if checklist_items:
                checklist = TaskChecklist(
                    org_id=org_id,
                    task_id=task.id,
                    title=item.checklist_title or template.name,
                    position=0,
                )
                session.add(checklist)
                await session.flush()
                for index, entry in enumerate(checklist_items):
                    session.add(
                        TaskChecklistItem(
                            org_id=org_id,
                            checklist_id=checklist.id,
                            title=str(entry.get("title") or "")[:512],
                            # Already sanitized at template-write time; copied verbatim (issue #66).
                            description=entry.get("description"),
                            position=index,
                        )
                    )

            session.add(
                TaskActivity(
                    org_id=org_id,
                    task_id=task.id,
                    actor_user_id=actor_id,
                    actor_name=actor_name,
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
            await self._instantiate(
                template,
                company_id,
                actor_id=self.ctx.user.id,
                actor_name=_display_name(self.ctx.user),
            )


async def on_company_status(ctx: RequestContext, payload: dict[str, Any]) -> None:
    """Event handler for ``company.created`` and ``company.status_changed``."""
    company_id = payload["company_id"]
    status = payload["status"]
    if not isinstance(company_id, uuid.UUID):
        raise AppError("validation", "errors.validation")
    await TemplateService(ctx).instantiate_for_status(company_id, str(status))
