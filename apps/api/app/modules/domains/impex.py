"""CSV import/export shape for domains and the TLD price list (issue #77, CLAUDE.md §17).

Two descriptors, because they are two different spreadsheets a tenant actually holds:

* ``domain`` — the register itself, upserted on ``name``. That name is the one truly stable
  handle a domain has (``uq_domains_org_name``), and it is normalised on the way in by the
  module's own ``DomainCreate``/``DomainUpdate`` validator, so "https://WWW.Example.NL/" in a
  pasted column matches the ``example.nl`` already on file instead of creating a second row.
* ``domain_tld_price`` — the per-TLD rate card, which is where a price change actually arrives
  from (a registrar's tariff sheet), and the one place bulk entry beats a form outright.

Everything the DNS worker fills in (nameservers, DNSSEC, MX, the last check) and everything the
service derives (``tld``, ``next_invoice_date``) is exported read-only: it is worth having in
the file, and writing it back would either be overwritten on the next run or quietly reschedule
an invoice cycle.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from app.core.impex import ImpexColumn, ImpexDescriptor
from app.core.impex.party import party_tokens, resolve_party
from app.core.impex.resolvers import (
    name_or_id_resolver,
    no_natural_key,
    provider_resolver,
)
from app.core.providers.models import ProviderKind
from app.core.tenancy import RequestContext
from app.modules.domains.models import Domain, DomainStatus
from app.modules.domains.schemas import (
    DomainCreate,
    DomainUpdate,
    TldPriceUpsert,
    normalize_domain_name,
)
from app.modules.domains.service import DomainService

#: Written straight through on an update when the file carries them; an absent key leaves the
#: field alone (``exclude_unset``), an empty cell clears it where the column is clearable.
_FIELDS = (
    "name",
    "company_id",
    "redirect_url",
    "start_date",
    "registrar_provider_id",
    "dns_provider_id",
    "email_provider_id",
    "registry_contact",
    "email_contact",
    # Three-state (#298): an empty cell is a real value here — it clears the decision back to
    # "follow the register", which is why the column is ``clearable``.
    "invoiceable",
)


def _party_getter(role: str):
    """Read the token :func:`_hydrate_parties` stashed for this role."""
    return lambda domain: getattr(domain, f"_impex_{role}", None)


async def _fetch_page(
    ctx: RequestContext, *, limit: int, offset: int, filters: dict[str, Any]
) -> Sequence[Any]:
    items, _ = await DomainService(ctx).list(
        limit=limit,
        offset=offset,
        q=filters.get("q"),
        company_id=filters.get("company_id"),
        sort=filters.get("sort"),
    )
    await _hydrate_parties(ctx, items)
    return items


async def _hydrate_parties(ctx: RequestContext, domains: Sequence[Any]) -> None:
    """Both party columns for a whole export page in one batch, never one lookup per row.

    ``list`` already resolved display *labels* onto each row; a label is not a token and does
    not re-import, so the export resolves its own (docs/PERFORMANCE.md — the point is that it
    costs a fixed handful of queries per page either way).
    """
    if not domains:
        return
    pairs = [
        (d.registry_contact_party_type, d.registry_contact_party_id, d.company_id)
        for d in domains
    ] + [
        (d.email_contact_party_type, d.email_contact_party_id, d.company_id) for d in domains
    ]
    tokens = await party_tokens(ctx, pairs)
    for i, domain in enumerate(domains):
        domain._impex_registry_contact = tokens[i]
        domain._impex_email_contact = tokens[len(domains) + i]


async def _find_existing(
    ctx: RequestContext, key: str, values: list[str]
) -> dict[str, list[Any]]:
    """Match on ``name``, which is org-unique — so every bucket holds at most one row.

    The cells are matched **normalised**, because the write normalises too: people paste
    "https://WWW.Example.NL/pagina" into a domain column, and ``DomainCreate`` reduces that to
    ``example.nl``. Matching the raw text would find nothing, decide the row is a create, and
    then hit the unique index on a name that was already there — a 409 that rolls the whole
    file back and blames a row the user can see nothing wrong with. Buckets stay keyed by the
    raw cell the engine handed over; only the lookup is normalised.
    """
    wanted = {value: str(normalize_domain_name(value)) for value in values}
    stmt = ctx.repo(Domain).scoped_select().where(Domain.name.in_(set(wanted.values())))
    by_name: dict[str, list[Any]] = {}
    for domain in (await ctx.session.execute(stmt)).scalars():
        by_name.setdefault(domain.name, []).append(domain)
    return {raw: by_name[name] for raw, name in wanted.items() if name in by_name}


def _payload(values: dict[str, Any]) -> dict[str, Any]:
    """The subset of ``values`` this module's schemas accept, keys absent when the file
    didn't carry them (so ``exclude_unset`` still means "leave it alone")."""
    return {key: values[key] for key in _FIELDS if key in values}


