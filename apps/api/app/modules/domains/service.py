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
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import bindparam, cast, func, literal, null, select, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.sql.expression import column as sa_column
from sqlalchemy.sql.expression import table as sa_table

from app.core.activity import ActivityService
from app.core.activity.service import snapshot

# Billing-cycle calendar arithmetic lives in core (§6) rather than being re-stated per module
# that bills on one — a drift between two copies is a double bill or a missed one.
from app.core.billing import add_months, period_boundaries
from app.core.customfields import CustomFieldsService
from app.core.jobs import enqueue
from app.core.models import OrgSettings
from app.core.party import PartyService, PartyType
from app.core.party.schemas import PartyRef
from app.core.providers import ProviderService
from app.core.providers.models import Provider, ProviderKind
from app.core.registrar import register_expiry_expression, register_presences
from app.core.sorting import apply_sort
from app.core.tenancy import RequestContext
from app.core.timezone import org_zoneinfo
from app.core.urls import reject_dangerous_url
from app.errors import AppError
from app.modules.domains.dns import fetch_dns
from app.modules.domains.invoiceable import (
    invoiceable_condition,
    register_authority,
    source_of,
)
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

#: The definition fields the activity trail tracks (§16) — never the DNS-fetched facts (those
#: are observations, not edits) and never the derived ``tld``.
#:
#: ``next_invoice_date`` **is** tracked, and was not while it was purely derived: it is now
#: something a person sets, in the form, in a spreadsheet or over a selection, and "when does
#: this client's renewal go out" is precisely the kind of change an agency later needs to be
#: able to attribute. The cron's own yearly advance does not come through here (it writes the
#: column directly in ``jobs.py``), which is right — that is the cycle running, not an edit.
_AUDITED_FIELDS = (
    "name",
    "company_id",
    "status",
    "redirect_url",
    "start_date",
    "next_invoice_date",
    "price_override",
    "invoiceable",
    "auto_invoice_mode",
    "registrar_provider_id",
    "dns_provider_id",
    "email_enabled",
    "email_provider_id",
)


@dataclass(frozen=True)
class OpenPeriod:
    """One outstanding renewal period of one domain, priced at **its own** boundary."""

    period_start: date
    period_end: date
    amount: Decimal
    lines: tuple[tuple[str, Decimal, Decimal], ...]
    #: The period has not started yet — billing it renews in advance.
    future: bool


@dataclass(frozen=True)
class OpenRenewal:
    """A domain and every renewal period of it still outstanding (published, §6).

    `invoicing` builds its picker from this. Read-only: raising the invoice, and claiming the
    period so this module's cron skips it, belongs to whoever owns documents.
    """

    domain_id: uuid.UUID
    name: str
    currency: str
    amount: Decimal
    periods: tuple[OpenPeriod, ...]
    truncated: bool
    #: No renewal cycle is set, so no period can be named — and an unnameable period cannot
    #: be claimed. Reported so the picker can say why it is offering nothing.
    no_cycle: bool
    #: No price resolves (no override, and no TLD price valid at the boundary). Reported
    #: rather than offered at zero: a €0,00 renewal line is a silent invoicing error.
    no_price: bool
    #: This domain is not invoiced (#298) — the register says the agency does not hold its
    #: registration, or somebody said so. **Reported with its periods, never omitted**: the
    #: automation skips it, but "why is klant.nl not on the invoice" is exactly the question
    #: the picker exists to answer, and answering by omission is how the duplicate happens.
    invoiceable: bool = True
    #: Whose domain it is, and how far its own cron takes an invoice — the ``OpenAgreement``
    #: fields, for the same reason (#302): the org-wide backlog groups by client and has to
    #: separate what bills itself from what is waiting for a human.
    company_id: uuid.UUID | None = None
    company_name: str = ""
    auto_invoice_mode: str | None = None


def first_future_anniversary(start: date, today: date) -> date:
    """The first yearly anniversary of ``start`` strictly after ``today`` — deriving the
    renewal date this way means onboarding an old domain never back-bills history (#250)."""
    nxt = add_months(start, 12)
    while nxt <= today:
        nxt = add_months(nxt, 12)
    return nxt


