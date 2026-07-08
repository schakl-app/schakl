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
        q: str | None = None,
    ) -> tuple[Sequence[Project], int]:
        from sqlalchemy import func, select

        conditions = []
        if company_id is not None:
            conditions.append(Project.company_id == company_id)
        if status is not None:
            conditions.append(Project.status == status.value)
        if q:
            conditions.append(Project.name.ilike(f"%{q.strip()}%"))
        stmt = (
            self.repo.scoped_select()
            .where(*conditions)
            .order_by(Project.name.asc())
            .limit(limit)
            .offset(offset)
        )
        items = (await self.ctx.session.execute(stmt)).scalars().all()
        total = int(
            await self.ctx.session.scalar(
                select(func.count())
                .select_from(Project)
                .where(Project.org_id == self.ctx.org.id, *conditions)
            )
            or 0
        )
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
