"""Business logic for websites — all DB access tenant-scoped (Golden Rule 1).

A website is a site at an **address** under one of the tenant's domains: the apex or ``www``,
plus a path (``""`` for the root). A domain carries any number of them, one per address, so
recording the same address twice is a ``409`` naming the address. Its parent domain and its
optional hosting are validated as bare table references (§6); its technical owner is a party
(§88). Whose it is — the domain's client, or the client the site names for itself — is resolved
by :func:`app.core.webaddress.website_company_expr`'s rule in one place here and read by every
screen. ``custom`` is validated against the ``website`` custom-field definitions (§13).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import and_, bindparam, case, func, null, or_, select, text
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.sql.expression import column as sa_column
from sqlalchemy.sql.expression import table as sa_table

from app.core.activity import ActivityService
from app.core.activity.service import snapshot
from app.core.customfields import CustomFieldsService
from app.core.monitoring import website_statuses
from app.core.party import PartyService
from app.core.sorting import SortPart, apply_sort, composite
from app.core.tenancy import RequestContext
from app.core.webaddress import website_host, website_label, website_label_expr, website_url
from app.errors import AppError
from app.modules.websites.models import Website
from app.modules.websites.schemas import AvailableDomain, WebsiteCreate, WebsiteUpdate

ENTITY_TYPE = "website"

#: The definition fields the activity trail tracks (§16) — the record's own columns, never the
#: resolved display names beside them (those are somebody else's row changing, not an edit here).
#:
#: The ``technical_owner_party_*`` pair is deliberately absent, following domains, which audits
#: neither of its own two party pairs: one logical field stored in two columns produces two trail
#: lines, and both print internals (``agency``, and a bare UUID) that the reader cannot resolve.
_AUDITED_FIELDS = (
    "root",
    "path",
    "company_override_id",
    "hosting_id",
    "uptime_enabled",
)

# The parent domain, its company and the hosting account, as bare tables (§6): sorting by
# them must not import another module's internals.
_domains = sa_table(
    "domains", sa_column("id"), sa_column("org_id"), sa_column("name"), sa_column("company_id")
)
_companies = sa_table("companies", sa_column("id"), sa_column("org_id"), sa_column("name"))
_hosting = sa_table("hosting", sa_column("id"), sa_column("org_id"), sa_column("name"))


def _domain_sort_name() -> Any:
    """Order by the parent domain's name — the label the row prints (docs/UX.md), never the FK.
    Correlated, so a row is never multiplied. The path breaks ties, so a domain's sites list
    root first and then by install."""
    return (
        select(func.lower(_domains.c.name))
        .where(_domains.c.org_id == Website.org_id, _domains.c.id == Website.domain_id)
        .scalar_subquery()
    )


def _company_sort_name() -> Any:
    """Order by the resolved client's name — the override where the site names one, else the
    parent domain's client (the :mod:`app.core.webaddress` rule, over this module's own model).

    One level of subquery, joined: a scalar subquery nested inside a scalar subquery does not
    correlate to the website two levels up, so the inner one read *every* domain's client and
    Postgres refused it as "more than one row returned by a subquery".
    """
    return (
        select(func.lower(_companies.c.name))
        .select_from(
            _domains.join(
                _companies,
                _companies.c.id
                == func.coalesce(Website.company_override_id, _domains.c.company_id),
            )
        )
        .where(
            _domains.c.org_id == Website.org_id,
            _domains.c.id == Website.domain_id,
            _companies.c.org_id == Website.org_id,
        )
        .scalar_subquery()
    )


def _hosting_sort_name() -> Any:
    """Order by the hosting account's name; a site with none sorts last (``NULLS LAST``)."""
    return (
        select(func.lower(_hosting.c.name))
        .where(_hosting.c.org_id == Website.org_id, _hosting.c.id == Website.hosting_id)
        .scalar_subquery()
    )


def _domain_exists(*conditions: Any) -> Any:
    """A predicate on the parent domain, correlated to the website row.

    The bridge every website filter that is really a *domain* filter crosses — the name a site
    answers on is the domain's. Correlated ``EXISTS`` over the same bare table the sorts use
    (§6): the join ``websites`` may not make by importing, and an ``EXISTS`` never multiplies
    the row the way a real join would. Org-scoped on both sides — RLS already is, but a bare
    table read states its own tenancy (Golden Rule 1).
    """
    return (
        select(_domains.c.id)
        .where(
            _domains.c.org_id == Website.org_id,
            _domains.c.id == Website.domain_id,
            *conditions,
        )
        .exists()
    )


