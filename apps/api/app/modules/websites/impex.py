"""CSV import/export shape for websites (issue #77, CLAUDE.md §17).

A website has no name of its own — it *is* the site on a domain — so the domain is both its
required reference and its natural key. That is the one shape the engine needs told about
twice: ``domain`` appears in ``natural_keys`` and in ``fk_resolvers``, matching on the raw
name the file carries and resolving the id independently. Without it a re-import of an export
would hit ``uq_websites_domain`` on every row and roll the whole file back, which is the worst
possible answer to "I edited two cells and imported it again".

``domain`` is not clearable and cannot be changed on an existing row (``WebsiteUpdate`` has no
``domain_id``): moving a site to another domain is a delete plus a create, not a cell edit.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import column, table

from app.core.impex import ImpexColumn, ImpexDescriptor
from app.core.impex.party import party_tokens, resolve_party
from app.core.impex.resolvers import name_or_id_resolver
from app.core.tenancy import RequestContext
from app.modules.websites.models import Website
from app.modules.websites.schemas import WebsiteCreate, WebsiteUpdate
from app.modules.websites.service import WebsiteService

#: The parent domain as a bare table (§6) — matching a website by its domain name is a lookup,
#: not a data path into the domains module.
_domains = table("domains", column("id"), column("name"), column("org_id"))


async def _fetch_page(
    ctx: RequestContext, *, limit: int, offset: int, filters: dict[str, Any]
) -> Sequence[Any]:
    items, _ = await WebsiteService(ctx).list(
        limit=limit,
        offset=offset,
        company_id=filters.get("company_id"),
        q=filters.get("q"),
        sort=filters.get("sort"),
    )
    if items:
        tokens = await party_tokens(
            ctx,
            [
                (w.technical_owner_party_type, w.technical_owner_party_id, w.company_id)
                for w in items
            ],
        )
        for website, token in zip(items, tokens, strict=True):
            website._impex_technical_owner = token
    return items


async def _find_existing(
    ctx: RequestContext, key: str, values: list[str]
) -> dict[str, list[Any]]:
    """Match a website by its parent domain's name — org-unique, so one row per bucket.

    The cells arrive as the file wrote them; a domain the tenant doesn't hold simply matches
    nothing here and fails as an unresolved reference on the ``domain`` column instead.
    """
    stmt = (
        ctx.repo(Website)
        .scoped_select()
        .join(_domains, _domains.c.id == Website.domain_id)
        .where(_domains.c.org_id == ctx.org.id, _domains.c.name.in_(values))
        .add_columns(_domains.c.name)
    )
    found: dict[str, list[Any]] = {}
    for website, name in await ctx.session.execute(stmt):
        found.setdefault(name, []).append(website)
    return found


async def _create(ctx: RequestContext, values: dict[str, Any]) -> Any:
    return await WebsiteService(ctx).create(
        WebsiteCreate(
            domain_id=values["domain_id"],
            root=values.get("root", True) is not False,
            hosting_id=values.get("hosting_id"),
            technical_owner=values.get("technical_owner"),
            uptime_enabled=bool(values.get("uptime_enabled")),
            custom=values.get("custom") or {},
        )
    )


async def _update(ctx: RequestContext, website: Any, values: dict[str, Any]) -> None:
    fields: dict[str, Any] = {}
    for key in ("root", "hosting_id", "technical_owner", "uptime_enabled", "custom"):
        if key in values:
            fields[key] = values[key]
    if fields:
        await WebsiteService(ctx).update(website.id, WebsiteUpdate(**fields))


WEBSITE_IMPEX = ImpexDescriptor(
    entity_type="website",
    read_permission="websites.website.read",
    write_permission="websites.website.write",
    natural_keys=("domain",),
    # Exactly what the list endpoint takes, and nothing the screen cannot set: an export
    # carries the filters the user is looking at, so ``q`` belongs here the moment the box
    # above the table does. It searches the parent domain's name — a website has none of its
    # own (``natural_keys``, above).
    filters=("company_id", "q", "sort"),
    columns=(
        ImpexColumn(
            "domain",
            data_type="fk",
            field="domain_id",
            required=True,
            clearable=False,
            getter=lambda w: getattr(w, "domain_name", None),
            aliases=("domein", "domeinnaam", "domain name", "site", "url"),
        ),
        # The client is the parent domain's, never set here — exported because a spreadsheet
        # of sites without their client is unreadable.
        ImpexColumn(
            "company",
            readonly=True,
            getter=lambda w: getattr(w, "company_name", None),
            aliases=("klant", "bedrijf", "client"),
        ),
        # True = the apex (``@``), False = ``www``. Not clearable: every site is one or the
        # other, and an empty cell means "the file doesn't carry this", not "neither".
        ImpexColumn(
            "root", data_type="bool", clearable=False, aliases=("apex", "hoofddomein")
        ),
        ImpexColumn(
            "hosting",
            data_type="fk",
            field="hosting_id",
            getter=lambda w: getattr(w, "hosting_name", None),
            aliases=("hostingaccount", "server", "hosting account"),
        ),
        ImpexColumn(
            "technical_owner",
            data_type="party",
            getter=lambda w: getattr(w, "_impex_technical_owner", None),
            aliases=("technisch beheer", "beheerder", "technical owner", "owner"),
        ),
        ImpexColumn(
            "uptime_enabled",
            data_type="bool",
            clearable=False,
            aliases=("uptime", "monitoring", "uptime monitoring"),
        ),
    ),
    fk_resolvers={
        "domain": name_or_id_resolver("domains"),
        "hosting": name_or_id_resolver("hosting"),
        "technical_owner": resolve_party,
    },
    fetch_page=_fetch_page,
    find_existing=_find_existing,
    create_row=_create,
    update_row=_update,
)
