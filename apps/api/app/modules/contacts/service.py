"""Business logic for contacts — all DB access via the tenant-scoped repository.

A contact links to many companies through ``company_contacts`` (``CompanyContact``); each link
carries ``is_primary`` (the primary contact *for that company*). Writes require a non-client role,
and ``custom`` is validated against the tenant's ``contact`` custom-field definitions on every
write (CLAUDE.md §13).

Company rows are read via RLS-scoped raw SQL against the ``companies`` table (shared schema), not
by importing the companies module — modules never import each other's internals (CLAUDE.md §3).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import bindparam, column, func, or_, select, table, text, update

from app.core.activity import ActivityService
from app.core.activity.service import snapshot
from app.core.customfields import CustomFieldsService
from app.core.phone import normalize_phone
from app.core.region import org_default_country
from app.core.richtext import sanitize_markdown
from app.core.sorting import apply_sort
from app.core.tenancy import RequestContext, TenantScopedRepository
from app.errors import AppError
from app.modules.contacts.models import CompanyContact, Contact, ContactType
from app.modules.contacts.schemas import (
    ContactCompanyLink,
    ContactCreate,
    ContactTypeCreate,
    ContactTypeUpdate,
    ContactUpdate,
)

ENTITY_TYPE = "contact"

# Definition fields whose before/after values the activity trail records (issue #67); notes and
# custom are left out of the diff, as on every auditable entity.
_AUDITED_FIELDS = ("first_name", "last_name", "email", "phone", "job_title")


# ``companies`` belongs to another module. Reference it as a bare table by name rather than
# importing its model — the same FK-name convention ``time.revenue()`` uses to reach `projects`
# (CLAUDE.md §6: modules never import each other's internals). Public within this module because
# ``portal.py`` names clients too (#406), and a second copy of the same four strings is how the
# two come to disagree about which columns exist.
companies_table = table("companies", column("id"), column("name"), column("org_id"))


def _company_sort_name() -> Any:
    """Sort key for "client": the alphabetically first company this contact is linked to.

    Note what this *cannot* be. ``is_primary`` on ``company_contacts`` means "the primary contact
    **for that company**" — it is unique per company, not per contact — so the same person can be
    primary at three clients at once. "Their primary company" is not a thing that exists, and a
    subquery selecting it raises a cardinality violation the moment someone is. ``MIN`` picks the
    same client every time, which is what a sorted list needs.

    Correlated, not joined: a contact links to many companies and a join would multiply the row,
    changing which contacts land on the page. A contact linked to nobody yields NULL, filed last.
    """
    return (
        select(func.min(func.lower(companies_table.c.name)))
        .select_from(CompanyContact)
        .join(
            companies_table,
            (companies_table.c.id == CompanyContact.company_id)
            & (companies_table.c.org_id == CompanyContact.org_id),
        )
        .where(
            CompanyContact.contact_id == Contact.id,
            CompanyContact.org_id == Contact.org_id,
        )
        .correlate(Contact)
        .scalar_subquery()
    )


# Columns a client may sort by; anything else in ``?sort=`` is rejected (app/core/sorting.py).
# Names sort case-insensitively — Postgres' default collation files lowercase after uppercase.
SORTABLE = {
    "first_name": func.lower(Contact.first_name),
    "last_name": func.lower(Contact.last_name),
    "email": func.lower(Contact.email),
    "job_title": func.lower(Contact.job_title),
    "company": _company_sort_name(),
    "created_at": Contact.created_at,
    "updated_at": Contact.updated_at,
}


def _linked_in_scope(scope: frozenset[uuid.UUID] | None):  # noqa: ANN202 — SQLA condition
    """A contact is inside the horizon when a ``company_contacts`` link points at a company
    the membership may see — and *only* then. This is the client-login rule; restricted staff
    additionally keep unattached contacts (``Contact.__company_horizon_clause__``).

    Defined on the model (``Contact.__portal_horizon_clause__``) so the repository here and the
    cross-module reference seam give a client the same answer by construction, not by two
    predicates happening to agree.
    """
    return Contact.__portal_horizon_clause__(scope)


class ContactService:
    class _PortalContactRepository(TenantScopedRepository):
        """The contact repo an external (client) login gets (#193): every read demands a link
        to a company inside the horizon — a client reads their companies' people, never the
        org's whole address book. Unlinked contacts are invisible too: for a client they are
        someone else's drafts, not shared data.

        It follows ``ctx.is_portal``, which since #274 means *any* client-role login, not only
        a contact-linked one — a directly-invited client fell past this repo entirely and read
        the whole address book, the leak #252 closed for companies but not for their people.

        It overrides ``horizon_condition``, not ``_scoped``: the predicate is then the *one*
        answer every path takes — ``get_or_404``, the list, ``scoped_count_select`` and the
        service's hand-built ``COUNT(*)`` alike. Overriding ``_scoped`` left the others
        reading the looser staff rule (#285).
        """

        def horizon_condition(self):  # noqa: ANN202 — mirrors the base signature
            return _linked_in_scope(self.company_scope)

    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = (
            self._PortalContactRepository(
                ctx.session, ctx.org.id, Contact, company_scope=ctx.company_scope
            )
            if ctx.is_portal
            else ctx.repo(Contact)
        )
        self.links = ctx.repo(CompanyContact)
        self.custom_fields = CustomFieldsService(ctx)

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
        contact_type_id: uuid.UUID | None = None,
        q: str | None = None,
        sort: str | None = None,
        count: bool = True,
    ) -> tuple[Sequence[Contact], int]:
        conditions = []
        if q:
            pattern = f"%{q.strip()}%"
            conditions.append(
                or_(
                    Contact.first_name.ilike(pattern),
                    Contact.last_name.ilike(pattern),
                    Contact.email.ilike(pattern),
                )
            )
        if (
            company_id is not None
            and self.ctx.company_scope is not None
            and company_id not in self.ctx.company_scope
        ):
            # Filtering on a company outside the horizon answers 404, like reading that
            # company does (#191) — an empty list would confirm the company exists. This holds
            # for restricted *staff* too, not only a client login (#285): otherwise the filter
            # answered "that client has these people" to someone who cannot see the client.
            raise AppError("not_found", "errors.not_found", status_code=404)

        stmt = self.repo.scoped_select().where(*conditions)
        count_stmt = (
            select(func.count())
            .select_from(Contact)
            .where(Contact.org_id == self._org_id, *conditions)
        )
        # The count statement is hand-built (it can't ride ``scoped_select``), so the horizon is
        # AND'd on from the same seam the main statement gets it from — including the portal
        # repo's stricter override, since ``self.repo`` *is* that repo for a client login.
        horizon = self.repo.horizon_condition()
        if horizon is not None:
            count_stmt = count_stmt.where(horizon)
        # A type filter matches a person who holds that type at *any* company (the type lives on
        # the link, §91); with both filters set it is one link carrying both, which is why they
        # share a single condition list rather than getting an ``EXISTS`` each.
        #
        # **A link filter is an ``EXISTS``, never a join + ``DISTINCT``** (#301). The join
        # multiplies a contact by its matching links and the ``DISTINCT`` folds them back, which
        # reads as harmless and is not: ``SELECT DISTINCT`` requires every ``ORDER BY`` expression
        # to appear in the select list, and four of the sortable columns are ``func.lower(...)``
        # expressions — names sort case-insensitively. So the *filtered* list 500'd on exactly the
        # sorts the unfiltered list handles fine, which is why it survived so long: it needs a
        # company/type filter **and** a name sort. Every scoped contact picker sends that pair,
        # and ``contactsForScope`` swallows the failure, so it read as "this client has no
        # contacts" rather than as an error. ``EXISTS`` cannot multiply a row, so nothing needs
        # de-duplicating and the count is a plain ``COUNT(*)`` again.
        if company_id is not None or contact_type_id is not None:
            link_where = [
                CompanyContact.contact_id == Contact.id,
                CompanyContact.org_id == self._org_id,
            ]
            if company_id is not None:
                link_where.append(CompanyContact.company_id == company_id)
            if contact_type_id is not None:
                link_where.append(CompanyContact.contact_type_id == contact_type_id)
            linked = select(1).where(*link_where).exists()
            stmt = stmt.where(linked)
            count_stmt = count_stmt.where(linked)

        stmt = apply_sort(stmt, sort, SORTABLE, default=Contact.created_at.desc())
        stmt = stmt.limit(limit).offset(offset)
        items = list((await self.ctx.session.execute(stmt)).scalars().all())
        # ``count=False`` skips the discarded COUNT(*) — batched consumers like the CSV export
        # never show a total (docs/PERFORMANCE.md).
        total = int(await self.ctx.session.scalar(count_stmt) or 0) if count else len(items)
        await self._attach_companies(items)
        return items, total

    async def get(self, contact_id: uuid.UUID) -> Contact:
        contact = await self.repo.get_or_404(contact_id)
        await self._attach_companies([contact])
        return contact

    async def contacts_for_company(
        self, company_id: uuid.UUID, *, limit: int | None = None
    ) -> tuple[list[tuple[Contact, bool]], int]:
        """Contacts linked to a company, primary-first then by creation time (panel order).

        Bounded and counted (#407). The panel that draws these is a chip field rather than a
        list, so the cap is generous — nobody wants the sixth of six contacts folded away —
        but "generous" and "absent" are different things, and it was absent: the read's size
        was the client's, which is the one thing docs/PERFORMANCE.md calls a build break
        everywhere else.
        """
        # The company hub reached this having already loaded the company through the horizon,
        # but the `contacts.for_company` AI/MCP tool hands the id straight in — so the check
        # belongs here, on the query, not on the one caller that happens to be safe. Free: the
        # horizon is already resolved on the context.
        if self.ctx.company_scope is not None and company_id not in self.ctx.company_scope:
            raise AppError("not_found", "errors.not_found", status_code=404)
        rows = (
            await self.ctx.session.execute(
                select(Contact, CompanyContact.is_primary)
                .join(CompanyContact, CompanyContact.contact_id == Contact.id)
                .where(
                    Contact.org_id == self._org_id,
                    CompanyContact.org_id == self._org_id,
                    CompanyContact.company_id == company_id,
                )
                .order_by(CompanyContact.is_primary.desc(), Contact.created_at)
                .limit(limit)
            )
        ).all()
        if limit is None:
            return [(row[0], row[1]) for row in rows], len(rows)
        total = int(
            await self.ctx.session.scalar(
                select(func.count())
                .select_from(CompanyContact)
                .where(
                    CompanyContact.org_id == self._org_id,
                    CompanyContact.company_id == company_id,
                )
            )
            or 0
        )
        return [(row[0], row[1]) for row in rows], total

    async def candidates_for_company(
        self, company_id: uuid.UUID, *, limit: int = 20
    ) -> Sequence[Contact]:
        """Org contacts not yet linked to this company — the type-ahead's **opening** options.

        Twenty, not five hundred (#290). Every render of a client page shipped the whole
        address book so that a dropdown most visits never open could be filtered in the
        browser; the picker searches the API as the user types now, so this only has to fill
        the list before anyone has typed anything.
        """
        linked = select(CompanyContact.contact_id).where(
            CompanyContact.org_id == self._org_id,
            CompanyContact.company_id == company_id,
        )
        stmt = (
            self.repo.scoped_select()
            .where(Contact.id.notin_(linked))
            .order_by(Contact.first_name, Contact.last_name)
            .limit(limit)
        )
        return list((await self.ctx.session.execute(stmt)).scalars().all())

    # --- writes -------------------------------------------------------------- #
    async def _ensure_email_unique(
        self, email: str | None, *, exclude_id: uuid.UUID | None = None
    ) -> None:
        """One person, one contact row: the same address twice is a duplicate, not a namesake.

        Case-insensitive and service-level (no DB constraint — existing tenants may already
        hold duplicates, and a unique index would abort their unattended upgrade).
        """
        if not email:
            return
        stmt = select(Contact.id).where(
            Contact.org_id == self._org_id, func.lower(Contact.email) == email.lower()
        )
        if exclude_id is not None:
            stmt = stmt.where(Contact.id != exclude_id)
        if await self.ctx.session.scalar(stmt):
            raise AppError(
                "conflict",
                "errors.contact_email_exists",
                status_code=409,
                fields={"email": "errors.contact_email_exists"},
            )

    async def _phone_region(self) -> str:
        """Which country a national phone number belongs to: the org's.

        Unlike a company, a contact has no country column of its own — the person's number is
        read in the organisation's country. Called only once a phone value is actually being
        written, and skipped entirely by an international ``+…`` number.
        """
        return await org_default_country(self.ctx.session, self.ctx.org.id)

    def _guard_client_write(self, company_ids: Sequence[uuid.UUID]) -> None:
        """What an external (client) login may write, and the honest reason when it may not.

        A client's contacts repo only ever returns people linked to a company inside their
        horizon, so two writes would land in a black hole — saved, then invisible on the very
        next read. Both are refused *before* anything is created, and with a message that names
        the missing piece rather than the generic ``errors.not_found`` #274 was filed about:

        * **an empty horizon** — the login is scoped to no company at all (a directly-invited
          client, or a portal contact attached to nothing). Nothing they add could ever be
          theirs, and granting more permissions will never change that; only linking their
          contact to a company, or assigning them a company group, will. ``403``: this is about
          the caller's own account, so it leaks nothing.
        * **no company on the contact** — a floating contact is invisible to them by design
          (#193). ``422`` on the field, like any other missing required input.
        """
        if not self.ctx.is_portal:
            return
        if not self.ctx.company_scope:
            raise AppError("forbidden", "errors.no_company_scope", status_code=403)
        if not company_ids:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"company_ids": "errors.contact_company_required"},
            )

    async def create(self, data: ContactCreate) -> Contact:
        self.ctx.require("contacts.contact.write")
        values = data.model_dump()
        company_ids = values.pop("company_ids", None) or []
        # Attaching is its own capability (``contacts.link.write``), demanded up front rather
        # than from inside the loop: a caller who lacks it must not get as far as writing the
        # contact row and having it rolled back under them.
        if company_ids:
            self.ctx.require("contacts.link.write")
        self._guard_client_write(company_ids)
        # Notes are markdown source (issue #66/#228): strip raw HTML on write.
        values["notes"] = sanitize_markdown(values.get("notes"))
        values["email"] = (values.get("email") or "").strip() or None
        await self._ensure_email_unique(values["email"])
        # New writes store E.164 (issue #256); only pre-existing freeform rows are grandfathered.
        # A contact carries no country of its own, so a national number is read in the org's
        # (``org_settings.default_country``) — which is what lets a pasted client list import.
        values["phone"] = normalize_phone(
            values.get("phone"), region=await self._phone_region()
        )
        values["custom"] = await self.custom_fields.validate(
            ENTITY_TYPE, values.get("custom") or {}
        )
        contact = await self.repo.create(**values)
        await ActivityService(self.ctx).record_created(ENTITY_TYPE, contact.id)
        # ``_link``, not ``link``: the public entry point re-reads the contact through the repo
        # as an existence check, and for a client login that repo demands an *existing* company
        # link — which a contact created one statement ago cannot have yet. That re-read is what
        # made every client-side "add a contact" answer 404 (#274). The row is right here and
        # already tenant-scoped; there is nothing left to check.
        for company_id in company_ids:
            await self._link(contact.id, company_id, is_primary=None)
        await self._attach_companies([contact])
        return contact

    async def update(self, contact_id: uuid.UUID, data: ContactUpdate) -> Contact:
        self.ctx.require("contacts.contact.write")
        contact = await self.repo.get_or_404(contact_id)
        before = snapshot(contact, _AUDITED_FIELDS)
        values = data.model_dump(exclude_unset=True)
        if "notes" in values:
            values["notes"] = sanitize_markdown(values.get("notes"))
        if "email" in values:
            values["email"] = (values.get("email") or "").strip() or None
            await self._ensure_email_unique(values["email"], exclude_id=contact.id)
        # Only a *changed* phone is validated (issue #256): rows predating validation hold
        # freeform strings, and an unrelated edit must not force them through the new gate.
        if "phone" in values and values["phone"] != contact.phone:
            values["phone"] = normalize_phone(
                values["phone"], region=await self._phone_region()
            )
        if "custom" in values:
            values["custom"] = await self.custom_fields.validate(
                ENTITY_TYPE, values.get("custom") or {}
            )
        contact = await self.repo.update(contact, **values)
        await ActivityService(self.ctx).record_update(
            ENTITY_TYPE, contact.id, before, snapshot(contact, _AUDITED_FIELDS)
        )
        await self._attach_companies([contact])
        return contact

    async def delete(self, contact_id: uuid.UUID) -> None:
        self.ctx.require("contacts.contact.delete")
        contact = await self.repo.get_or_404(contact_id)
        await self.repo.delete(contact)

    # --- links --------------------------------------------------------------- #
    async def link(
        self,
        contact_id: uuid.UUID,
        company_id: uuid.UUID,
        *,
        is_primary: bool | None = None,
        contact_type_id: uuid.UUID | None = None,
        set_type: bool = False,
    ) -> CompanyContact:
        """Attach a contact to a company (idempotent).

        ``is_primary``: ``True`` forces primary (unsets any other), ``False`` forces non-primary,
        ``None`` auto-promotes to primary only when the company has no primary yet. ``set_type``
        marks that ``contact_type_id`` should be written (``None`` clears it); when ``False`` the
        link's existing type is left untouched.
        """
        self.ctx.require("contacts.link.write")
        await self.repo.get_or_404(contact_id)  # tenant- and horizon-scoped existence check
        return await self._link(
            contact_id,
            company_id,
            is_primary=is_primary,
            contact_type_id=contact_type_id,
            set_type=set_type,
        )

    async def _link(
        self,
        contact_id: uuid.UUID,
        company_id: uuid.UUID,
        *,
        is_primary: bool | None = None,
        contact_type_id: uuid.UUID | None = None,
        set_type: bool = False,
    ) -> CompanyContact:
        """``link`` minus the contact existence check — for a caller holding the row already."""
        await self._ensure_company_visible(company_id)
        if set_type and contact_type_id is not None:
            await self._ensure_type_in_tenant(contact_type_id)

        link = await self._get_link(company_id, contact_id)
        if link is None:
            link = await self.links.create(
                company_id=company_id,
                contact_id=contact_id,
                is_primary=False,
                contact_type_id=contact_type_id if set_type else None,
            )
        elif set_type:
            link = await self.links.update(link, contact_type_id=contact_type_id)

        make_primary = is_primary is True
        if is_primary is None:
            make_primary = not await self._company_has_primary(company_id)

        if make_primary:
            await self._set_company_primary(company_id, contact_id)
        elif is_primary is False and link.is_primary:
            link = await self.links.update(link, is_primary=False)
        return link

    async def set_primary(self, contact_id: uuid.UUID, company_id: uuid.UUID) -> None:
        self.ctx.require("contacts.link.write")
        await self._ensure_company_visible(company_id)
        link = await self._get_link(company_id, contact_id)
        if link is None:
            raise AppError("not_found", "errors.not_found", status_code=404)
        await self._set_company_primary(company_id, contact_id)

    async def unlink(self, contact_id: uuid.UUID, company_id: uuid.UUID) -> None:
        self.ctx.require("contacts.link.write")
        # A company outside the horizon answers 404 here too, rather than the silent 204 an
        # idempotent detach would otherwise give it: "there is nothing to unlink" and "you may
        # not see that company" are different answers, and only the second is true (#191).
        await self._ensure_company_visible(company_id)
        link = await self._get_link(company_id, contact_id)
        if link is not None:
            await self.links.delete(link)

    # --- internals ----------------------------------------------------------- #
    async def _get_link(
        self, company_id: uuid.UUID, contact_id: uuid.UUID
    ) -> CompanyContact | None:
        # Through the repo, so the company horizon (#191) rides along: ``set_primary`` and
        # ``unlink`` take a company id straight from the caller, and a hand-built org-only
        # query let a scoped login re-primary or detach a contact at a company it cannot see.
        return await self.ctx.session.scalar(
            self.links.scoped_select().where(
                CompanyContact.company_id == company_id,
                CompanyContact.contact_id == contact_id,
            )
        )

    async def _company_has_primary(self, company_id: uuid.UUID) -> bool:
        count = await self.ctx.session.scalar(
            select(func.count())
            .select_from(CompanyContact)
            .where(
                CompanyContact.org_id == self._org_id,
                CompanyContact.company_id == company_id,
                CompanyContact.is_primary.is_(True),
            )
        )
        return bool(count)

    async def _set_company_primary(
        self, company_id: uuid.UUID, contact_id: uuid.UUID
    ) -> None:
        # Clear then set, in two statements: the partial unique index (one primary per company)
        # is a bare unique index, so it's checked per-row immediately — a single UPDATE that
        # swaps which row is primary would momentarily have two primaries and fail.
        await self.ctx.session.execute(
            update(CompanyContact)
            .where(
                CompanyContact.org_id == self._org_id,
                CompanyContact.company_id == company_id,
                CompanyContact.is_primary.is_(True),
            )
            .values(is_primary=False)
        )
        await self.ctx.session.flush()
        await self.ctx.session.execute(
            update(CompanyContact)
            .where(
                CompanyContact.org_id == self._org_id,
                CompanyContact.company_id == company_id,
                CompanyContact.contact_id == contact_id,
            )
            .values(is_primary=True)
        )
        await self.ctx.session.flush()

    async def _ensure_company_visible(self, company_id: uuid.UUID) -> None:
        """The company exists in this tenant **and** inside the caller's horizon (#191).

        404 either way — the same answer reading that company gets — so a link never confirms a
        company the caller may not see. The horizon half is not merely belt-and-braces: the
        repository's write guard only fires on paths that *insert* a ``company_contacts`` row,
        which left ``unlink`` and the primary flip taking a company id on trust.
        """
        # RLS already scopes ``companies`` to the current org; the explicit filter is
        # defence-in-depth (Golden Rule 1).
        ok = await self.ctx.session.scalar(
            text("SELECT 1 FROM companies WHERE id = :cid AND org_id = :oid"),
            {"cid": company_id, "oid": self._org_id},
        )
        if not ok or (
            self.ctx.company_scope is not None and company_id not in self.ctx.company_scope
        ):
            raise AppError("not_found", "errors.not_found", status_code=404)

    async def _ensure_type_in_tenant(self, contact_type_id: uuid.UUID) -> None:
        ok = await self.ctx.session.scalar(
            select(ContactType.id).where(
                ContactType.org_id == self._org_id, ContactType.id == contact_type_id
            )
        )
        if not ok:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"contact_type_id": "errors.validation"},
            )

    async def _attach_companies(self, contacts: Sequence[Contact]) -> None:
        """Populate ``ContactRead.companies`` for each contact in one batched query."""
        contact_ids = [c.id for c in contacts]
        mapping = await self._load_companies_map(contact_ids)
        for contact in contacts:
            contact.companies = mapping.get(contact.id, [])  # type: ignore[attr-defined]

    async def _load_companies_map(
        self, contact_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, list[ContactCompanyLink]]:
        if not contact_ids:
            return {}
        stmt = select(
            CompanyContact.contact_id,
            CompanyContact.company_id,
            CompanyContact.is_primary,
            CompanyContact.contact_type_id,
        ).where(
            CompanyContact.org_id == self._org_id,
            CompanyContact.contact_id.in_(contact_ids),
        )
        if self.ctx.company_scope is not None:
            # A contact reachable inside the horizon may also be linked to companies outside
            # it — naming those on the read hands a client the roster #252 took away, one
            # colleague at a time. They see the links they can see.
            stmt = stmt.where(CompanyContact.company_id.in_(self.ctx.company_scope))
        rows = (await self.ctx.session.execute(stmt)).all()

        company_ids = list({row.company_id for row in rows})
        names: dict[uuid.UUID, str] = {}
        if company_ids:
            name_stmt = text(
                "SELECT id, name FROM companies WHERE id IN :ids"
            ).bindparams(bindparam("ids", expanding=True))
            name_rows = (
                await self.ctx.session.execute(name_stmt, {"ids": company_ids})
            ).all()
            names = {row[0]: row[1] for row in name_rows}

        result: dict[uuid.UUID, list[ContactCompanyLink]] = {}
        for row in rows:
            result.setdefault(row.contact_id, []).append(
                ContactCompanyLink(
                    company_id=row.company_id,
                    name=names.get(row.company_id, ""),
                    is_primary=row.is_primary,
                    contact_type_id=row.contact_type_id,
                )
            )
        for links in result.values():
            links.sort(key=lambda link: (not link.is_primary, link.name.lower()))
        return result


class ContactTypeService:
    """CRUD for tenant-configurable contact types (issue #91), gated on ``contacts.type.manage``.

    The leave-types shape: ``label_i18n`` + ``active`` + ``position``, unique ``key`` per org. The
    type is referenced from ``company_contacts.contact_type_id``; deleting a type SET NULLs those
    links (see the model), so a type can always be removed without stranding a contact.
    """

    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = ctx.repo(ContactType)

    @property
    def _org_id(self) -> uuid.UUID:
        return self.ctx.org.id

    async def list(self, *, include_inactive: bool = False) -> Sequence[ContactType]:
        stmt = self.repo.scoped_select()
        if not include_inactive:
            stmt = stmt.where(ContactType.active.is_(True))
        stmt = stmt.order_by(ContactType.position, ContactType.key)
        return list((await self.ctx.session.execute(stmt)).scalars().all())

    async def create(self, data: ContactTypeCreate) -> ContactType:
        self.ctx.require("contacts.type.manage")
        existing = await self.ctx.session.scalar(
            select(ContactType.id).where(
                ContactType.org_id == self._org_id, ContactType.key == data.key
            )
        )
        if existing is not None:
            raise AppError(
                "conflict", "errors.conflict", status_code=409, fields={"key": "errors.conflict"}
            )
        return await self.repo.create(**data.model_dump(mode="json"))

    async def update(
        self, contact_type_id: uuid.UUID, data: ContactTypeUpdate
    ) -> ContactType:
        self.ctx.require("contacts.type.manage")
        contact_type = await self.repo.get_or_404(contact_type_id)
        return await self.repo.update(
            contact_type, **data.model_dump(mode="json", exclude_unset=True)
        )

    async def delete(self, contact_type_id: uuid.UUID) -> None:
        self.ctx.require("contacts.type.manage")
        contact_type = await self.repo.get_or_404(contact_type_id)
        await self.repo.delete(contact_type)
