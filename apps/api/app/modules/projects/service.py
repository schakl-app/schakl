"""Business logic for projects — all DB access via the tenant-scoped repository (CLAUDE.md §6).

Customizable entity: ``custom`` is validated against the tenant's definitions on every write.

Several employees work a project: ``project_assignees`` holds them all, one starred as primary.
``projects.responsible_user_id`` mirrors that primary and is dropped in a later release
(docs/WORKFLOW.md, expand/contract) — read ``primary_assignee()`` instead of the column.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select

from app.core.assignees import AssigneeService
from app.core.customfields import CustomFieldsService
from app.core.tenancy import RequestContext
from app.modules.projects.models import Project, ProjectAssignee, ProjectStatus
from app.modules.projects.schemas import ProjectCreate, ProjectUpdate

ENTITY_TYPE = "project"


class ProjectService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = ctx.repo(Project)
        self.custom_fields = CustomFieldsService(ctx)
        self.assignees = AssigneeService(ctx, ProjectAssignee, "project_id")

    async def _attach_assignees(self, projects: Sequence[Project]) -> None:
        """One extra query for the whole page, never one per row (docs/PERFORMANCE.md)."""
        if not projects:
            return
        grouped = await self.assignees.for_entities([p.id for p in projects])
        for project in projects:
            project.assignees = grouped.get(project.id, [])

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        company_id: uuid.UUID | None = None,
        status: ProjectStatus | None = None,
        q: str | None = None,
        mine: bool = False,
        count: bool = True,
    ) -> tuple[Sequence[Project], int]:
        conditions = []
        if company_id is not None:
            conditions.append(Project.company_id == company_id)
        if status is not None:
            conditions.append(Project.status == status.value)
        if q:
            conditions.append(Project.name.ilike(f"%{q.strip()}%"))
        if mine:
            # "My projects" matches *any* assignee, not just the primary.
            conditions.append(
                Project.id.in_(self.assignees.entity_ids_for_user(self.ctx.user.id))
            )
        stmt = (
            self.repo.scoped_select()
            .where(*conditions)
            .order_by(Project.name.asc())
            .limit(limit)
            .offset(offset)
        )
        items = list((await self.ctx.session.execute(stmt)).scalars().all())
        # ``count=False`` skips the discarded COUNT(*) for name-only lookups.
        total = (
            int(
                await self.ctx.session.scalar(
                    select(func.count())
                    .select_from(Project)
                    .where(Project.org_id == self.ctx.org.id, *conditions)
                )
                or 0
            )
            if count
            else len(items)
        )
        await self._attach_assignees(items)
        return items, total

    async def get(self, project_id: uuid.UUID) -> Project:
        project = await self.repo.get_or_404(project_id)
        await self._attach_assignees([project])
        return project

    async def primary_assignee(self, project_id: uuid.UUID) -> uuid.UUID | None:
        """Who owns this project. Published so other modules never import our models (§6)."""
        return await self.assignees.primary(project_id)

    async def create(self, data: ProjectCreate) -> Project:
        self.ctx.ensure_can_write()
        values = data.model_dump()
        values.pop("assignees", None)

        # A project inherits the client's *primary* when nobody was named — not the client's whole
        # roster, which is a superset of the people actually on this project.
        fallback = data.responsible_user_id
        if data.assignees is None and fallback is None and values.get("company_id") is not None:
            fallback = await self._company_primary(values["company_id"])
        links = self.assignees.normalize(data.assignees, fallback_primary=fallback)
        values["responsible_user_id"] = self.assignees.primary_of(links)

        values["custom"] = await self.custom_fields.validate(
            ENTITY_TYPE, values.get("custom") or {}
        )
        project = await self.repo.create(**values)
        await self.assignees.replace(project.id, links)
        project.assignees = await self.assignees.for_entity(project.id)
        return project

    async def _company_primary(self, company_id: uuid.UUID) -> uuid.UUID | None:
        """The primary assignee of a company, via its published service (§6 — no model
        cross-imports). ``None`` when the client has nobody assigned."""
        from app.modules.companies.service import CompanyService

        return await CompanyService(self.ctx).primary_assignee(company_id)

    async def update(self, project_id: uuid.UUID, data: ProjectUpdate) -> Project:
        self.ctx.ensure_can_write()
        project = await self.repo.get_or_404(project_id)
        values = data.model_dump(exclude_unset=True)
        if "custom" in values:
            values["custom"] = await self.custom_fields.validate(
                ENTITY_TYPE, values.get("custom") or {}
            )

        # Sending ``assignees`` replaces the roster wholesale. Sending only ``responsible_user_id``
        # just moves the star — the other assignees stay put.
        links = None
        if "assignees" in values:
            values.pop("assignees")
            links = self.assignees.normalize(
                data.assignees, fallback_primary=values.get("responsible_user_id")
            )
            values["responsible_user_id"] = self.assignees.primary_of(links)

        project = await self.repo.update(project, **values)
        if links is not None:
            await self.assignees.replace(project.id, links)
        elif "responsible_user_id" in values:
            await self.assignees.set_primary(project.id, values["responsible_user_id"])
        project.assignees = await self.assignees.for_entity(project.id)
        return project

    async def delete(self, project_id: uuid.UUID) -> None:
        self.ctx.ensure_can_write()
        project = await self.repo.get_or_404(project_id)
        await self.repo.delete(project)