def _company_matches(company_id: uuid.UUID) -> Any:
    """The rows whose **resolved** client is ``company_id``: an override that names it, or no
    override and a parent domain that belongs to it. Two predicates rather than a ``COALESCE``
    in the WHERE so the domain half stays the same correlated ``EXISTS`` the search uses."""
    return or_(
        Website.company_override_id == company_id,
        and_(
            Website.company_override_id.is_(None),
            _domain_exists(_domains.c.company_id == company_id),
        ),
    )


def _name_matches(needle: str) -> Any:
    """A website's searchable text is its address: the parent domain's name and its own path —
    ``briellaerd`` must find ``breik.dev/briellaerd``, whose domain says nothing about it."""
    return or_(
        _domain_exists(_domains.c.name.ilike(f"%{needle}%")),
        Website.path.ilike(f"%{needle}%"),
    )


# Sort keys a client may pass; anything else is rejected (app/core/sorting.py).
SORTABLE = {
    # The domain's name, then the apex before ``www.``, then the path: a domain's sites list
    # in address order — ``breik.dev``, ``breik.dev/briellaerd``, ``www.breik.dev/nova``.
    "name": composite(_domain_sort_name(), SortPart(Website.root, invert=True), Website.path),
    "company": _company_sort_name(),
    "hosting": _hosting_sort_name(),
    "uptime": Website.uptime_enabled,
    "created_at": Website.created_at,
    "updated_at": Website.updated_at,
}