async def _create(ctx: RequestContext, values: dict[str, Any]) -> Any:
    data = _payload(values)
    return await DomainService(ctx).create(
        DomainCreate(
            **data,
            status=(
                DomainStatus(values["status"])
                if values.get("status")
                else DomainStatus.ACTIVE
            ),
            email_enabled=bool(values.get("email_enabled")),
            price_override=(
                Decimal(values["price_override"]) if values.get("price_override") else None
            ),
            custom=values.get("custom") or {},
        )
    )


async def _update(ctx: RequestContext, domain: Any, values: dict[str, Any]) -> None:
    fields = _payload(values)
    if values.get("status"):
        fields["status"] = DomainStatus(values["status"])
    if "email_enabled" in values and values["email_enabled"] is not None:
        fields["email_enabled"] = values["email_enabled"]
    if "price_override" in values:
        raw = values["price_override"]
        fields["price_override"] = Decimal(raw) if raw else None
    if "custom" in values:
        fields["custom"] = values["custom"]
    if fields:
        await DomainService(ctx).update(domain.id, DomainUpdate(**fields))


DOMAIN_IMPEX = ImpexDescriptor(
    entity_type="domain",
    read_permission="domains.domain.read",
    write_permission="domains.domain.write",
    natural_keys=("name",),
    # The domains list has no status filter of its own (see DomainService.list); an export
    # mirrors the list endpoint exactly rather than growing a filter the screen can't set.
    filters=("q", "company_id", "sort"),
    columns=(
        ImpexColumn(
            "name",
            required=True,
            clearable=False,
            aliases=("domein", "domeinnaam", "domain", "domain name", "url", "website"),
        ),
        ImpexColumn(
            "company",
            data_type="fk",
            field="company_id",
            required=True,
            clearable=False,
            getter=lambda d: getattr(d, "company_name", None),
            aliases=("klant", "bedrijf", "client", "company"),
        ),
        # Not clearable: a domain always has a status; an empty cell leaves it as it was.
        ImpexColumn(
            "status",
            data_type="select",
            clearable=False,
            options=tuple(status.value for status in DomainStatus),
            option_label_key="domains.status.{option}",
            aliases=("statuts", "state"),
        ),
        ImpexColumn(
            "redirect_url",
            aliases=("doorverwijzing", "redirect", "doorstuur url", "redirect url"),
        ),
        # Anchors the renewal cycle, so an empty cell must not wipe it — the service would then
        # have nothing to compute the next invoice date from.
        ImpexColumn(
            "start_date",
            data_type="date",
            clearable=False,
            aliases=("startdatum", "start date", "registratiedatum", "ingangsdatum"),
        ),
        ImpexColumn(
            "registrar_provider",
            data_type="fk",
            field="registrar_provider_id",
            getter=lambda d: getattr(d, "registrar_provider_name", None),
            aliases=("registrar", "registrar provider"),
        ),
        ImpexColumn(
            "dns_provider",
            data_type="fk",
            field="dns_provider_id",
            getter=lambda d: getattr(d, "dns_provider_name", None),
            aliases=("dns", "dns provider", "nameserver provider"),
        ),
        ImpexColumn(
            "registry_contact",
            data_type="party",
            getter=_party_getter("registry_contact"),
            aliases=("registrant", "houder", "registry contact"),
        ),
        ImpexColumn(
            "email_enabled",
            data_type="bool",
            clearable=False,
            aliases=("e-mail", "email", "mail actief", "email enabled"),
        ),
        ImpexColumn(
            "email_provider",
            data_type="fk",
            field="email_provider_id",
            getter=lambda d: getattr(d, "email_provider_name", None),
            aliases=("mailprovider", "email provider", "mail provider"),
        ),
        ImpexColumn(
            "email_contact",
            data_type="party",
            getter=_party_getter("email_contact"),
            aliases=("e-mail contact", "email contact", "mailcontact"),
        ),
        ImpexColumn(
            "price_override",
            data_type="number",
            aliases=("prijs", "afwijkende prijs", "price", "price override"),
        ),
        # The **stored decision**, not the resolved answer, so an export re-imports unchanged
        # (§17): an empty cell means "follow the register" and importing the effective answer
        # back would pin every domain to whatever the register happened to say that day.
        ImpexColumn(
            "invoiceable",
            data_type="bool",
            aliases=("factureren", "factureerbaar", "invoiceable", "billable", "invoice"),
        ),
        # --- derived / fetched: exported so the file is worth reading, never written back --- #
        ImpexColumn(
            "invoiceable_effective",
            readonly=True,
            getter=lambda d: getattr(d, "invoiceable_effective", None),
        ),
        ImpexColumn("tld", readonly=True),
        ImpexColumn("next_invoice_date", readonly=True),
        ImpexColumn(
            "resolved_price", readonly=True, getter=lambda d: getattr(d, "resolved_price", None)
        ),
        ImpexColumn(
            "nameservers", readonly=True, getter=lambda d: d.nameservers or None
        ),
        ImpexColumn("dnssec", readonly=True),
        ImpexColumn(
            "mx_records",
            readonly=True,
            getter=lambda d: [
                f"{r.get('priority')} {r.get('exchange')}" for r in (d.mx_records or [])
            ]
            or None,
        ),
        ImpexColumn("dns_checked_at", readonly=True),
    ),
    fk_resolvers={
        "company": name_or_id_resolver("companies"),
        "registrar_provider": provider_resolver(ProviderKind.REGISTRAR.value),
        "dns_provider": provider_resolver(ProviderKind.DNS.value),
        "email_provider": provider_resolver(ProviderKind.EMAIL.value),
        "registry_contact": resolve_party,
        "email_contact": resolve_party,
    },
    fetch_page=_fetch_page,
    find_existing=_find_existing,
    create_row=_create,
    update_row=_update,
)


