"""CSV import/export shape for hosting accounts (issue #77, CLAUDE.md §17).

Upserted on ``name``, which is what a hosting overview actually carries — and which, unlike a
domain, is **not** unique per org at the database level. Two accounts called "Server 1" are
therefore an ambiguous match and become that row's error rather than a coin flip, the same
answer ``projects`` gives for the same reason.

``company_id`` is nullable here and that is meaningful: a hosting record with no client is
shared infrastructure, so its column is clearable and an empty cell really does detach it.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.core.impex import ImpexColumn, ImpexDescriptor
from app.core.impex.party import party_tokens, resolve_party
from app.core.impex.resolvers import name_or_id_resolver, provider_resolver
from app.core.providers.models import ProviderKind
from app.core.tenancy import RequestContext
from app.modules.hosting.models import Hosting
from app.modules.hosting.schemas import HostingCreate, HostingUpdate
from app.modules.hosting.service import HostingService

_FIELDS = ("name", "company_id", "provider_id", "ip_address", "contact")


async def _fetch_page(
    ctx: RequestContext, *, limit: int, offset: int, filters: dict[str, Any]
) -> Sequence[Any]:
    items, _ = await HostingService(ctx).list(
        limit=limit,
        offset=offset,
        q=filters.get("q"),
        company_id=filters.get("company_id"),
        sort=filters.get("sort"),
    )
    if items:
        tokens = await party_tokens(
            ctx, [(h.contact_party_type, h.contact_party_id, h.company_id) for h in items]
        )
        for hosting, token in zip(items, tokens, strict=True):
            hosting._impex_contact = token
    return items


async def _find_existing(
    ctx: RequestContext, key: str, values: list[str]
) -> dict[str, list[Any]]:
    stmt = ctx.repo(Hosting).scoped_select().where(Hosting.name.in_(values))
    found: dict[str, list[Any]] = {}
    for hosting in (await ctx.session.execute(stmt)).scalars():
        found.setdefault(hosting.name, []).append(hosting)
    return found


async def _create(ctx: RequestContext, values: dict[str, Any]) -> Any:
    return await HostingService(ctx).create(
        HostingCreate(
            **{key: values[key] for key in _FIELDS if key in values},
            custom=values.get("custom") or {},
        )
    )


async def _update(ctx: RequestContext, hosting: Any, values: dict[str, Any]) -> None:
    fields: dict[str, Any] = {key: values[key] for key in _FIELDS if key in values}
    if "custom" in values:
        fields["custom"] = values["custom"]
    if fields:
        await HostingService(ctx).update(hosting.id, HostingUpdate(**fields))


HOSTING_IMPEX = ImpexDescriptor(
    entity_type="hosting",
    read_permission="hosting.hosting.read",
    write_permission="hosting.hosting.write",
    natural_keys=("name",),
    filters=("q", "company_id", "sort"),
    columns=(
        ImpexColumn(
            "name",
            required=True,
            clearable=False,
            aliases=("naam", "account", "server", "hostingaccount"),
        ),
        # Clearable, unlike everywhere else: NULL here means *shared infrastructure*, a real
        # state rather than a missing one, so an emptied cell must actually detach the client.
        ImpexColumn(
            "company",
            data_type="fk",
            field="company_id",
            getter=lambda h: getattr(h, "company_name", None),
            aliases=("klant", "bedrijf", "client", "company"),
        ),
        ImpexColumn(
            "provider",
            data_type="fk",
            field="provider_id",
            getter=lambda h: getattr(h, "provider_name", None),
            aliases=("leverancier", "hoster", "provider", "partij"),
        ),
        ImpexColumn("ip_address", aliases=("ip", "ip-adres", "ip address")),
        ImpexColumn(
            "contact",
            data_type="party",
            getter=lambda h: getattr(h, "_impex_contact", None),
            aliases=("contactpersoon", "beheerder", "contact person"),
        ),
    ),
    fk_resolvers={
        "company": name_or_id_resolver("companies"),
        "provider": provider_resolver(ProviderKind.HOSTING.value),
        "contact": resolve_party,
    },
    fetch_page=_fetch_page,
    find_existing=_find_existing,
    create_row=_create,
    update_row=_update,
)
