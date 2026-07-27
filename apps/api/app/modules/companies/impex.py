"""CSV import/export shape for companies (issue #77).

Core owns the mechanics (``app/core/impex``); this file only describes the shape and adapts
the coerced values to this module's own service — so an imported company goes through exactly
the same validation, custom-fields check and events as one created from the form. Upsert
matches on ``name`` (the natural key a spreadsheet actually carries); the tenant's custom
fields are appended by core at request time, never declared here.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.core.impex import ImpexColumn, ImpexDescriptor
from app.core.tenancy import RequestContext
from app.modules.companies.models import Company, CompanyStatus
from app.modules.companies.schemas import CompanyCreate, CompanyUpdate
from app.modules.companies.service import CompanyService

# Billing identity (issue #11) — a client list from a bookkeeping package carries these.
# Spelled out rather than sliced off ``_TEXT_FIELDS``: a positional slice makes inserting a
# column at the front silently shift every billing value one field along on create, with no
# error anywhere. The duplication is the point.
_BILLING_FIELDS = (
    "vat_number", "coc_number", "address_line1", "house_number", "address_line2",
    "postal_code", "city", "country",
)
_TEXT_FIELDS = (
    "name", "client_number", "website", "phone", "invoice_email", "notes", *_BILLING_FIELDS,
)


async def _fetch_page(
    ctx: RequestContext, *, limit: int, offset: int, filters: dict[str, Any]
) -> Sequence[Any]:
    """The module's own list — same filters/sort as ``GET /companies``, never a fork of it."""
    items, _ = await CompanyService(ctx).list(
        limit=limit,
        offset=offset,
        q=filters.get("q"),
        status=filters.get("status"),
        mine=bool(filters.get("mine")),
        sort=filters.get("sort"),
        count=False,
    )
    return items


async def _find_existing(
    ctx: RequestContext, key: str, values: list[str]
) -> dict[str, list[Any]]:
    """Match on whichever natural key the engine resolved this batch of rows to.

    ``client_number`` is org-unique at the database level, so its buckets hold at most one row
    and it can never come back ambiguous; ``name`` is not, which is exactly why the number is
    tried first.
    """
    column = Company.client_number if key == "client_number" else Company.name
    stmt = ctx.repo(Company).scoped_select().where(column.in_(values))
    found: dict[str, list[Any]] = {}
    for company in (await ctx.session.execute(stmt)).scalars():
        found.setdefault(getattr(company, key), []).append(company)
    return found


async def _create(ctx: RequestContext, values: dict[str, Any]) -> Any:
    return await CompanyService(ctx).create(
        CompanyCreate(
            name=values["name"],
            client_number=values.get("client_number"),
            website=values.get("website"),
            phone=values.get("phone"),
            invoice_email=values.get("invoice_email"),
            notes=values.get("notes"),
            status=CompanyStatus(values["status"])
            if values.get("status")
            else CompanyStatus.ACTIVE,
            custom=values.get("custom") or {},
            **{key: values.get(key) for key in _BILLING_FIELDS},
        )
    )


async def _update(ctx: RequestContext, company: Any, values: dict[str, Any]) -> None:
    # Only the columns present in the file are touched: an explicit ``None`` clears, an
    # absent key stays unset and the service leaves the field alone (``exclude_unset``).
    fields: dict[str, Any] = {key: values[key] for key in _TEXT_FIELDS if key in values}
    if values.get("status"):
        fields["status"] = CompanyStatus(values["status"])
    if "custom" in values:
        fields["custom"] = values["custom"]
    if fields:
        await CompanyService(ctx).update(company.id, CompanyUpdate(**fields))


COMPANY_IMPEX = ImpexDescriptor(
    entity_type="company",
    read_permission="companies.company.read",
    write_permission="companies.company.write",
    natural_keys=("client_number", "name"),
    filters=("q", "status", "mine", "sort"),
    columns=(
        ImpexColumn("name", required=True, aliases=("naam", "bedrijf", "bedrijfsnaam",
                                                    "company", "company name", "klant")),
        # Klantnummer — tried before ``name`` as the upsert key (a client can be renamed and
        # keep their number). Not clearable: an empty cell means "this file doesn't carry the
        # number", never "remove the number this client already has".
        ImpexColumn(
            "client_number",
            clearable=False,
            aliases=("klantnummer", "klantnr", "klantcode", "client number", "debiteurnummer"),
        ),
        ImpexColumn("website", aliases=("site", "url", "webadres")),
        # Stored E.164; a national number needs the org's default country (see app/core/phone).
        ImpexColumn("phone", aliases=("telefoon", "telefoonnummer", "tel", "telephone")),
        # Deliberately *not* aliased to a bare "e-mail"/"email": in a client list that column
        # is far more often the contact person's address than the billing one, and a wrong
        # suggestion the user accepts is worse than no suggestion at all. Verified in the
        # browser — a pasted list with "Contactpersoon" + "E-mail" landed the address here.
        ImpexColumn(
            "invoice_email",
            data_type="email",
            aliases=("factuur e-mail", "factuuradres", "invoice email", "facturatie e-mail"),
        ),
        # Not clearable: a company always has a status — an empty cell leaves it unchanged
        # (defaults to "active" on a create).
        ImpexColumn(
            "status",
            data_type="select",
            clearable=False,
            options=tuple(status.value for status in CompanyStatus),
        ),
        ImpexColumn("vat_number", aliases=("btw", "btw-nummer", "vat", "vat number")),
        ImpexColumn("coc_number", aliases=("kvk", "kvk-nummer", "kvknummer", "coc")),
        ImpexColumn("address_line1", aliases=("adres", "straat", "address", "street")),
        ImpexColumn("house_number", aliases=("huisnummer", "huisnr", "house number", "nr")),
        ImpexColumn("address_line2", aliases=("adres 2", "address 2", "toevoeging")),
        ImpexColumn("postal_code", aliases=("postcode", "zip", "postal code", "pc")),
        ImpexColumn("city", aliases=("plaats", "stad", "woonplaats", "town")),
        ImpexColumn("country", aliases=("land", "landcode")),
        ImpexColumn("notes", aliases=("notities", "opmerkingen", "toelichting")),
    ),
    fetch_page=_fetch_page,
    find_existing=_find_existing,
    create_row=_create,
    update_row=_update,
)
