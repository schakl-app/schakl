"""Provider-catalog business logic (issue #89) — all DB access tenant-scoped (Golden Rule 1).

Managing the catalog requires ``settings.providers.manage`` (enforced here on every write). The
:meth:`ProviderService.ensure` helper is what the domains and hosting services call to validate a
referenced ``provider_id`` belongs to this tenant and is of the expected kind — so a domain can
never be pointed at another tenant's provider, nor a registrar slot filled with an email host.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select

from app.core.providers.models import Provider, ProviderKind
from app.core.providers.schemas import ProviderCreate, ProviderUpdate
from app.core.tenancy import RequestContext
from app.errors import AppError


class ProviderService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = ctx.repo(Provider)

    @property
    def _org_id(self) -> uuid.UUID:
        return self.ctx.org.id

    # --- reads --------------------------------------------------------------- #
    async def list(
        self, *, kind: ProviderKind | None = None, include_inactive: bool = False
    ) -> Sequence[Provider]:
        stmt = self.repo.scoped_select()
        if kind is not None:
            stmt = stmt.where(Provider.kind == kind.value)
        if not include_inactive:
            stmt = stmt.where(Provider.active.is_(True))
        stmt = stmt.order_by(Provider.position, Provider.name)
        return list((await self.ctx.session.execute(stmt)).scalars().all())

    async def get(self, provider_id: uuid.UUID) -> Provider:
        return await self.repo.get_or_404(provider_id)

    async def ensure(
        self, provider_id: uuid.UUID | None, *, kind: ProviderKind
    ) -> uuid.UUID | None:
        """Validate a referenced provider is this tenant's and of ``kind``; return it unchanged.

        ``None`` passes through (the reference is optional). A wrong-kind or cross-tenant id is a
        ``400`` with the same envelope a bad party gets, so a form surfaces it per field.
        """
        if provider_id is None:
            return None
        row = await self.ctx.session.scalar(
            select(Provider.kind).where(
                Provider.org_id == self._org_id, Provider.id == provider_id
            )
        )
        if row is None or row != kind.value:
            raise AppError("invalid_provider", "errors.invalid_provider", status_code=400)
        return provider_id

    # --- writes -------------------------------------------------------------- #
    async def create(self, data: ProviderCreate) -> Provider:
        self.ctx.require("settings.providers.manage")
        return await self.repo.create(**data.model_dump(mode="json"))

    async def update(self, provider_id: uuid.UUID, data: ProviderUpdate) -> Provider:
        self.ctx.require("settings.providers.manage")
        provider = await self.repo.get_or_404(provider_id)
        return await self.repo.update(
            provider, **data.model_dump(mode="json", exclude_unset=True)
        )

    async def delete(self, provider_id: uuid.UUID) -> None:
        self.ctx.require("settings.providers.manage")
        provider = await self.repo.get_or_404(provider_id)
        await self.repo.delete(provider)