class _NamedDomain:
    """A domain that does not exist yet, in the one shape the registrar seams correlate against.

    :func:`~app.core.registrar.expiry.register_expiry_expression` builds a clause over ``.id``
    and ``.name`` of a ``domains`` row, matching a register row by either — linked by a sync, or
    by name for a domain typed since the last one. On a **create** there is no row yet, and
    inserting first to re-resolve after would cost a second write on every domain anyone ever
    adds. So the clause is handed literals: the name we are about to store, and an id half that
    can never match.

    That last part is the whole reason this is a cast and not a bare ``null()``. SQLAlchemy
    renders ``column == null()`` as ``column IS NULL`` — which is a perfectly good predicate and
    the exact opposite of what is wanted here: an unmatched register row *has* a NULL
    ``domain_id``, so the id half would have matched **every registration the sync could not
    place**, and a new domain would inherit the expiry of an unrelated one. ``CAST(NULL AS
    UUID)`` compares as NULL instead, so the half never contributes and the name decides.

    Deliberately not a ``Domain`` instance: a half-built ORM object in a query builder is the
    kind of thing that later grows a flush nobody asked for.
    """

    def __init__(self, name: str) -> None:
        self.id = cast(null(), PGUUID(as_uuid=True))
        self.name = literal(name)


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


