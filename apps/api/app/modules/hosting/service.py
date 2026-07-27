"""Business logic for hosting — all DB access tenant-scoped (Golden Rule 1).

Validates its provider (kind ``hosting``, §89), its responsible party (§88), its optional company
(a bare table reference, §6) and ``custom`` (§13) on every write. Reads batch-resolve display
names and the party label so a list never N+1s (docs/PERFORMANCE.md).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import bindparam, func, select, text

from app.core.customfields import CustomFieldsService
from app.core.party import PartyService
from app.core.providers import ProviderService
from app.core.providers.models import Provider, ProviderKind
from app.core.sorting import apply_sort
from app.core.tenancy import RequestContext
from app.errors import AppError
from app.modules.hosting.models import Hosting
from app.modules.hosting.schemas import HostingCreate, HostingUpdate

ENTITY_TYPE = "hosting"

SORTABLE = {
    "name": func.lower(Hosting.name),
    "ip_address": Hosting.ip_address,
    "created_at": Hosting.created_at,
    "updated_at": Hosting.updated_at,
}


class HostingService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = ctx.repo(Hosting)
        self.custom_fields = CustomFieldsService(ctx)
        self.providers = ProviderService(ctx)
        self.party = PartyService(ctx)

    @property
    def _org_id(self) -> uuid.UUID:
        return self.ctx.org.id

    # --- reads --------------------------------------------------------------- #
    async def list(
        self,
        *,
        limit: int,
        offset: int,
        company_id: uuid.UUID | None = None,
        q: str | None = None,
        sort: str | None = None,
    ) -> tuple[Sequence[Hosting], int]:
        conditions = []
        if company_id is not None:
            conditions.append(Hosting.company_id == company_id)
        if q:
            conditions.append(Hosting.name.ilike(f"%{q.strip()}%"))

        stmt = self.repo.scoped_select().where(*conditions)
        stmt = apply_sort(stmt, sort, SORTABLE, default=func.lower(Hosting.name))
        stmt = stmt.limit(limit).offset(offset)
        items = list((await self.ctx.session.execute(stmt)).scalars().all())

        total = int(
            await self.ctx.session.scalar(
                self.repo.scoped_count_select().where(*conditions)
            )
            or 0
        )
        await self._attach(items)
        return items, total

    async def get(self, hosting_id: uuid.UUID) -> Hosting:
        hosting = await self.repo.get_or_404(hosting_id)
        await self._attach([hosting])
        return hosting

    async def hostings_for_company(self, company_id: uuid.UUID) -> Sequence[Hosting]:
        stmt = (
            self.repo.scoped_select()
            .where(Hosting.company_id == company_id)
            .order_by(func.lower(Hosting.name))
        )
        items = list((await self.ctx.session.execute(stmt)).scalars().all())
        await self._attach(items)
        return items

    # --- writes -------------------------------------------------------------- #
    async def create(self, data: HostingCreate) -> Hosting:
        self.ctx.require("hosting.hosting.write")
        if data.company_id is not None:
            await self._ensure_company(data.company_id)
        custom = await self.custom_fields.validate(ENTITY_TYPE, data.custom or {})
        provider_id = await self.providers.ensure(data.provider_id, kind=ProviderKind.HOSTING)
        contact_type, contact_id = await self.party.validate(data.contact)

        hosting = await self.repo.create(
            name=data.name.strip(),
            company_id=data.company_id,
            provider_id=provider_id,
            ip_address=(data.ip_address or None),
            contact_party_type=contact_type,
            contact_party_id=contact_id,
            custom=custom,
        )
        await self._attach([hosting])
        return hosting

    async def update(self, hosting_id: uuid.UUID, data: HostingUpdate) -> Hosting:
        self.ctx.require("hosting.hosting.write")
        hosting = await self.repo.get_or_404(hosting_id)
        sent = data.model_dump(exclude_unset=True)
        values: dict[str, Any] = {}

        if "name" in sent and data.name is not None:
            values["name"] = data.name.strip()
        if "company_id" in sent:
            if data.company_id is not None:
                await self._ensure_company(data.company_id)
            values["company_id"] = data.company_id
        if "provider_id" in sent:
            values["provider_id"] = await self.providers.ensure(
                data.provider_id, kind=ProviderKind.HOSTING
            )
        if "ip_address" in sent:
            values["ip_address"] = data.ip_address or None
        if "contact" in sent:
            contact_type, contact_id = await self.party.validate(data.contact)
            values["contact_party_type"] = contact_type
            values["contact_party_id"] = contact_id
        if "custom" in sent:
            values["custom"] = await self.custom_fields.validate(ENTITY_TYPE, data.custom or {})

        hosting = await self.repo.update(hosting, **values)
        await self._attach([hosting])
        return hosting

    async def delete(self, hosting_id: uuid.UUID) -> None:
        self.ctx.require("hosting.hosting.delete")
        hosting = await self.repo.get_or_404(hosting_id)
        await self.repo.delete(hosting)

    # --- internals ----------------------------------------------------------- #
    async def _ensure_company(self, company_id: uuid.UUID) -> None:
        ok = await self.ctx.session.scalar(
            text("SELECT 1 FROM companies WHERE id = :cid AND org_id = :oid"),
            {"cid": company_id, "oid": self._org_id},
        )
        if not ok:
            raise AppError("not_found", "errors.not_found", status_code=404)

    async def _attach(self, hostings: Sequence[Hosting]) -> None:
        if not hostings:
            return
        company_names = await self._company_names(
            {h.company_id for h in hostings if h.company_id is not None}
        )
        provider_names = await self._provider_names(
            {h.provider_id for h in hostings if h.provider_id is not None}
        )
        resolved = await self.party.resolve_many(
            [(h.contact_party_type, h.contact_party_id, h.company_id) for h in hostings]
        )
        for i, h in enumerate(hostings):
            h.company_name = company_names.get(h.company_id) if h.company_id else None  # type: ignore[attr-defined]
            h.provider_name = provider_names.get(h.provider_id)  # type: ignore[attr-defined]
            h.contact = resolved[i]  # type: ignore[attr-defined]

    async def _company_names(self, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
        if not ids:
            return {}
        stmt = text("SELECT id, name FROM companies WHERE id IN :ids").bindparams(
            bindparam("ids", expanding=True)
        )
        rows = (await self.ctx.session.execute(stmt, {"ids": list(ids)})).all()
        return {row[0]: row[1] for row in rows}

    async def _provider_names(self, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
        if not ids:
            return {}
        rows = (
            await self.ctx.session.execute(
                select(Provider.id, Provider.name).where(
                    Provider.org_id == self._org_id, Provider.id.in_(ids)
                )
            )
        ).all()
        return {row[0]: row[1] for row in rows}
