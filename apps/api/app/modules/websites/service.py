"""Business logic for websites — all DB access tenant-scoped (Golden Rule 1).

A website is a 0/1 child of a domain: creating a second one for the same domain is a ``409``.
Its parent domain and its optional hosting are validated as bare table references (§6); its
technical owner is a party (§88). When that party is *the company*, the "record's own company"
is the parent domain's company, so read resolution passes it through. ``custom`` is validated
against the ``website`` custom-field definitions (§13).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import bindparam, func, select, text

from app.core.customfields import CustomFieldsService
from app.core.party import PartyService
from app.core.tenancy import RequestContext
from app.errors import AppError
from app.modules.websites.models import Website
from app.modules.websites.schemas import WebsiteCreate, WebsiteUpdate

ENTITY_TYPE = "website"


class WebsiteService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = ctx.repo(Website)
        self.custom_fields = CustomFieldsService(ctx)
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
        domain_id: uuid.UUID | None = None,
        company_id: uuid.UUID | None = None,
    ) -> tuple[Sequence[Website], int]:
        conditions = []
        if domain_id is not None:
            conditions.append(Website.domain_id == domain_id)
        if company_id is not None:
            # A website's client is its parent domain's (§6 bare-table bridge, no import).
            company_domains = (
                await self.ctx.session.scalars(
                    text("SELECT id FROM domains WHERE org_id = :oid AND company_id = :cid"),
                    {"oid": self._org_id, "cid": company_id},
                )
            ).all()
            if not company_domains:
                return [], 0
            conditions.append(Website.domain_id.in_(company_domains))
        stmt = (
            self.repo.scoped_select()
            .where(*conditions)
            .order_by(Website.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        items = list((await self.ctx.session.execute(stmt)).scalars().all())
        total = int(
            await self.ctx.session.scalar(
                select(func.count())
                .select_from(Website)
                .where(Website.org_id == self._org_id, *conditions)
            )
            or 0
        )
        await self._attach(items)
        return items, total

    async def get(self, website_id: uuid.UUID) -> Website:
        website = await self.repo.get_or_404(website_id)
        await self._attach([website])
        return website

    async def for_domain(self, domain_id: uuid.UUID) -> Website | None:
        website = await self.ctx.session.scalar(
            self.repo.scoped_select().where(Website.domain_id == domain_id)
        )
        if website is not None:
            await self._attach([website])
        return website

    # --- writes -------------------------------------------------------------- #
    async def create(self, data: WebsiteCreate) -> Website:
        self.ctx.require("websites.website.write")
        await self._ensure_domain(data.domain_id)
        if await self.ctx.session.scalar(
            select(Website.id).where(
                Website.org_id == self._org_id, Website.domain_id == data.domain_id
            )
        ):
            raise AppError("conflict", "errors.conflict", status_code=409)

        custom = await self.custom_fields.validate(ENTITY_TYPE, data.custom or {})
        hosting_id = await self._ensure_hosting(data.hosting_id)
        owner_type, owner_id = await self.party.validate(data.technical_owner)

        website = await self.repo.create(
            domain_id=data.domain_id,
            root=data.root,
            technical_owner_party_type=owner_type,
            technical_owner_party_id=owner_id,
            hosting_id=hosting_id,
            uptime_enabled=data.uptime_enabled,
            custom=custom,
        )
        await self._attach([website])
        return website

    async def update(self, website_id: uuid.UUID, data: WebsiteUpdate) -> Website:
        self.ctx.require("websites.website.write")
        website = await self.repo.get_or_404(website_id)
        sent = data.model_dump(exclude_unset=True)
        values: dict[str, Any] = {}

        if "root" in sent and data.root is not None:
            values["root"] = data.root
        if "uptime_enabled" in sent and data.uptime_enabled is not None:
            values["uptime_enabled"] = data.uptime_enabled
        if "hosting_id" in sent:
            values["hosting_id"] = await self._ensure_hosting(data.hosting_id)
        if "technical_owner" in sent:
            owner_type, owner_id = await self.party.validate(data.technical_owner)
            values["technical_owner_party_type"] = owner_type
            values["technical_owner_party_id"] = owner_id
        if "custom" in sent:
            values["custom"] = await self.custom_fields.validate(ENTITY_TYPE, data.custom or {})

        website = await self.repo.update(website, **values)
        await self._attach([website])
        return website

    async def delete(self, website_id: uuid.UUID) -> None:
        self.ctx.require("websites.website.delete")
        website = await self.repo.get_or_404(website_id)
        await self.repo.delete(website)

    # --- internals ----------------------------------------------------------- #
    async def _ensure_domain(self, domain_id: uuid.UUID) -> None:
        ok = await self.ctx.session.scalar(
            text("SELECT 1 FROM domains WHERE id = :id AND org_id = :oid"),
            {"id": domain_id, "oid": self._org_id},
        )
        if not ok:
            raise AppError("not_found", "errors.not_found", status_code=404)

    async def _ensure_hosting(self, hosting_id: uuid.UUID | None) -> uuid.UUID | None:
        if hosting_id is None:
            return None
        ok = await self.ctx.session.scalar(
            text("SELECT 1 FROM hosting WHERE id = :id AND org_id = :oid"),
            {"id": hosting_id, "oid": self._org_id},
        )
        if not ok:
            raise AppError("invalid_hosting", "errors.invalid_hosting", status_code=400)
        return hosting_id

    async def _attach(self, websites: Sequence[Website]) -> None:
        if not websites:
            return
        # The parent domain's name (for display) and company (the party "own company" fallback).
        domain_ids = {w.domain_id for w in websites}
        domain_rows = (
            await self.ctx.session.execute(
                text(
                    "SELECT id, name, company_id FROM domains WHERE id IN :ids"
                ).bindparams(bindparam("ids", expanding=True)),
                {"ids": list(domain_ids)},
            )
        ).all()
        domain_names = {row[0]: row[1] for row in domain_rows}
        domain_company = {row[0]: row[2] for row in domain_rows}

        hosting_names = await self._hosting_names(
            {w.hosting_id for w in websites if w.hosting_id is not None}
        )
        company_names = await self._company_names(
            {cid for cid in domain_company.values() if cid is not None}
        )
        resolved = await self.party.resolve_many(
            [
                (
                    w.technical_owner_party_type,
                    w.technical_owner_party_id,
                    domain_company.get(w.domain_id),
                )
                for w in websites
            ]
        )
        for i, w in enumerate(websites):
            w.domain_name = domain_names.get(w.domain_id, "")  # type: ignore[attr-defined]
            w.hosting_name = hosting_names.get(w.hosting_id)  # type: ignore[attr-defined]
            w.company_id = domain_company.get(w.domain_id)  # type: ignore[attr-defined]
            w.company_name = company_names.get(domain_company.get(w.domain_id))  # type: ignore[attr-defined]
            w.technical_owner = resolved[i]  # type: ignore[attr-defined]

    async def _hosting_names(self, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
        if not ids:
            return {}
        stmt = text("SELECT id, name FROM hosting WHERE id IN :ids").bindparams(
            bindparam("ids", expanding=True)
        )
        rows = (await self.ctx.session.execute(stmt, {"ids": list(ids)})).all()
        return {row[0]: row[1] for row in rows}

    async def _company_names(self, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
        if not ids:
            return {}
        stmt = text("SELECT id, name FROM companies WHERE id IN :ids").bindparams(
            bindparam("ids", expanding=True)
        )
        rows = (await self.ctx.session.execute(stmt, {"ids": list(ids)})).all()
        return {row[0]: row[1] for row in rows}
