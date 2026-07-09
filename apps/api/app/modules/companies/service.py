"""Business logic for companies — all DB access via the tenant-scoped repository.

No raw, unscoped queries here (Golden Rule 1 / CLAUDE.md §6). Writes require a non-client role.

Several employees work a client: ``company_assignees`` holds them all, one starred as primary.
``companies.responsible_user_id`` mirrors that primary on every write and is dropped in a later
release (docs/WORKFLOW.md, expand/contract) — read ``primary_assignee()`` instead of the column.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, or_, select

from app.core.assignees import AssigneeService
from app.core.customfields import CustomFieldsService
from app.core.events import emit
from app.core.tenancy import RequestContext
from app.modules.companies.models import Company, CompanyAssignee
from app.modules.companies.schemas import CompanyCreate, CompanyUpdate

ENTITY_TYPE = "company"


class CompanyService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = ctx.repo(Company)
        self.custom_fields = CustomFieldsService(ctx)
        self.assignees = AssigneeService(ctx, CompanyAssignee, "company_id")

    async def _attach_assignees(self, companies: Sequence[Company]) -> None:
        """One extra query for the whole page, never one per row (docs/PERFORMANCE.md)."""
        if not companies:
            return
        grouped = await self.assignees.for_entities([c.id for c in companies])
        for company in companies:
            company.assignees = grouped.get(company.id, [])

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        q: str | None = None,
        mine: bool = False,
        count: bool = True,
    ) -> tuple[Sequence[Company], int]:
        conditions = []
        if q:
            pattern = f"%{q.strip()}%"
            conditions.append(or_(Company.name.ilike(pattern), Company.website.ilike(pattern)))
        if mine:
            # "My clients" matches *any* assignee, not just the primary.
            conditions.append(
                Company.id.in_(self.assignees.entity_ids_for_user(self.ctx.user.id))
            )

        stmt = (
            self.repo.scoped_select()
            .where(*conditions)
            # A search ranks by name; the plain list stays newest-first.
            .order_by(Company.name.asc() if q else Company.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        items = list((await self.ctx.session.execute(stmt)).scalars().all())
        # ``count=False`` skips the discarded COUNT(*) for name-only lookups (pickers,
        # dashboard grouping) — see docs/PERFORMANCE.md.
        total = (
            int(
                await self.ctx.session.scalar(
                    select(func.count())
                    .select_from(Company)
                    .where(Company.org_id == self.ctx.org.id, *conditions)
                )
                or 0
            )
            if count
            else len(items)
        )
        await self._attach_assignees(items)
        return items, total

    async def get(self, company_id: uuid.UUID) -> Company:
        company = await self.repo.get_or_404(company_id)
        await self._attach_assignees([company])
        return company

    async def primary_assignee(self, company_id: uuid.UUID) -> uuid.UUID | None:
        """Who owns this client. Published so other modules never import our models (§6)."""
        return await self.assignees.primary(company_id)

    async def create(self, data: CompanyCreate) -> Company:
        self.ctx.ensure_can_write()
        values = data.model_dump()
        values.pop("assignees", None)
        links = self.assignees.normalize(
            data.assignees, fallback_primary=data.responsible_user_id
        )
        values["responsible_user_id"] = self.assignees.primary_of(links)
        values["status"] = values["status"].value
        values["custom"] = await self.custom_fields.validate(
            ENTITY_TYPE, values.get("custom") or {}
        )
        company = await self.repo.create(**values)
        await self.assignees.replace(company.id, links)
        company.assignees = await self.assignees.for_entity(company.id)
        await emit(
            "company.created",
            self.ctx,
            {"company_id": company.id, "status": company.status},
        )
        return company

    async def update(self, company_id: uuid.UUID, data: CompanyUpdate) -> Company:
        self.ctx.ensure_can_write()
        company = await self.repo.get_or_404(company_id)
        previous_status = company.status
        values = data.model_dump(exclude_unset=True)
        if values.get("status") is not None:
            values["status"] = values["status"].value
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

        company = await self.repo.update(company, **values)
        if links is not None:
            await self.assignees.replace(company.id, links)
        elif "responsible_user_id" in values:
            await self.assignees.set_primary(company.id, values["responsible_user_id"])
        company.assignees = await self.assignees.for_entity(company.id)

        if company.status != previous_status:
            await emit(
                "company.status_changed",
                self.ctx,
                {
                    "company_id": company.id,
                    "status": company.status,
                    "previous_status": previous_status,
                },
            )
        return company

    async def delete(self, company_id: uuid.UUID) -> None:
        self.ctx.ensure_can_write()
        company = await self.repo.get_or_404(company_id)
        await self.repo.delete(company)
