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
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import bindparam, func, select, text
from sqlalchemy.sql.expression import column as sa_column
from sqlalchemy.sql.expression import table as sa_table

from app.core.activity import ActivityService
from app.core.activity.service import snapshot
from app.core.customfields import CustomFieldsService
from app.core.jobs import enqueue
from app.core.models import OrgSettings
from app.core.party import PartyService, PartyType
from app.core.party.schemas import PartyRef
from app.core.providers import ProviderService
from app.core.providers.models import Provider, ProviderKind
from app.core.sorting import apply_sort
from app.core.tenancy import RequestContext
from app.core.timezone import org_zoneinfo
from app.core.urls import reject_dangerous_url
from app.errors import AppError
from app.modules.domains.dns import fetch_dns
from app.modules.domains.models import BILLABLE_STATUSES, Domain, DomainTldPrice
from app.modules.domains.schemas import (
    DomainCreate,
    DomainUpdate,
    TldPriceGroup,
    TldPriceIncreaseItem,
    TldPriceIncreaseRequest,
    TldPriceIncreaseResult,
    TldPriceRow,
    TldPriceUpsert,
    tld_of,
)

logger = logging.getLogger("schakl.domains")

ENTITY_TYPE = "domain"

#: The definition fields the activity trail tracks (§16) — never the DNS-fetched facts
#: (those are observations, not edits) and never the derived ``tld``/``next_invoice_date``.
_AUDITED_FIELDS = (
    "name",
    "company_id",
    "status",
    "redirect_url",
    "start_date",
    "price_override",
    "registrar_provider_id",
    "dns_provider_id",
    "email_enabled",
    "email_provider_id",
)


def add_months(day: date, months: int) -> date:
    """Calendar-safe month addition: 31 Jan + 1 month = 28/29 Feb, never a ValueError.
    (Subscriptions' helper, re-stated here — modules don't import each other's internals, §6.)"""
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    next_month_start = date(year + (month == 12), month % 12 + 1, 1)
    last_day = (next_month_start - date.resolution).day
    return date(year, month, min(day.day, last_day))


def first_future_anniversary(start: date, today: date) -> date:
    """The first yearly anniversary of ``start`` strictly after ``today`` — deriving the
    renewal date this way means onboarding an old domain never back-bills history (#250)."""
    nxt = add_months(start, 12)
    while nxt <= today:
        nxt = add_months(nxt, 12)
    return nxt


