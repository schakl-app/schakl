"""Business logic for projects — all DB access via the tenant-scoped repository (CLAUDE.md §6).

Customizable entity: ``custom`` is validated against the tenant's definitions on every write.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from app.core.customfields import CustomFieldsService
from app.core.tenancy import RequestContext
from app.modules.projects.models import Project, ProjectStatus
from app.modules.projects.schemas import ProjectCreate, ProjectUpdate

ENTITY_TYPE = "project"


class ProjectService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = ctx.repo(Project)
        self.custom_fields = CustomFieldsService(ctx)

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        company_id: uuid.UUID | None = None,
        status: ProjectStatus | None = None,
    ) -> tuple[Sequence[Project], int]:
        filters: dict = {}
        if company_id is not None:
            filters["company_id"] = company_id
        if status is not None:
            filters["status"] = status.value
        order = Project.name.asc()
        items = await self.repo.list(limit=limit, offset=offset, order_by=order, **filters)
        total = await self.repo.count(**filters)
        return items, total

    async def get(self, project_id: uuid.UUID) -> Project:
        return await self.repo.get_or_404(project_id)

    async def create(self, data: ProjectCreate) -> Project:
        self.ctx.ensure_can_write()
        values = data.model_dump()
        values["custom"] = await self.custom_fields.validate(
            ENTITY_TYPE, values.get("custom") or {}
        )
        return await self.repo.create(**values)

    async def update(self, project_id: uuid.UUID, data: ProjectUpdate) -> Project:
        self.ctx.ensure_can_write()
        project = await self.repo.get_or_404(project_id)
        values = data.model_dump(exclude_unset=True)
        if "custom" in values:
            values["custom"] = await self.custom_fields.validate(
                ENTITY_TYPE, values.get("custom") or {}
            )
        return await self.repo.update(project, **values)

    async def delete(self, project_id: uuid.UUID) -> None:
        self.ctx.ensure_can_write()
        project = await self.repo.get_or_404(project_id)
        await self.repo.delete(project)
