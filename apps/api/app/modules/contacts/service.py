"""Business logic for contacts — all DB access via the tenant-scoped repository.

Mirrors the companies reference: writes require a non-client role, and ``custom`` is validated
against the tenant's ``contact`` custom-field definitions on every write (CLAUDE.md §13).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from app.core.customfields import CustomFieldsService
from app.core.tenancy import RequestContext
from app.modules.contacts.models import Contact
from app.modules.contacts.schemas import ContactCreate, ContactUpdate

ENTITY_TYPE = "contact"


class ContactService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = ctx.repo(Contact)
        self.custom_fields = CustomFieldsService(ctx)

    async def list(
        self, *, limit: int, offset: int, company_id: uuid.UUID | None = None
    ) -> tuple[Sequence[Contact], int]:
        filters: dict = {}
        if company_id is not None:
            filters["company_id"] = company_id
        items = await self.repo.list(limit=limit, offset=offset, **filters)
        total = await self.repo.count(**filters)
        return items, total

    async def get(self, contact_id: uuid.UUID) -> Contact:
        return await self.repo.get_or_404(contact_id)

    async def create(self, data: ContactCreate) -> Contact:
        self.ctx.ensure_can_write()
        values = data.model_dump()
        values["custom"] = await self.custom_fields.validate(
            ENTITY_TYPE, values.get("custom") or {}
        )
        return await self.repo.create(**values)

    async def update(self, contact_id: uuid.UUID, data: ContactUpdate) -> Contact:
        self.ctx.ensure_can_write()
        contact = await self.repo.get_or_404(contact_id)
        values = data.model_dump(exclude_unset=True)
        if "custom" in values:
            values["custom"] = await self.custom_fields.validate(
                ENTITY_TYPE, values.get("custom") or {}
            )
        return await self.repo.update(contact, **values)

    async def delete(self, contact_id: uuid.UUID) -> None:
        self.ctx.ensure_can_write()
        contact = await self.repo.get_or_404(contact_id)
        await self.repo.delete(contact)