def _bumped(current: Decimal, data: TldPriceIncreaseRequest) -> Decimal:
    """#231's arithmetic: percent multiplies, amount adds, set overwrites; floored at zero
    (the preview shows the 0,00 rather than the API refusing the batch), rounded once."""
    if data.mode == "percent":
        new = current * (1 + data.value / 100)
    elif data.mode == "amount":
        new = current + data.value
    else:
        new = data.value
    return max(Decimal("0.00"), new.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

# The client company, as a bare table (§6): sorting by it must not import the companies module.
_companies = sa_table("companies", sa_column("id"), sa_column("org_id"), sa_column("name"))


def _company_sort_name() -> Any:
    """Order by the client's name — the label the cell prints, never the FK (docs/UX.md).
    Correlated, so a row is never multiplied."""
    return (
        select(func.lower(_companies.c.name))
        .where(
            _companies.c.org_id == Domain.org_id,
            _companies.c.id == Domain.company_id,
        )
        .scalar_subquery()
    )


def _provider_sort_name(provider_id: Any) -> Any:
    """Order by the provider's name; a domain with none sorts last (``NULLS LAST``)."""
    return (
        select(func.lower(Provider.name))
        .where(Provider.org_id == Domain.org_id, Provider.id == provider_id)
        .scalar_subquery()
    )


# Sort keys a client may pass; anything else is rejected (app/core/sorting.py).
SORTABLE = {
    "name": func.lower(Domain.name),
    "company": _company_sort_name(),
    "status": Domain.status,
    "registrar": _provider_sort_name(Domain.registrar_provider_id),
    "dns": _provider_sort_name(Domain.dns_provider_id),
    "dnssec": Domain.dnssec,
    "email_enabled": Domain.email_enabled,
    "start_date": Domain.start_date,
    "next_invoice_date": Domain.next_invoice_date,
    "created_at": Domain.created_at,
    "updated_at": Domain.updated_at,
}


class DomainService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = ctx.repo(Domain)
        self.tld_prices = ctx.repo(DomainTldPrice)
        self.custom_fields = CustomFieldsService(ctx)
        self.providers = ProviderService(ctx)
        self.party = PartyService(ctx)

    @property
    def _org_id(self) -> uuid.UUID:
        return self.ctx.org.id

    async def _org_today(self) -> date:
        """Today in the org's zone (CLAUDE.md §8) — a renewal date is a local concept."""
        return datetime.now(await org_zoneinfo(self.ctx.session, self._org_id)).date()

    async def _org_currency(self) -> str:
        """The org's money currency (#124) — what new TLD price rows are written in."""
        currency = await self.ctx.session.scalar(
            select(OrgSettings.currency).where(OrgSettings.org_id == self._org_id)
        )
        return currency or "EUR"

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

        count_stmt = self.repo.scoped_count_select().where(*conditions)
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

        name = data.name.strip()
        today = await self._org_today()
        start_date = data.start_date or today
        # The renewal anchors on start_date; the first cycle still ahead, so a domain
        # registered years ago starts billing at its next anniversary, never its history.
        next_invoice_date = (
            first_future_anniversary(start_date, today)
            if data.status.value in BILLABLE_STATUSES
            else None
        )
        domain = await self.repo.create(
            name=name,
            company_id=data.company_id,
            status=data.status.value,
            redirect_url=self._clean_redirect_url(data.redirect_url),
            start_date=start_date,
            tld=tld_of(name),
            price_override=data.price_override,
            next_invoice_date=next_invoice_date,
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
        await ActivityService(self.ctx).record_created(ENTITY_TYPE, domain.id)
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
        before = snapshot(domain, _AUDITED_FIELDS)
        sent = data.model_dump(exclude_unset=True)
        values: dict[str, Any] = {}

        if "name" in sent:
            name = data.name.strip()
            await self._ensure_name_unique(name, exclude_id=domain.id)
            values["name"] = name
            values["tld"] = tld_of(name)  # stamped, never reparsed on read (#250)
        if "company_id" in sent and data.company_id is not None:
            await self._ensure_company(data.company_id)
            values["company_id"] = data.company_id
        if "status" in sent and data.status is not None:
            values["status"] = data.status.value
        if "redirect_url" in sent:
            values["redirect_url"] = self._clean_redirect_url(data.redirect_url)
        if "start_date" in sent and data.start_date is not None:
            values["start_date"] = data.start_date
        if "price_override" in sent:
            values["price_override"] = data.price_override
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
        # A domain whose renewal was never scheduled (pre-#250 row in a dead status, or one
        # created expired) gets its date the moment it becomes billable. An already-set date
        # is never touched: rescheduling a cycle invoices may exist for is not an edit's job.
        if domain.next_invoice_date is None and domain.status in BILLABLE_STATUSES:
            domain = await self.repo.update(
                domain,
                next_invoice_date=first_future_anniversary(
                    domain.start_date, await self._org_today()
                ),
            )
        await ActivityService(self.ctx).record_update(
            ENTITY_TYPE, domain.id, before, snapshot(domain, _AUDITED_FIELDS)
        )
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

    # --- TLD price list (#250) ------------------------------------------------ #
    async def tld_price_groups(self) -> list[TldPriceGroup]:
        """The price list as the tab shows it: every TLD with history **plus** every TLD
        the org holds domains under — an unpriced TLD with twelve domains on it is exactly
        what the list exists to surface. Grouped around the org-local today."""
        today = await self._org_today()
        rows = (
            (
                await self.ctx.session.execute(
                    self.tld_prices.scoped_select().order_by(
                        DomainTldPrice.tld, DomainTldPrice.valid_from.desc()
                    )
                )
            )
            .scalars()
            .all()
        )
        count_rows = (
            await self.ctx.session.execute(
                select(Domain.tld, func.count())
                .where(Domain.org_id == self._org_id, Domain.tld.is_not(None))
                .group_by(Domain.tld)
            )
        ).all()
        counts = {row[0]: int(row[1]) for row in count_rows}

        groups: dict[str, TldPriceGroup] = {}
        for row in rows:
            group = groups.get(row.tld)
            if group is None:
                group = groups[row.tld] = TldPriceGroup(
                    tld=row.tld, domain_count=counts.get(row.tld, 0), currency=row.currency
                )
            price = TldPriceRow.model_validate(row)
            if row.valid_from > today:
                group.upcoming.append(price)
            else:
                if group.current is None:
                    group.current = price
                group.history.append(price)
        for group in groups.values():
            group.upcoming.reverse()  # rows arrive newest-first; scheduled reads soonest-first

        if unpriced := counts.keys() - groups.keys():
            org_currency = await self._org_currency()
            for tld in unpriced:
                groups[tld] = TldPriceGroup(
                    tld=tld, domain_count=counts[tld], currency=org_currency
                )
        return [groups[key] for key in sorted(groups)]

    async def set_tld_price(self, data: TldPriceUpsert) -> TldPriceRow:
        """Append one price row (a same-day row is corrected in place — the manual-edit
        semantics the whole pricing model shares); history is never rewritten."""
        self.ctx.require("domains.tld_price.manage")
        valid_from = data.valid_from or await self._org_today()
        existing = await self.ctx.session.scalar(
            self.tld_prices.scoped_select().where(
                DomainTldPrice.tld == data.tld,
                DomainTldPrice.valid_from == valid_from,
            )
        )
        if existing is not None:
            row = await self.tld_prices.update(existing, amount=data.amount)
        else:
            row = await self.tld_prices.create(
                tld=data.tld,
                amount=data.amount,
                currency=await self._org_currency(),
                valid_from=valid_from,
            )
        return TldPriceRow.model_validate(row)

    async def delete_tld_price(self, price_id: uuid.UUID) -> None:
        """Remove one history row — undoing a scheduled increase or a slip of the keyboard.
        Safe on issued paper by construction: an invoice snapshots at draft time (§16 of
        docs/INVOICING.md), it never live-joins this table."""
        self.ctx.require("domains.tld_price.manage")
        row = await self.tld_prices.get_or_404(price_id)
        await self.tld_prices.delete(row)

    async def tld_price_increase(
        self, data: TldPriceIncreaseRequest, *, apply: bool
    ) -> TldPriceIncreaseResult:
        """Compute (and with ``apply`` write) a price change over the TLD list — #231's
        preview-then-apply shape, verbatim. The base per TLD is the newest row *strictly
        before* ``valid_from`` (an on-date row is corrected in place, so a re-run with a
        fixed value replaces rather than compounds); a TLD with no price on the date is
        skipped, never given a base of nothing."""
        self.ctx.require("domains.tld_price.manage")
        conditions = [DomainTldPrice.valid_from <= data.valid_from]
        if data.tld is not None:
            # Tenant-scoped existence check: an unknown TLD is a 404, never an empty preview.
            known = await self.ctx.session.scalar(
                select(DomainTldPrice.id)
                .where(
                    DomainTldPrice.org_id == self._org_id,
                    DomainTldPrice.tld == data.tld,
                )
                .limit(1)
            )
            if known is None:
                raise AppError("not_found", "errors.not_found", status_code=404)
            conditions.append(DomainTldPrice.tld == data.tld)
        rows = (
            (
                await self.ctx.session.execute(
                    self.tld_prices.scoped_select()
                    .where(*conditions)
                    .order_by(DomainTldPrice.tld, DomainTldPrice.valid_from.desc())
                )
            )
            .scalars()
            .all()
        )

        base: dict[str, DomainTldPrice] = {}
        on_date: dict[str, DomainTldPrice] = {}
        for row in rows:
            if row.valid_from == data.valid_from:
                on_date[row.tld] = row
            else:
                base.setdefault(row.tld, row)
        for tld, row in on_date.items():
            base.setdefault(tld, row)

        # Impact per TLD: the billable domains that resolve to its list price (an override
        # doesn't move with the list, so it doesn't count).
        counts: dict[str, int] = {}
        if base:
            count_rows = (
                await self.ctx.session.execute(
                    select(Domain.tld, func.count())
                    .where(
                        Domain.org_id == self._org_id,
                        Domain.tld.in_(base.keys()),
                        Domain.status.in_(BILLABLE_STATUSES),
                        Domain.price_override.is_(None),
                    )
                    .group_by(Domain.tld)
                )
            ).all()
            counts = {row[0]: int(row[1]) for row in count_rows}

        items: list[TldPriceIncreaseItem] = []
        for tld in sorted(base):
            current = base[tld].amount
            new = _bumped(current, data)
            items.append(
                TldPriceIncreaseItem(
                    tld=tld,
                    currency=base[tld].currency,
                    current_amount=current,
                    new_amount=new,
                    domain_count=counts.get(tld, 0),
                )
            )
            if not apply or new == current:
                continue
            existing = on_date.get(tld)
            if existing is not None:
                await self.tld_prices.update(existing, amount=new)
            else:
                await self.tld_prices.create(
                    tld=tld,
                    amount=new,
                    currency=base[tld].currency,
                    valid_from=data.valid_from,
                )
        return TldPriceIncreaseResult(items=items)

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

        current_prices = await self._current_tld_prices({d.tld for d in domains if d.tld})
        # The org currency backs an override with no TLD row behind it; fetched lazily so a
        # list without one pays no extra query.
        org_currency: str | None = None

        for i, d in enumerate(domains):
            d.company_name = company_names.get(d.company_id, "")  # type: ignore[attr-defined]
            d.registrar_provider_name = provider_names.get(d.registrar_provider_id)  # type: ignore[attr-defined]
            d.dns_provider_name = provider_names.get(d.dns_provider_id)  # type: ignore[attr-defined]
            d.email_provider_name = provider_names.get(d.email_provider_id)  # type: ignore[attr-defined]
            d.registry_contact = resolved[2 * i]  # type: ignore[attr-defined]
            d.email_contact = resolved[2 * i + 1]  # type: ignore[attr-defined]
            price_row = current_prices.get(d.tld) if d.tld else None
            if d.price_override is not None:
                if price_row is None and org_currency is None:
                    org_currency = await self._org_currency()
                d.resolved_price = d.price_override  # type: ignore[attr-defined]
                d.resolved_currency = (  # type: ignore[attr-defined]
                    price_row.currency if price_row is not None else org_currency
                )
            elif price_row is not None:
                d.resolved_price = price_row.amount  # type: ignore[attr-defined]
                d.resolved_currency = price_row.currency  # type: ignore[attr-defined]
            else:
                d.resolved_price = None  # type: ignore[attr-defined]
                d.resolved_currency = None  # type: ignore[attr-defined]

    async def _current_tld_prices(self, tlds: set[str]) -> dict[str, DomainTldPrice]:
        """The price row in effect today per TLD, in one batch — newest ``valid_from`` not
        in the future wins (the ``SubscriptionPrice`` resolution rule)."""
        if not tlds:
            return {}
        today = await self._org_today()
        rows = (
            await self.ctx.session.execute(
                self.tld_prices.scoped_select()
                .where(
                    DomainTldPrice.tld.in_(tlds),
                    DomainTldPrice.valid_from <= today,
                )
                .order_by(DomainTldPrice.tld, DomainTldPrice.valid_from.desc())
            )
        ).scalars()
        current: dict[str, DomainTldPrice] = {}
        for row in rows:
            current.setdefault(row.tld, row)
        return current

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
