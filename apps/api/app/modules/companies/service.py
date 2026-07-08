"""Business logic for companies — all DB access via the tenant-scoped repository.

No raw, unscoped queries here (Golden Rule 1 / CLAUDE.md §6). Writes require a non-client role.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from app.core.customfields import CustomFieldsService
from app.core.events import emit
from app.core.tenancy import RequestContext
from app.modules.companies.models import Company
from app.modules.companies.schemas import CompanyCreate, CompanyUpdate

ENTITY_TYPE = "company"


class CompanyService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = ctx.repo(Company)
        self.custom_fields = CustomFieldsService(ctx)

    async def list(
        self, *, limit: int, offset: int, q: str | None = None, count: bool = True
    ) -> tuple[Sequence[Company], int]:
        # ``count=False`` skips the discarded COUNT(*) for name-only lookups (pickers,
        # dashboard grouping) — see docs/PERFORMANCE.md.
        if not q:
            items = await self.repo.list(limit=limit, offset=offset)
            total = await self.repo.count() if count else len(items)
            return items, total

        from sqlalchemy import func, or_, select

        pattern = f"%{q.strip()}%"
        condition = or_(Company.name.ilike(pattern), Company.website.ilike(pattern))
        stmt = (
            self.repo.scoped_select()
            .where(condition)
            .order_by(Company.name.asc())
            .limit(limit)
            .offset(offset)
        )
        items = (await self.ctx.session.execute(stmt)).scalars().all()
        total = (
            int(
                await self.ctx.session.scalar(
                    select(func.count())
                    .select_from(Company)
                    .where(Company.org_id == self.ctx.org.id, condition)
                )
                or 0
            )
            if count
            else len(items)
        )
        return items, total

    async def get(self, company_id: uuid.UUID) -> Company:
        return await self.repo.get_or_404(company_id)

    async def create(self, data: CompanyCreate) -> Company:
        self.ctx.ensure_can_write()
        values = data.model_dump()
        values["status"] = values["status"].value
        values["custom"] = await self.custom_fields.validate(
            ENTITY_TYPE, values.get("custom") or {}
        )
        company = await self.repo.create(**values)
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
        company = await self.repo.update(company, **values)
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
