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
# (CLAUDE.md §6: modules never import each other's internals).
_companies = table("companies", column("id"), column("name"), column("org_id"))


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
        select(func.min(func.lower(_companies.c.name)))
        .select_from(CompanyContact)
        .join(
            _companies,
            (_companies.c.id == CompanyContact.company_id)
            & (_companies.c.org_id == CompanyContact.org_id),
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
    the membership may see. ``Contact`` carries no ``company_id`` column, so the repository's
    generic horizon filter (#191) cannot express this — the module owns the shape."""
    return (
        select(CompanyContact.id)
        .where(
            CompanyContact.contact_id == Contact.id,
            CompanyContact.company_id.in_(scope or frozenset()),
        )
        .exists()
    )


class ContactService:
    class _PortalContactRepository(TenantScopedRepository):
        """The contact repo a portal login gets (#193): every read demands a link to a company
        inside the horizon, on the same ``_scoped()`` seam org filtering rides — a client reads
        their companies' people, never the org's whole address book. Unlinked contacts are
        invisible too: for a portal user they are someone else's drafts, not shared data."""

        def _scoped(self):  # noqa: ANN202 — mirrors the base signature
            return super()._scoped().where(_linked_in_scope(self.company_scope))

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
        if self.ctx.is_portal and company_id is not None and company_id not in (
            self.ctx.company_scope or frozenset()
        ):
            # Filtering on a company outside the horizon answers 404, like reading that
            # company does (#191) — an empty list would confirm the company exists.
            raise AppError("not_found", "errors.not_found", status_code=404)

        stmt = self.repo.scoped_select().where(*conditions)
        count_stmt = (
            select(func.count(func.distinct(Contact.id)))
            .select_from(Contact)
            .where(Contact.org_id == self._org_id, *conditions)
        )
        if self.ctx.is_portal:
            # The count statement is hand-built (it can't ride ``scoped_select``), so the
            # portal horizon is AND'd here; the main statement gets it from the repo.
            count_stmt = count_stmt.where(_linked_in_scope(self.ctx.company_scope))
        # A type filter matches a person who holds that type at *any* company (the type lives on
        # the link, §91), so it joins ``company_contacts`` and de-duplicates like the company one.
        if company_id is not None or contact_type_id is not None:
            join_on = CompanyContact.contact_id == Contact.id
            link_where = [CompanyContact.org_id == self._org_id]
            if company_id is not None:
                link_where.append(CompanyContact.company_id == company_id)
            if contact_type_id is not None:
                link_where.append(CompanyContact.contact_type_id == contact_type_id)
            stmt = stmt.join(CompanyContact, join_on).where(*link_where).distinct()
            count_stmt = count_stmt.join(CompanyContact, join_on).where(*link_where)

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
        self, company_id: uuid.UUID
    ) -> list[tuple[Contact, bool]]:
        """Contacts linked to a company, primary-first then by creation time (panel order)."""
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
            )
        ).all()
        return [(row[0], row[1]) for row in rows]

    async def candidates_for_company(
        self, company_id: uuid.UUID, *, limit: int = 500
    ) -> Sequence[Contact]:
        """Org contacts not yet linked to this company (the type-ahead's attach list)."""
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

    async def create(self, data: ContactCreate) -> Contact:
        self.ctx.require("contacts.contact.write")
        values = data.model_dump()
        company_ids = values.pop("company_ids", None) or []
        # Notes are markdown source (issue #66/#228): strip raw HTML on write.
        values["notes"] = sanitize_markdown(values.get("notes"))
        values["email"] = (values.get("email") or "").strip() or None
        await self._ensure_email_unique(values["email"])
        # New writes store E.164 (issue #256); only pre-existing freeform rows are grandfathered.
        values["phone"] = normalize_phone(values.get("phone"))
        values["custom"] = await self.custom_fields.validate(
            ENTITY_TYPE, values.get("custom") or {}
        )
        contact = await self.repo.create(**values)
        await ActivityService(self.ctx).record_created(ENTITY_TYPE, contact.id)
        for company_id in company_ids:
            await self.link(contact.id, company_id, is_primary=None)
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
            values["phone"] = normalize_phone(values["phone"])
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
        await self.repo.get_or_404(contact_id)  # tenant-scoped existence check
        await self._ensure_company_in_tenant(company_id)
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
        link = await self._get_link(company_id, contact_id)
        if link is None:
            raise AppError("not_found", "errors.not_found", status_code=404)
        await self._set_company_primary(company_id, contact_id)

    async def unlink(self, contact_id: uuid.UUID, company_id: uuid.UUID) -> None:
        self.ctx.require("contacts.link.write")
        link = await self._get_link(company_id, contact_id)
        if link is not None:
            await self.links.delete(link)

    # --- internals ----------------------------------------------------------- #
    async def _get_link(
        self, company_id: uuid.UUID, contact_id: uuid.UUID
    ) -> CompanyContact | None:
        return await self.ctx.session.scalar(
            select(CompanyContact).where(
                CompanyContact.org_id == self._org_id,
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

    async def _ensure_company_in_tenant(self, company_id: uuid.UUID) -> None:
        # RLS already scopes ``companies`` to the current org; the explicit filter is
        # defence-in-depth (Golden Rule 1).
        ok = await self.ctx.session.scalar(
            text("SELECT 1 FROM companies WHERE id = :cid AND org_id = :oid"),
            {"cid": company_id, "oid": self._org_id},
        )
        if not ok:
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
        rows = (
            await self.ctx.session.execute(
                select(
                    CompanyContact.contact_id,
                    CompanyContact.company_id,
                    CompanyContact.is_primary,
                    CompanyContact.contact_type_id,
                ).where(
                    CompanyContact.org_id == self._org_id,
                    CompanyContact.contact_id.in_(contact_ids),
                )
            )
        ).all()

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