# --- TLD price list (#250) ------------------------------------------------------------- #
async def _fetch_prices(
    ctx: RequestContext, *, limit: int, offset: int, filters: dict[str, Any]
) -> Sequence[Any]:
    """Every priced TLD's full history, flattened one row per price.

    ``tld_price_groups`` is the screen's shape (current / upcoming / history per TLD); a
    spreadsheet wants the rows. Paged in memory because the whole rate card is a few dozen
    rows by construction — one TLD times its handful of price changes — and the service reads
    it in a single query either way.
    """
    rows = [
        price
        for group in await DomainService(ctx).tld_price_groups()
        for price in sorted([*group.history, *group.upcoming], key=lambda p: p.valid_from)
    ]
    return rows[offset : offset + limit]


async def _create_price(ctx: RequestContext, values: dict[str, Any]) -> Any:
    return await DomainService(ctx).set_tld_price(
        TldPriceUpsert(
            tld=values["tld"],
            amount=Decimal(values["amount"]),
            valid_from=values.get("valid_from"),
        )
    )


async def _update_price(ctx: RequestContext, row: Any, values: dict[str, Any]) -> None:
    raise NotImplementedError  # unreachable: create-only (natural_keys=())


TLD_PRICE_IMPEX = ImpexDescriptor(
    entity_type="domain_tld_price",
    read_permission="domains.tld_price.read",
    write_permission="domains.tld_price.manage",
    # Create-only, and deliberately so: a price row is identified by ``(tld, valid_from)``
    # together and the engine matches on one column. It needs no second key, because
    # ``set_tld_price`` already *is* the upsert — a row for a date that exists is corrected in
    # place, any other date appends history. So an import of the same sheet twice is idempotent
    # even though every row reports as a create.
    natural_keys=(),
    filters=(),
    columns=(
        ImpexColumn(
            "tld", required=True, clearable=False, aliases=("extensie", "extension", "suffix")
        ),
        ImpexColumn(
            "amount",
            data_type="number",
            required=True,
            clearable=False,
            aliases=("prijs", "bedrag", "price", "tarief"),
        ),
        # Omitted ⇒ the org-local today, exactly as the form's date field does.
        ImpexColumn(
            "valid_from",
            data_type="date",
            clearable=False,
            aliases=("geldig vanaf", "ingangsdatum", "valid from", "vanaf"),
        ),
        # The currency is the org's, never per row (``set_tld_price`` stamps it).
        ImpexColumn("currency", readonly=True),
    ),
    fetch_page=_fetch_prices,
    find_existing=no_natural_key,
    create_row=_create_price,
    update_row=_update_price,
)
