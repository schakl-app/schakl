"""Business logic for companies — all DB access via the tenant-scoped repository.

No raw, unscoped queries here (Golden Rule 1 / CLAUDE.md §6). Writes require a non-client role.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from app.core.tenancy import RequestContext
from app.modules.companies.models import Company
from app.modules.companies.schemas import CompanyCreate, CompanyUpdate


class CompanyService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = ctx.repo(Company)

    async def list(self, *, limit: int, offset: int) -> tuple[Sequence[Company], int]:
        items = await self.repo.list(limit=limit, offset=offset)
        total = await self.repo.count()
        return items, total

    async def get(self, company_id: uuid.UUID) -> Company:
        return await self.repo.get_or_404(company_id)

    async def create(self, data: CompanyCreate) -> Company:
        self.ctx.ensure_can_write()
        return await self.repo.create(**data.model_dump())

    async def update(self, company_id: uuid.UUID, data: CompanyUpdate) -> Company:
        self.ctx.ensure_can_write()
        company = await self.repo.get_or_404(company_id)
        return await self.repo.update(company, **data.model_dump(exclude_unset=True))

    async def delete(self, company_id: uuid.UUID) -> None:
        self.ctx.ensure_can_write()
        company = await self.repo.get_or_404(company_id)
        await self.repo.delete(company)