def _blank_display_fields(websites: Sequence[Website]) -> None:
    """The ``meta=false`` branch: what :meth:`WebsiteService._attach` resolves, left empty.

    Written out rather than left to Pydantic's field defaults — see the twin in
    ``domains/service.py`` for why. ``company_id`` is in here because it is the *resolved*
    client (override or domain's), and the address fields because they need the domain's name.
    """
    for w in websites:
        w.domain_name = ""  # type: ignore[attr-defined]
        w.host = ""  # type: ignore[attr-defined]
        w.label = ""  # type: ignore[attr-defined]
        w.url = ""  # type: ignore[attr-defined]
        w.hosting_name = None  # type: ignore[attr-defined]
        w.company_id = None  # type: ignore[attr-defined]
        w.company_name = None  # type: ignore[attr-defined]
        w.domain_company_id = None  # type: ignore[attr-defined]
        w.technical_owner = None  # type: ignore[attr-defined]
        w.uptime_status = None  # type: ignore[attr-defined]


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
        q: str | None = None,
        hosting_id: uuid.UUID | None = None,
        uptime_enabled: bool | None = None,
        sort: str | None = None,
        count: bool = True,
        meta: bool = True,
    ) -> tuple[Sequence[Website], int]:
        conditions = []
        if domain_id is not None:
            conditions.append(Website.domain_id == domain_id)
        if q and q.strip():
            conditions.append(_name_matches(q.strip()))
        # The client narrows through the same bridge the search does. It used to
        # `SELECT id FROM domains WHERE company_id = …` into Python and pass the ids back as an
        # `IN` — an unbounded read whose cost grew with the client's register rather than with
        # the page (docs/PERFORMANCE.md), and one the count statement paid for a second time.
        if company_id is not None:
            conditions.append(_company_matches(company_id))
        if hosting_id is not None:
            conditions.append(Website.hosting_id == hosting_id)
        if uptime_enabled is not None:
            conditions.append(Website.uptime_enabled.is_(uptime_enabled))
        stmt = self.repo.scoped_select().where(*conditions)
        stmt = apply_sort(stmt, sort, SORTABLE, default=Website.created_at.desc())
        stmt = stmt.limit(limit).offset(offset)
        items = list((await self.ctx.session.execute(stmt)).scalars().all())
        if count:
            total = int(
                await self.ctx.session.scalar(
                    self.repo.scoped_count_select().where(*conditions)
                )
                or 0
            )
        else:
            total = len(items)
        if meta:
            await self._attach(items)
        else:
            _blank_display_fields(items)
        return items, total

    async def get(self, website_id: uuid.UUID) -> Website:
        website = await self.repo.get_or_404(website_id)
        await self._attach([website])
        return website

    async def available_domains(self, *, limit: int) -> list[AvailableDomain]:
        """The domains a website may be created on — the create picker's whole vocabulary — each
        with the addresses already recorded on it.

        Every domain, not the unclaimed ones: a domain carrying a site is exactly where the next
        dev install goes, so a ``NOT EXISTS`` here would hide the one domain the form is opened
        for. What the picker needs instead is what is *taken*, so the constraint shows itself
        working (#305) before the save 409s. Still **one statement** whatever the register holds
        (``tests/test_perf_query_budgets.py``): the sites ride along as an aggregate over a left
        join, and the cap bounds the domains (docs/PERFORMANCE.md).

        Company horizon is applied here by hand, which is the rule for any read that leaves the
        repository's path (§15, failure mode 3): ``domains`` is a bare table to this module, so
        nothing else would have narrowed it, and ``domains.company_id`` is ``NOT NULL`` — there
        is no unattached domain to exempt.
        """
        conditions = [_domains.c.org_id == self._org_id]
        scope = self.ctx.company_scope
        if scope is not None:
            conditions.append(_domains.c.company_id.in_(scope))
        # A domain with no site joins NULL columns, and the label expression would still print a
        # host over them — so the label is only ever computed where a site row exists, and the
        # NULLs the empty domains produce are removed from the array.
        label = case(
            (Website.id.is_(None), null()),
            else_=website_label_expr(Website.root, Website.path, _domains.c.name),
        )
        taken = func.array_remove(
            func.array_agg(aggregate_order_by(label, Website.root.desc(), Website.path)),
            None,
        )
        stmt = (
            select(_domains.c.id, _domains.c.name, _domains.c.company_id, taken)
            .select_from(
                _domains.outerjoin(
                    Website,
                    and_(Website.domain_id == _domains.c.id, Website.org_id == _domains.c.org_id),
                )
            )
            .where(*conditions)
            .group_by(_domains.c.id, _domains.c.name, _domains.c.company_id)
            .order_by(func.lower(_domains.c.name))
            .limit(limit)
        )
        rows = (await self.ctx.session.execute(stmt)).all()
        return [
            AvailableDomain(id=row[0], name=row[1], company_id=row[2], taken=list(row[3] or []))
            for row in rows
        ]

    # --- writes -------------------------------------------------------------- #
    async def create(self, data: WebsiteCreate) -> Website:
        self.ctx.require("websites.website.write")
        domain_name, domain_company = await self._ensure_domain(data.domain_id)
        await self._ensure_address_free(data.domain_id, domain_name, data.root, data.path)
        override = await self._resolve_override(data.company_override_id, domain_company)

        custom = await self.custom_fields.validate(ENTITY_TYPE, data.custom or {})
        hosting_id = await self._ensure_hosting(data.hosting_id)
        owner_type, owner_id = await self.party.validate(data.technical_owner)

        website = await self.repo.create(
            domain_id=data.domain_id,
            root=data.root,
            path=data.path,
            company_override_id=override,
            technical_owner_party_type=owner_type,
            technical_owner_party_id=owner_id,
            hosting_id=hosting_id,
            uptime_enabled=data.uptime_enabled,
            custom=custom,
        )
        await ActivityService(self.ctx).record_created(ENTITY_TYPE, website.id)
        await self._attach([website])
        return website

    async def update(self, website_id: uuid.UUID, data: WebsiteUpdate) -> Website:
        self.ctx.require("websites.website.write")
        website = await self.repo.get_or_404(website_id)
        before = snapshot(website, _AUDITED_FIELDS)
        sent = data.model_dump(exclude_unset=True)
        values: dict[str, Any] = {}

        if "root" in sent and data.root is not None:
            values["root"] = data.root
        if "path" in sent and data.path is not None:
            values["path"] = data.path
        if "root" in values or "path" in values:
            # Moving a site to another address is an ordinary edit, and it meets the same rule
            # a create does: the address must not already name a site.
            domain_name, _ = await self._ensure_domain(website.domain_id)
            await self._ensure_address_free(
                website.domain_id,
                domain_name,
                values.get("root", website.root),
                values.get("path", website.path),
                exclude=website.id,
            )
        if "company_override_id" in sent:
            _, domain_company = await self._ensure_domain(website.domain_id)
            values["company_override_id"] = await self._resolve_override(
                data.company_override_id, domain_company
            )
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
        await ActivityService(self.ctx).record_update(
            ENTITY_TYPE, website.id, before, snapshot(website, _AUDITED_FIELDS)
        )
        await self._attach([website])
        return website

    async def delete(self, website_id: uuid.UUID) -> None:
        self.ctx.require("websites.website.delete")
        website = await self.repo.get_or_404(website_id)
        await self.repo.delete(website)

    # --- internals ----------------------------------------------------------- #
    async def _ensure_domain(self, domain_id: uuid.UUID) -> tuple[str, uuid.UUID]:
        """The parent domain exists in this tenant **and** inside the caller's horizon (#285);
        answers ``(name, company_id)`` because both callers need both.

        A website carries no ``company_id`` column of its own, so the repository's write guard
        has nothing to refuse: without the horizon half here, a membership scoped to one company
        group could create a website on an invisible client's domain — and then not see what it
        made.
        """
        rows = (
            await self.ctx.session.execute(
                text("SELECT name, company_id FROM domains WHERE id = :id AND org_id = :oid"),
                {"id": domain_id, "oid": self._org_id},
            )
        ).all()
        scope = self.ctx.company_scope
        if not rows or (scope is not None and rows[0][1] not in scope):
            raise AppError("not_found", "errors.not_found", status_code=404)
        return rows[0][0], rows[0][1]

    async def _ensure_address_free(
        self,
        domain_id: uuid.UUID,
        domain_name: str,
        root: bool,
        path: str,
        *,
        exclude: uuid.UUID | None = None,
    ) -> None:
        """One site per address — refused with the address named, so the 409 reads as a rule
        rather than as a broken control (#305). The unique constraint is the backstop for a
        race; this is the sentence."""
        conditions = [
            Website.org_id == self._org_id,
            Website.domain_id == domain_id,
            Website.root.is_(root),
            Website.path == path,
        ]
        if exclude is not None:
            conditions.append(Website.id != exclude)
        if await self.ctx.session.scalar(select(Website.id).where(*conditions)):
            raise AppError(
                "website_exists",
                "errors.website_exists",
                status_code=409,
                fields={"path": "errors.website_exists"},
                details={"label": website_label(domain_name, root, path)},
            )

    async def _resolve_override(
        self, company_id: uuid.UUID | None, domain_company: uuid.UUID
    ) -> uuid.UUID | None:
        """The client to store on the row: ``None`` for "follow the domain", which includes an
        override that merely restates the domain's client — a form re-posting the resolved
        value must not freeze it (the platform's ``NULL`` = *inherit* rule). A named client
        must exist here and sit inside the caller's horizon, refused as the read of it would
        be (§15's 404 rule), so a restricted membership cannot file a site onto a client it
        cannot see."""
        if company_id is None or company_id == domain_company:
            return None
        rows = (
            await self.ctx.session.execute(
                text("SELECT id FROM companies WHERE id = :id AND org_id = :oid"),
                {"id": company_id, "oid": self._org_id},
            )
        ).all()
        scope = self.ctx.company_scope
        if not rows or (scope is not None and company_id not in scope):
            raise AppError("not_found", "errors.not_found", status_code=404)
        return company_id

    async def _ensure_hosting(self, hosting_id: uuid.UUID | None) -> uuid.UUID | None:
        if hosting_id is None:
            return None
        # Hosting *may* be company-less (shared infra), which the horizon leaves visible — so
        # the check mirrors the nullable-column rule rather than demanding a company (#285).
        rows = (
            await self.ctx.session.execute(
                text("SELECT company_id FROM hosting WHERE id = :id AND org_id = :oid"),
                {"id": hosting_id, "oid": self._org_id},
            )
        ).all()
        scope = self.ctx.company_scope
        visible = bool(rows) and (
            scope is None or rows[0][0] is None or rows[0][0] in scope
        )
        if not visible:
            raise AppError("invalid_hosting", "errors.invalid_hosting", status_code=400)
        return hosting_id

    async def _attach(self, websites: Sequence[Website]) -> None:
        if not websites:
            return
        # The parent domain's name (for the address) and company (the client unless overridden,
        # and the party "own company" fallback).
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
        # One rule for whose the site is, applied once per row and read by everything below.
        company_of = {
            w.id: w.company_override_id or domain_company.get(w.domain_id) for w in websites
        }

        hosting_names = await self._hosting_names(
            {w.hosting_id for w in websites if w.hosting_id is not None}
        )
        company_names = await self._company_names(
            {cid for cid in company_of.values() if cid is not None}
        )
        resolved = await self.party.resolve_many(
            [
                (w.technical_owner_party_type, w.technical_owner_party_id, company_of[w.id])
                for w in websites
            ]
        )
        # One batched question to whichever module watches these sites — or to nobody, on an
        # instance without the uptime module, where an empty answer is the honest one (#356).
        statuses = await website_statuses(self.ctx, {w.id for w in websites})
        for i, w in enumerate(websites):
            name = domain_names.get(w.domain_id, "")
            w.uptime_status = statuses.get(w.id)  # type: ignore[attr-defined]
            w.domain_name = name  # type: ignore[attr-defined]
            w.host = website_host(name, w.root)  # type: ignore[attr-defined]
            w.label = website_label(name, w.root, w.path)  # type: ignore[attr-defined]
            w.url = website_url(name, w.root, w.path)  # type: ignore[attr-defined]
            w.hosting_name = hosting_names.get(w.hosting_id)  # type: ignore[attr-defined]
            w.company_id = company_of[w.id]  # type: ignore[attr-defined]
            w.company_name = company_names.get(company_of[w.id])  # type: ignore[attr-defined]
            w.domain_company_id = domain_company.get(w.domain_id)  # type: ignore[attr-defined]
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
