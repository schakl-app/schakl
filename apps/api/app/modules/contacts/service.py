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

from app.core.customfields import CustomFieldsService
from app.core.sorting import apply_sort
from app.core.tenancy import RequestContext
from app.errors import AppError
from app.modules.contacts.models import CompanyContact, Contact
from app.modules.contacts.schemas import ContactCompanyLink, ContactCreate, ContactUpdate

ENTITY_TYPE = "contact"


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


class ContactService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = ctx.repo(Contact)
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
        q: str | None = None,
        sort: str | None = None,
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

        stmt = self.repo.scoped_select().where(*conditions)
        count_stmt = (
            select(func.count(func.distinct(Contact.id)))
            .select_from(Contact)
            .where(Contact.org_id == self._org_id, *conditions)
        )
        if company_id is not None:
            join_on = CompanyContact.contact_id == Contact.id
            link_where = (
                CompanyContact.org_id == self._org_id,
                CompanyContact.company_id == company_id,
            )
            stmt = stmt.join(CompanyContact, join_on).where(*link_where)
            count_stmt = count_stmt.join(CompanyContact, join_on).where(*link_where)

        stmt = apply_sort(stmt, sort, SORTABLE, default=Contact.created_at.desc())
        stmt = stmt.limit(limit).offset(offset)
        items = list((await self.ctx.session.execute(stmt)).scalars().all())
        total = int(await self.ctx.session.scalar(count_stmt) or 0)
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
    async def create(self, data: ContactCreate) -> Contact:
        self.ctx.require("contacts.contact.write")
        values = data.model_dump()
        company_ids = values.pop("company_ids", None) or []
        values["custom"] = await self.custom_fields.validate(
            ENTITY_TYPE, values.get("custom") or {}
        )
        contact = await self.repo.create(**values)
        for company_id in company_ids:
            await self.link(contact.id, company_id, is_primary=None)
        await self._attach_companies([contact])
        return contact

    async def update(self, contact_id: uuid.UUID, data: ContactUpdate) -> Contact:
        self.ctx.require("contacts.contact.write")
        contact = await self.repo.get_or_404(contact_id)
        values = data.model_dump(exclude_unset=True)
        if "custom" in values:
            values["custom"] = await self.custom_fields.validate(
                ENTITY_TYPE, values.get("custom") or {}
            )
        contact = await self.repo.update(contact, **values)
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
    ) -> CompanyContact:
        """Attach a contact to a company (idempotent).

        ``is_primary``: ``True`` forces primary (unsets any other), ``False`` forces non-primary,
        ``None`` auto-promotes to primary only when the company has no primary yet.
        """
        self.ctx.require("contacts.link.write")
        await self.repo.get_or_404(contact_id)  # tenant-scoped existence check
        await self._ensure_company_in_tenant(company_id)

        link = await self._get_link(company_id, contact_id)
        if link is None:
            link = await self.links.create(
                company_id=company_id, contact_id=contact_id, is_primary=False
            )

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
                )
            )
        for links in result.values():
            links.sort(key=lambda link: (not link.is_primary, link.name.lower()))
        return result
