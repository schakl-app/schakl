"""Business logic for companies — all DB access via the tenant-scoped repository.

No raw, unscoped queries here (Golden Rule 1 / CLAUDE.md §6). Writes require a non-client role.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from app.core.customfields import CustomFieldsService
from app.core.tenancy import RequestContext
from app.modules.companies.models import Company
from app.modules.companies.schemas import CompanyCreate, CompanyUpdate

ENTITY_TYPE = "company"


class CompanyService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = ctx.repo(Company)
        self.custom_fields = CustomFieldsService(ctx)

    async def list(self, *, limit: int, offset: int) -> tuple[Sequence[Company], int]:
        items = await self.repo.list(limit=limit, offset=offset)
        total = await self.repo.count()
        return items, total

    async def get(self, company_id: uuid.UUID) -> Company:
        return await self.repo.get_or_404(company_id)

    async def create(self, data: CompanyCreate) -> Company:
        self.ctx.ensure_can_write()
        values = data.model_dump()
        values["custom"] = await self.custom_fields.validate(
            ENTITY_TYPE, values.get("custom") or {}
        )
        return await self.repo.create(**values)

    async def update(self, company_id: uuid.UUID, data: CompanyUpdate) -> Company:
        self.ctx.ensure_can_write()
        company = await self.repo.get_or_404(company_id)
        values = data.model_dump(exclude_unset=True)
        if "custom" in values:
            values["custom"] = await self.custom_fields.validate(
                ENTITY_TYPE, values.get("custom") or {}
            )
        return await self.repo.update(company, **values)

    async def delete(self, company_id: uuid.UUID) -> None:
        self.ctx.ensure_can_write()
        company = await self.repo.get_or_404(company_id)
        await self.repo.delete(company)
