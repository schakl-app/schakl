"""CSV import/export shape for websites (issue #77, CLAUDE.md §17).

A website has no name of its own — it *is* the site at an address — so the **address** is its
natural key: ``breik.dev/briellaerd``, ``www.klant.nl``, ``klant.nl``, exactly what the export
writes and every screen prints (:func:`app.core.webaddress.website_label`). A file that carries
only the ``domain`` column still matches on it, second in priority, **while the domain carries
one site** — the ordinary case, and the one every older export describes. A domain carrying
several is no longer an identity, so such a row is a create, and one whose address is already
taken is named in the preview (``_validate_row``) rather than matched to a site it did not name.

Three columns describe where the site lives — ``domain`` (the required reference), ``root`` and
``path`` — and ``address`` is their sum, so it is identity only: what it names is decided by
those three on a create, and moving a site is an edit of ``root``/``path`` (or, to another
domain, a delete plus a create; ``WebsiteUpdate`` has no ``domain_id``).

``company`` is the client the site belongs to, exported **resolved** (the override, else the
domain's) and imported as the override: a cell naming the domain's own client stores nothing,
because the service reads an override that restates the domain as *follow the domain* — which
is what makes a re-import of an export a no-op.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import column, func, select, table

from app.core.impex import ImpexColumn, ImpexDescriptor
from app.core.impex.party import party_tokens, resolve_party
from app.core.impex.resolvers import name_or_id_resolver
from app.core.tenancy import RequestContext
from app.core.webaddress import normalize_path, split_address, website_label
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
        hosting_id=filters.get("hosting_id"),
        uptime_enabled=filters.get("uptime_enabled"),
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
    """Match a website by its address, or by its parent domain's name.

    The cells arrive as the file wrote them and the buckets are keyed by those raw cells, which
    is the engine's contract; an address is read through :func:`split_address` so
    ``https://www.Klant.nl/shop/`` and ``www.klant.nl/shop`` find the same row. A domain the
    tenant doesn't hold simply matches nothing here and fails as an unresolved reference on the
    ``domain`` column instead.
    """
    if key == "address":
        wanted: dict[str, str] = {}
        for raw in values:
            try:
                apex, root, path = split_address(raw)
            except ValueError:
                continue
            if apex:
                wanted[raw] = website_label(apex, root, path)
        if not wanted:
            return {}
        apexes = {label.removeprefix("www.").split("/", 1)[0] for label in wanted.values()}
        stmt = (
            ctx.repo(Website)
            .scoped_select()
            .join(_domains, _domains.c.id == Website.domain_id)
            .where(_domains.c.org_id == ctx.org.id, func.lower(_domains.c.name).in_(apexes))
            .add_columns(_domains.c.name)
        )
        by_label: dict[str, list[Any]] = {}
        for website, name in await ctx.session.execute(stmt):
            by_label.setdefault(website_label(name, website.root, website.path), []).append(
                website
            )
        return {raw: by_label[label] for raw, label in wanted.items() if label in by_label}

    # A domain identifies a site only while it carries exactly one. A domain with several is
    # left out rather than reported as ambiguous: a row naming it is then a *create*, which is
    # what a hand-made sheet of new dev installs means, and one whose address already exists is
    # named by ``_validate_row`` — so the answer is "add the address column", never "which of
    # the three did you mean" on a row that meant none of them.
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
    return {name: rows for name, rows in found.items() if len(rows) == 1}


async def _validate_row(
    ctx: RequestContext, values: dict[str, Any], existing: Any | None
) -> Sequence[tuple[str, str]]:
    """One site per address, checked in the preview so the report names the row (#289).

    The same question the service asks before it writes (``_ensure_address_free``), asked here
    first: an import is all-or-nothing, and a 409 from ``create_row`` would fail the whole file
    as one request-level error naming nothing. For a create the address is the row's own three
    cells; for an update it is the matched site's, with the cells the file changes laid over.
    """
    domain_id = values.get("domain_id") if existing is None else existing.domain_id
    if domain_id is None:
        return []
    root = values.get("root", getattr(existing, "root", True) if existing else True)
    root = root is not False
    path = values.get("path", getattr(existing, "path", "") if existing else "")
    try:
        path = normalize_path(path)
    except ValueError:
        return [("path", "errors.invalid_website_path")]
    conditions = [
        Website.org_id == ctx.org.id,
        Website.domain_id == domain_id,
        Website.root.is_(root),
        Website.path == path,
    ]
    if existing is not None:
        conditions.append(Website.id != existing.id)
    taken = await ctx.session.scalar(select(Website.id).where(*conditions))
    return [("domain", "errors.website_exists")] if taken else []


async def _create(ctx: RequestContext, values: dict[str, Any]) -> Any:
    return await WebsiteService(ctx).create(
        WebsiteCreate(
            domain_id=values["domain_id"],
            root=values.get("root", True) is not False,
            path=values.get("path") or "",
            company_override_id=values.get("company_override_id"),
            hosting_id=values.get("hosting_id"),
            technical_owner=values.get("technical_owner"),
            uptime_enabled=bool(values.get("uptime_enabled")),
            custom=values.get("custom") or {},
        )
    )


async def _update(ctx: RequestContext, website: Any, values: dict[str, Any]) -> None:
    fields: dict[str, Any] = {}
    for key in (
        "root",
        "path",
        "company_override_id",
        "hosting_id",
        "technical_owner",
        "uptime_enabled",
        "custom",
    ):
        if key in values:
            fields[key] = values[key]
    if "path" in fields and fields["path"] is None:
        fields["path"] = ""  # an emptied cell is the root, never "leave alone" (clearable)
    if fields:
        await WebsiteService(ctx).update(website.id, WebsiteUpdate(**fields))


WEBSITE_IMPEX = ImpexDescriptor(
    entity_type="website",
    read_permission="websites.website.read",
    write_permission="websites.website.write",
    natural_keys=("address", "domain"),
    # Exactly what the list screen's filter bar can set, and nothing the screen cannot: an
    # export carries the filters the user is looking at, so the file is the list on screen,
    # whole (docs/UX.md). ``q`` searches the address — the parent domain's name and the path.
    filters=("q", "company_id", "hosting_id", "uptime_enabled", "sort"),
    columns=(
        # The row's identity (see the module docstring): host plus path, what every screen
        # prints. Matched on, never written — the three columns after it decide the address.
        ImpexColumn(
            "address",
            getter=lambda w: getattr(w, "label", None),
            aliases=("adres", "website", "site", "url", "webadres"),
        ),
        ImpexColumn(
            "domain",
            data_type="fk",
            field="domain_id",
            required=True,
            clearable=False,
            getter=lambda w: getattr(w, "domain_name", None),
            aliases=("domein", "domeinnaam", "domain name"),
        ),
        # True = the apex (``@``), False = ``www``. Not clearable: every site is one or the
        # other, and an empty cell means "the file doesn't carry this", not "neither".
        ImpexColumn(
            "root", data_type="bool", clearable=False, aliases=("apex", "hoofddomein")
        ),
        # The path under the host; an empty cell *is* the root, so it is clearable.
        ImpexColumn("path", aliases=("pad", "map", "submap", "directory")),
        # The client, resolved on the way out and stored as the override on the way in — a
        # cell naming the domain's own client stores nothing (``_resolve_override``). Clearable:
        # an emptied cell means "follow the domain again".
        ImpexColumn(
            "company",
            data_type="fk",
            field="company_override_id",
            getter=lambda w: getattr(w, "company_name", None),
            aliases=("klant", "bedrijf", "client"),
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
        "company": name_or_id_resolver("companies"),
        "hosting": name_or_id_resolver("hosting"),
        "technical_owner": resolve_party,
    },
    fetch_page=_fetch_page,
    find_existing=_find_existing,
    create_row=_create,
    update_row=_update,
    validate_row=_validate_row,
)
