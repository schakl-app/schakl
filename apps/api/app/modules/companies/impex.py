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

_TEXT_FIELDS = ("name", "website", "invoice_email", "notes")


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


async def _find_existing(ctx: RequestContext, values: list[str]) -> dict[str, list[Any]]:
    stmt = ctx.repo(Company).scoped_select().where(Company.name.in_(values))
    found: dict[str, list[Any]] = {}
    for company in (await ctx.session.execute(stmt)).scalars():
        found.setdefault(company.name, []).append(company)
    return found


async def _create(ctx: RequestContext, values: dict[str, Any]) -> None:
    await CompanyService(ctx).create(
        CompanyCreate(
            name=values["name"],
            website=values.get("website"),
            invoice_email=values.get("invoice_email"),
            notes=values.get("notes"),
            status=CompanyStatus(values["status"])
            if values.get("status")
            else CompanyStatus.ACTIVE,
            custom=values.get("custom") or {},
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
    natural_key="name",
    filters=("q", "status", "mine", "sort"),
    columns=(
        ImpexColumn("name", required=True),
        ImpexColumn("website"),
        ImpexColumn("invoice_email", data_type="email"),
        # Not clearable: a company always has a status — an empty cell leaves it unchanged
        # (defaults to "active" on a create).
        ImpexColumn(
            "status",
            data_type="select",
            clearable=False,
            options=tuple(status.value for status in CompanyStatus),
        ),
        ImpexColumn("notes"),
    ),
    fetch_page=_fetch_page,
    find_existing=_find_existing,
    create_row=_create,
    update_row=_update,
)
