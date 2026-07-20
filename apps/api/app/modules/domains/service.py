"""Business logic for domains — all DB access via the tenant-scoped repository (Golden Rule 1).

A domain references three cross-cutting things, each validated on write against *this tenant*:
its client company (a bare table reference, §6), catalog providers (:class:`ProviderService`, §89)
and responsible parties (:class:`PartyService`, §88). ``custom`` is validated against the tenant's
``domain`` custom-field definitions (§13). Reads batch-resolve company/provider names and party
labels so a list never N+1s (docs/PERFORMANCE.md).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import bindparam, func, select, text

from app.core.customfields import CustomFieldsService
from app.core.jobs import enqueue
from app.core.party import PartyService, PartyType
from app.core.party.schemas import PartyRef
from app.core.providers import ProviderService
from app.core.providers.models import Provider, ProviderKind
from app.core.sorting import apply_sort
from app.core.tenancy import RequestContext
from app.core.urls import reject_dangerous_url
from app.errors import AppError
from app.modules.domains.dns import fetch_dns
from app.modules.domains.models import Domain
from app.modules.domains.schemas import DomainCreate, DomainUpdate

logger = logging.getLogger("schakl.domains")

ENTITY_TYPE = "domain"

# Sort keys a client may pass; anything else is rejected (app/core/sorting.py).
SORTABLE = {
    "name": func.lower(Domain.name),
    "status": Domain.status,
    "created_at": Domain.created_at,
    "updated_at": Domain.updated_at,
}


class DomainService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = ctx.repo(Domain)
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
    ) -> tuple[Sequence[Domain], int]:
        conditions = []
        if company_id is not None:
            conditions.append(Domain.company_id == company_id)
        if q:
            conditions.append(Domain.name.ilike(f"%{q.strip()}%"))

        stmt = self.repo.scoped_select().where(*conditions)
        stmt = apply_sort(stmt, sort, SORTABLE, default=func.lower(Domain.name))
        stmt = stmt.limit(limit).offset(offset)
        items = list((await self.ctx.session.execute(stmt)).scalars().all())

        count_stmt = (
            select(func.count())
            .select_from(Domain)
            .where(Domain.org_id == self._org_id, *conditions)
        )
        total = int(await self.ctx.session.scalar(count_stmt) or 0)
        await self._attach(items)
        return items, total

    async def get(self, domain_id: uuid.UUID) -> Domain:
        domain = await self.repo.get_or_404(domain_id)
        await self._attach([domain])
        return domain

    async def domains_for_company(self, company_id: uuid.UUID) -> Sequence[Domain]:
        stmt = (
            self.repo.scoped_select()
            .where(Domain.company_id == company_id)
            .order_by(func.lower(Domain.name))
        )
        items = list((await self.ctx.session.execute(stmt)).scalars().all())
        await self._attach(items)
        return items

    # --- writes -------------------------------------------------------------- #
    async def create(self, data: DomainCreate) -> Domain:
        self.ctx.require("domains.domain.write")
        await self._ensure_company(data.company_id)
        await self._ensure_name_unique(data.name.strip())

        custom = await self.custom_fields.validate(ENTITY_TYPE, data.custom or {})
        registrar_id = await self.providers.ensure(
            data.registrar_provider_id, kind=ProviderKind.REGISTRAR
        )
        dns_id = await self.providers.ensure(data.dns_provider_id, kind=ProviderKind.DNS)
        rc_type, rc_id = await self.party.validate(
            data.registry_contact or PartyRef(type=PartyType.AGENCY)
        )

        email_provider_id: uuid.UUID | None = None
        ec_type: str | None = None
        ec_id: uuid.UUID | None = None
        if data.email_enabled:
            email_provider_id = await self.providers.ensure(
                data.email_provider_id, kind=ProviderKind.EMAIL
            )
            ec_type, ec_id = await self.party.validate(
                data.email_contact or PartyRef(type=PartyType.AGENCY)
            )

        domain = await self.repo.create(
            name=data.name.strip(),
            company_id=data.company_id,
            status=data.status.value,
            redirect_url=self._clean_redirect_url(data.redirect_url),
            registrar_provider_id=registrar_id,
            dns_provider_id=dns_id,
            registry_contact_party_type=rc_type,
            registry_contact_party_id=rc_id,
            email_enabled=data.email_enabled,
            email_provider_id=email_provider_id,
            email_contact_party_type=ec_type,
            email_contact_party_id=ec_id,
            custom=custom,
        )
        # First DNS fetch (#125): a one-off worker job, so create never waits on a resolver and
        # the DNS section fills in moments later instead of at the nightly cron. Deferred a few
        # seconds so the request's transaction has committed by the time the worker looks. The
        # job is a nicety — a queue failure must not fail the create it rides on.
        try:
            await enqueue(
                "refresh_domain_dns", str(self._org_id), str(domain.id), _defer_by=3
            )
        except Exception:
            logger.warning("could not enqueue first DNS fetch for domain %s", domain.id)
        await self._attach([domain])
        return domain

    async def update(self, domain_id: uuid.UUID, data: DomainUpdate) -> Domain:
        self.ctx.require("domains.domain.write")
        domain = await self.repo.get_or_404(domain_id)
        sent = data.model_dump(exclude_unset=True)
        values: dict[str, Any] = {}

        if "name" in sent:
            name = data.name.strip()
            await self._ensure_name_unique(name, exclude_id=domain.id)
            values["name"] = name
        if "company_id" in sent and data.company_id is not None:
            await self._ensure_company(data.company_id)
            values["company_id"] = data.company_id
        if "status" in sent and data.status is not None:
            values["status"] = data.status.value
        if "redirect_url" in sent:
            values["redirect_url"] = self._clean_redirect_url(data.redirect_url)
        if "registrar_provider_id" in sent:
            values["registrar_provider_id"] = await self.providers.ensure(
                data.registrar_provider_id, kind=ProviderKind.REGISTRAR
            )
        if "dns_provider_id" in sent:
            values["dns_provider_id"] = await self.providers.ensure(
                data.dns_provider_id, kind=ProviderKind.DNS
            )
        if "registry_contact" in sent:
            rc_type, rc_id = await self.party.validate(data.registry_contact)
            values["registry_contact_party_type"] = rc_type
            values["registry_contact_party_id"] = rc_id

        # Email: turning it off clears its provider + contact; leaving it on lets them be edited.
        email_enabled = data.email_enabled if "email_enabled" in sent else domain.email_enabled
        if "email_enabled" in sent:
            values["email_enabled"] = bool(data.email_enabled)
        if not email_enabled:
            if "email_enabled" in sent:
                values["email_provider_id"] = None
                values["email_contact_party_type"] = None
                values["email_contact_party_id"] = None
        else:
            if "email_provider_id" in sent:
                values["email_provider_id"] = await self.providers.ensure(
                    data.email_provider_id, kind=ProviderKind.EMAIL
                )
            if "email_contact" in sent:
                ec_type, ec_id = await self.party.validate(data.email_contact)
                values["email_contact_party_type"] = ec_type
                values["email_contact_party_id"] = ec_id

        if "custom" in sent:
            values["custom"] = await self.custom_fields.validate(
                ENTITY_TYPE, data.custom or {}
            )

        domain = await self.repo.update(domain, **values)
        await self._attach([domain])
        return domain

    async def delete(self, domain_id: uuid.UUID) -> None:
        self.ctx.require("domains.domain.delete")
        domain = await self.repo.get_or_404(domain_id)
        await self.repo.delete(domain)

    async def refresh_dns(self, domain_id: uuid.UUID) -> Domain:
        """Re-query public DNS now and store the result (#92). The write path, so gated on write.

        The network lookup runs *before* the DB write so a slow resolver doesn't hold the row's
        transaction open; ``fetch_dns`` never raises, so a failed lookup still stamps the attempt.
        """
        self.ctx.require("domains.domain.write")
        domain = await self.repo.get_or_404(domain_id)
        facts = await fetch_dns(domain.name)
        domain = await self.repo.update(
            domain,
            nameservers=facts.nameservers,
            dnssec=facts.dnssec,
            mx_records=facts.mx,
            dns_checked_at=datetime.now(UTC),
        )
        await self._attach([domain])
        return domain

    # --- internals ----------------------------------------------------------- #
    @staticmethod
    def _clean_redirect_url(value: str | None) -> str | None:
        """Strip, empty → NULL, store as typed; only refuse script-executing schemes (it's
        rendered as an ``href`` in the detail view)."""
        cleaned = (value or "").strip() or None
        return reject_dangerous_url(cleaned, field="redirect_url")

    async def _ensure_company(self, company_id: uuid.UUID) -> None:
        ok = await self.ctx.session.scalar(
            text("SELECT 1 FROM companies WHERE id = :cid AND org_id = :oid"),
            {"cid": company_id, "oid": self._org_id},
        )
        if not ok:
            raise AppError("not_found", "errors.not_found", status_code=404)

    async def _ensure_name_unique(
        self, name: str, *, exclude_id: uuid.UUID | None = None
    ) -> None:
        stmt = select(Domain.id).where(Domain.org_id == self._org_id, Domain.name == name)
        if exclude_id is not None:
            stmt = stmt.where(Domain.id != exclude_id)
        if await self.ctx.session.scalar(stmt):
            raise AppError(
                "conflict", "errors.conflict", status_code=409, fields={"name": "errors.conflict"}
            )

    async def _attach(self, domains: Sequence[Domain]) -> None:
        """Populate the read-only display fields (company/provider names, party labels) in batch."""
        if not domains:
            return
        company_names = await self._company_names({d.company_id for d in domains})

        provider_ids = {
            pid
            for d in domains
            for pid in (d.registrar_provider_id, d.dns_provider_id, d.email_provider_id)
            if pid is not None
        }
        provider_names = await self._provider_names(provider_ids)

        party_inputs = []
        for d in domains:
            party_inputs.append(
                (d.registry_contact_party_type, d.registry_contact_party_id, d.company_id)
            )
            party_inputs.append(
                (d.email_contact_party_type, d.email_contact_party_id, d.company_id)
            )
        resolved = await self.party.resolve_many(party_inputs)

        for i, d in enumerate(domains):
            d.company_name = company_names.get(d.company_id, "")  # type: ignore[attr-defined]
            d.registrar_provider_name = provider_names.get(d.registrar_provider_id)  # type: ignore[attr-defined]
            d.dns_provider_name = provider_names.get(d.dns_provider_id)  # type: ignore[attr-defined]
            d.email_provider_name = provider_names.get(d.email_provider_id)  # type: ignore[attr-defined]
            d.registry_contact = resolved[2 * i]  # type: ignore[attr-defined]
            d.email_contact = resolved[2 * i + 1]  # type: ignore[attr-defined]

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