def _blank_display_fields(domains: Sequence[Domain]) -> None:
    """The ``meta=false`` branch: every field :meth:`DomainService._attach` would resolve, set to
    its empty value without asking the database anything.

    Written out rather than left to Pydantic's field defaults, because "the attribute is absent
    so the schema's default applies" is a coincidence that a later ``model_config`` change would
    quietly turn into a validation error — and because the billing pair has no empty value. Those
    two take the *local* answer, exactly as :meth:`~DomainService._attach_register_facts` does
    when no register module is enabled: what this row says about itself, with no register
    consulted and ``invoiceable_source`` therefore never claiming one was.
    """
    for d in domains:
        d.company_name = ""  # type: ignore[attr-defined]
        d.registrar_provider_name = None  # type: ignore[attr-defined]
        d.dns_provider_name = None  # type: ignore[attr-defined]
        d.email_provider_name = None  # type: ignore[attr-defined]
        d.registry_contact = None  # type: ignore[attr-defined]
        d.email_contact = None  # type: ignore[attr-defined]
        d.resolved_price = None  # type: ignore[attr-defined]
        d.resolved_currency = None  # type: ignore[attr-defined]
        d.invoiceable_effective = d.invoiceable is not False  # type: ignore[attr-defined]
        d.invoiceable_source = source_of(d.invoiceable, has_authority=False)  # type: ignore[attr-defined]
        d.registers = []  # type: ignore[attr-defined]
        d.register_expires_on = None  # type: ignore[attr-defined]


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

    async def _register_expiry_for(self, name: str) -> date | None:
        """What a connected register says this **name** expires on, or ``None``.

        By name rather than by row, because the two callers that need it — a create, and the
        default offered for a row whose date is being reset — are both asking about a domain the
        register may know better than we do. One query, and none at all when no register module
        is enabled (:func:`register_expiry_expression` answers ``None` before any SQL is built).
        """
        expression = register_expiry_expression(self._org_id, _NamedDomain(name))
        if expression is None:
            return None
        return await self.ctx.session.scalar(select(expression))

    async def _default_invoice_date(self, *, name: str, start_date: date) -> date:
        """The renewal date to use when nobody has named one (#250, extended).

        **The register wins where it has spoken.** A domain's renewal is invoiced when the
        registration actually lapses, and the derived anniversary is only ever a stand-in for
        that date: it is correct exactly when ``start_date`` is the true registration date, and
        off by however far it misses when it is not — which is the normal case for a portfolio
        onboarded in one afternoon, where every domain is anchored to that afternoon and every
        renewal then goes out on the wrong day.

        An expiry already in the past is ignored rather than used. A lapsed registration is a
        thing to look at, not a date to bill on: taking it would hand the cron a due date it
        fires on immediately, and draft a renewal invoice for a registration that has run out.
        """
        expires_on = await self._register_expiry_for(name)
        today = await self._org_today()
        if expires_on is not None and expires_on > today:
            return expires_on
        return first_future_anniversary(start_date, today)

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
        invoiceable: bool | None = None,
        status: str | None = None,
        registrar_provider_id: uuid.UUID | None = None,
        dns_provider_id: uuid.UUID | None = None,
        count: bool = True,
        meta: bool = True,
    ) -> tuple[Sequence[Domain], int]:
        conditions = []
        if company_id is not None:
            conditions.append(Domain.company_id == company_id)
        if q:
            conditions.append(Domain.name.ilike(f"%{q.strip()}%"))
        if status:
            conditions.append(Domain.status == status)
        # "Which of our names sit at this registrar / on this DNS" is the question a portfolio
        # move is planned from, and the answer has to be the whole register rather than the page
        # that happened to load — so it is a query parameter, never a filter in the browser
        # (docs/PERFORMANCE.md).
        if registrar_provider_id is not None:
            conditions.append(Domain.registrar_provider_id == registrar_provider_id)
        if dns_provider_id is not None:
            conditions.append(Domain.dns_provider_id == dns_provider_id)
        if invoiceable is not None:
            # Filters on the *resolved* answer, not the stored column: "show me what I am not
            # billing" must include the domains a register decided about, which are precisely
            # the ones nobody has typed anything into.
            clause = invoiceable_condition(self._org_id)
            conditions.append(clause if invoiceable else ~clause)

        stmt = self.repo.scoped_select().where(*conditions)
        stmt = apply_sort(stmt, sort, SORTABLE, default=func.lower(Domain.name))
        stmt = stmt.limit(limit).offset(offset)
        items = list((await self.ctx.session.execute(stmt)).scalars().all())

        if count:
            count_stmt = self.repo.scoped_count_select().where(*conditions)
            total = int(await self.ctx.session.scalar(count_stmt) or 0)
        else:
            total = len(items)
        if meta:
            await self._attach(items)
        else:
            _blank_display_fields(items)
        return items, total

    async def get(self, domain_id: uuid.UUID) -> Domain:
        domain = await self.repo.get_or_404(domain_id)
        await self._attach([domain])
        return domain

    async def open_renewals(self, company_id: uuid.UUID | None = None) -> list[OpenRenewal]:
        """Domains and **every renewal period of each still outstanding** (§6), for one client
        or the whole org.

        The published seam `invoicing` builds its picker from — the subscriptions
        ``open_agreements`` shape, one entity over, because a renewal prints in its own band
        on the document and a picker that omitted it would claim to show everything
        outstanding while hiding eleven lines of it.

        ``company_id=None`` is the org-wide read behind the backlog report (#302), and it is
        the same walk over more rows for the same reason ``open_agreements`` gives: two
        different rules would eventually disagree about what a client owes.

        Boundaries walk forward from ``start_date`` in years, floored at the domain's own
        ``created_at``: #250's rule that *onboarding an old domain never back-bills history*
        is exactly this floor, and without it a 2005 registration entered last week would be
        offered twenty renewals nobody agreed to. Each is priced the way the cron prices it —
        ``price_override``, else the TLD price valid **at that boundary** — and a domain no
        price resolves for is reported rather than offered at zero.
        """
        where = [Domain.status.in_(BILLABLE_STATUSES)]
        if company_id is not None:
            where.append(Domain.company_id == company_id)
        domains = list(
            await self.ctx.session.scalars(
                self.repo.scoped_select().where(*where).order_by(func.lower(Domain.name))
            )
        )
        if not domains:
            return []
        await self._attach_register_facts(domains)
        # One read for every client in play — see ``open_agreements`` (docs/PERFORMANCE.md).
        names: dict[uuid.UUID, str] = {}
        client_ids = {d.company_id for d in domains if d.company_id is not None}
        if client_ids:
            names = {
                row.id: row.name
                for row in (
                    await self.ctx.session.execute(
                        text(
                            "SELECT id, name FROM companies WHERE org_id = :oid AND id IN :ids"
                        ).bindparams(bindparam("ids", expanding=True)),
                        {"oid": self.ctx.org.id, "ids": list(client_ids)},
                    )
                ).mappings()
            }
        tlds = {d.tld for d in domains if d.tld}
        # The whole price history for the TLDs in play, in one read: resolving per domain per
        # year would be one query per renewal (docs/PERFORMANCE.md).
        history: dict[str, list[DomainTldPrice]] = {}
        if tlds:
            for row in await self.ctx.session.scalars(
                self.tld_prices.scoped_select()
                .where(DomainTldPrice.tld.in_(tlds))
                .order_by(DomainTldPrice.valid_from)
            ):
                history.setdefault(row.tld, []).append(row)
        org_currency = await self._org_currency()
        today = await self._org_today()

        out: list[OpenRenewal] = []
        for domain in domains:
            rows = history.get(domain.tld or "", [])

            def priced(
                day: date,
                rows: list[DomainTldPrice] = rows,
                override: Decimal | None = domain.price_override,
            ) -> tuple[Decimal, str] | None:
                """The price the cron would use at ``day``: the per-domain override, else the
                newest TLD price valid then. Rows arrive sorted, so the last match wins."""
                current = None
                for row in rows:
                    if row.valid_from <= day:
                        current = row
                    else:
                        break
                if override is not None:
                    return override, (
                        current.currency if current is not None else org_currency
                    )
                return (current.amount, current.currency) if current is not None else None

            boundaries, truncated = (
                period_boundaries(
                    start_date=domain.start_date,
                    anchor=domain.next_invoice_date,
                    months=12,
                    floor=domain.created_at.date(),
                    until=today,
                )
                if domain.next_invoice_date is not None
                else ([], False)
            )
            periods: list[OpenPeriod] = []
            unpriced = False
            for boundary in boundaries:
                resolved = priced(boundary)
                if resolved is None:
                    unpriced = True
                    continue
                amount, _currency = resolved
                periods.append(
                    OpenPeriod(
                        period_start=add_months(boundary, -12),
                        period_end=boundary,
                        amount=amount,
                        lines=((domain.name, Decimal(1), amount),),
                        future=boundary > today,
                    )
                )
            now_priced = priced(today)
            out.append(
                OpenRenewal(
                    domain_id=domain.id,
                    name=domain.name,
                    currency=now_priced[1] if now_priced else org_currency,
                    amount=now_priced[0] if now_priced else Decimal(0),
                    periods=tuple(periods),
                    truncated=truncated,
                    no_cycle=domain.next_invoice_date is None,
                    no_price=now_priced is None or unpriced,
                    invoiceable=bool(getattr(domain, "invoiceable_effective", True)),
                    company_id=domain.company_id,
                    company_name=names.get(domain.company_id, "") if domain.company_id else "",
                    auto_invoice_mode=domain.auto_invoice_mode,
                )
            )
        return out

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
        # What was sent wins; otherwise the register's expiry, else the anniversary of
        # start_date — the first cycle still ahead, so a domain registered years ago starts
        # billing at its next renewal and never back-bills its history.
        next_invoice_date: date | None = None
        if data.status.value in BILLABLE_STATUSES:
            next_invoice_date = data.next_invoice_date or await self._default_invoice_date(
                name=name, start_date=start_date
            )
        domain = await self.repo.create(
            name=name,
            company_id=data.company_id,
            status=data.status.value,
            redirect_url=self._clean_redirect_url(data.redirect_url),
            start_date=start_date,
            tld=tld_of(name),
            price_override=data.price_override,
            # NULL by default, and deliberately: at create time nobody knows yet whether the
            # agency holds this registration — the register does, and answers on every read.
            invoiceable=data.invoiceable,
            auto_invoice_mode=(
                data.auto_invoice_mode.value if data.auto_invoice_mode else None
            ),
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
        if "next_invoice_date" in sent:
            # Explicit null **resets to the default** rather than stopping the cycle. That is
            # not the `invoiceable` three-state pattern in disguise: "never invoice this domain"
            # already has a field of its own, and a blank billing date that quietly meant it
            # would be the same decision spelled two ways. Emptying a date whose whole job is to
            # be derived means "forget my number, work it out again" — so it is, from the
            # register if one has spoken and from the anniversary otherwise.
            # A domain that is not billable has no cycle to reset to, so a reset leaves it NULL
            # — exactly what a create in that status does, and what the block below re-seeds the
            # moment it becomes billable again.
            if data.next_invoice_date is not None:
                values["next_invoice_date"] = data.next_invoice_date
            elif values.get("status", domain.status) in BILLABLE_STATUSES:
                values["next_invoice_date"] = await self._default_invoice_date(
                    name=values.get("name", domain.name),
                    start_date=values.get("start_date", domain.start_date),
                )
            else:
                values["next_invoice_date"] = None
        if "price_override" in sent:
            values["price_override"] = data.price_override
        if "invoiceable" in sent:
            # Explicit null clears the decision back to "follow the register" (#298) — the same
            # three-state discipline `auto_invoice_mode` below uses.
            values["invoiceable"] = data.invoiceable
        if "auto_invoice_mode" in sent:
            # Explicit null clears the override back to "inherit the org default" — the
            # `exclude_unset` split is what keeps "absent" and "cleared" distinct.
            values["auto_invoice_mode"] = (
                data.auto_invoice_mode.value if data.auto_invoice_mode else None
            )
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
        # created expired) gets its date the moment it becomes billable — from the register if
        # one has spoken, else the anniversary. An already-set date is never touched:
        # rescheduling a cycle invoices may exist for is not an edit's job.
        if domain.next_invoice_date is None and domain.status in BILLABLE_STATUSES:
            domain = await self.repo.update(
                domain,
                next_invoice_date=await self._default_invoice_date(
                    name=domain.name, start_date=domain.start_date
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
        """Populate the read-only display fields (company/provider names, party labels) in batch.

        Six statements' worth of resolution, which is right for a screen that draws all of it and
        pure waste for a picker that draws a name. :func:`_blank_display_fields` is the other
        branch (``meta=false``).
        """
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

        await self._attach_register_facts(domains)
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

    async def _attach_register_facts(self, domains: Sequence[Domain]) -> None:
        """Everything the registers say about a whole page, in **one** query.

        Two facts, deliberately on the same statement rather than in two passes: #298's
        three-state billing answer, and the expiry a register last observed. Both correlate to
        the same ``domains`` rows through the same seams, so the second is another selected
        column and costs nothing (docs/PERFORMANCE.md) — the uncorrelated authority clause is
        evaluated once per statement regardless of page size, and the per-row clauses ride along.

        Skipped entirely when no register module is enabled: there is nothing to ask, and asking
        would be a query per page to learn what an empty tuple already said.
        """
        sources = register_presences()
        expiry = register_expiry_expression(self._org_id, Domain)
        if not sources and expiry is None:
            for d in domains:
                d.invoiceable_effective = d.invoiceable is not False  # type: ignore[attr-defined]
                d.invoiceable_source = source_of(d.invoiceable, has_authority=False)  # type: ignore[attr-defined]
                d.registers = []  # type: ignore[attr-defined]
                d.register_expires_on = None  # type: ignore[attr-defined]
            return
        org_id = self._org_id
        ids = [d.id for d in domains]
        extra = [
            source.holds(org_id, Domain).label(f"held_{source.key}") for source in sources
        ]
        if expiry is not None:
            extra.append(expiry.label("register_expires_on"))
        rows = (
            await self.ctx.session.execute(
                select(
                    Domain.id,
                    invoiceable_condition(org_id).label("effective"),
                    register_authority(org_id).label("authority"),
                    *extra,
                ).where(Domain.org_id == org_id, Domain.id.in_(ids))
            )
        ).all()
        resolved = {row.id: row for row in rows}
        for d in domains:
            row = resolved.get(d.id)
            d.invoiceable_effective = (  # type: ignore[attr-defined]
                bool(row.effective) if row is not None else d.invoiceable is not False
            )
            d.invoiceable_source = source_of(  # type: ignore[attr-defined]
                d.invoiceable, has_authority=bool(row.authority) if row is not None else False
            )
            d.registers = (  # type: ignore[attr-defined]
                [s.key for s in sources if getattr(row, f"held_{s.key}", False)]
                if row is not None
                else []
            )
            d.register_expires_on = (  # type: ignore[attr-defined]
                getattr(row, "register_expires_on", None) if row is not None else None
            )

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
