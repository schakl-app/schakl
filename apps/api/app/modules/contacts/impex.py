"""CSV import/export shape for contacts (issue #77).

Upsert matches on ``email`` — the one natural key a contact spreadsheet reliably carries; a
row without one always creates. The ``company`` column is the FK case the issue calls out:
export writes the contact's first-listed (primary-first) company name, import resolves the
cell **by exact name or UUID**, tenant-scoped, and an unresolved or ambiguous reference is a
row error — never a silently orphaned contact. A contact linked to several companies keeps
its extra links on a round-trip (the import only ever *adds* a link, an empty cell never
unlinks); only the first link is what the CSV can express.

``companies`` belongs to another module: the resolver references it as a bare table by name,
exactly like the service does (CLAUDE.md §3 — modules never import each other's internals).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import column, select, table

from app.core.impex import ImpexColumn, ImpexDescriptor
from app.core.tenancy import RequestContext
from app.modules.contacts.models import Contact
from app.modules.contacts.schemas import ContactCreate, ContactUpdate
from app.modules.contacts.service import ContactService

_TEXT_FIELDS = ("first_name", "last_name", "email", "phone", "job_title", "notes")

_companies = table("companies", column("id"), column("name"), column("org_id"))


def _first_company_name(contact: Any) -> str | None:
    """Export cell for ``company``: the primary-first list the service attaches."""
    links = getattr(contact, "companies", None) or []
    return links[0].name if links else None


async def _fetch_page(
    ctx: RequestContext, *, limit: int, offset: int, filters: dict[str, Any]
) -> Sequence[Any]:
    items, _ = await ContactService(ctx).list(
        limit=limit,
        offset=offset,
        q=filters.get("q"),
        company_id=filters.get("company_id"),
        sort=filters.get("sort"),
        count=False,
    )
    return items


async def _find_existing(ctx: RequestContext, values: list[str]) -> dict[str, list[Any]]:
    stmt = ctx.repo(Contact).scoped_select().where(Contact.email.in_(values))
    found: dict[str, list[Any]] = {}
    for contact in (await ctx.session.execute(stmt)).scalars():
        found.setdefault(contact.email, []).append(contact)
    return found


async def _resolve_company(
    ctx: RequestContext, refs: list[str]
) -> dict[str, uuid.UUID | str]:
    """Batch-resolve ``company`` cells to tenant-scoped company ids.

    A cell that parses as a UUID resolves by id; anything else by **exact** name. Two grouped
    queries for the whole file, never one per row. A name carried by two companies is
    ambiguous — erroring beats silently picking one.
    """
    by_id: dict[str, uuid.UUID] = {}
    names: list[str] = []
    for ref in refs:
        try:
            by_id[ref] = uuid.UUID(ref)
        except ValueError:
            names.append(ref)

    resolved: dict[str, uuid.UUID | str] = {}
    if by_id:
        rows = (
            await ctx.session.execute(
                select(_companies.c.id).where(
                    _companies.c.org_id == ctx.org.id,
                    _companies.c.id.in_(list(by_id.values())),
                )
            )
        ).scalars()
        found = set(rows)
        for ref, company_id in by_id.items():
            resolved[ref] = (
                company_id if company_id in found else "impex.errors.unresolved_reference"
            )
    if names:
        rows = (
            await ctx.session.execute(
                select(_companies.c.id, _companies.c.name).where(
                    _companies.c.org_id == ctx.org.id,
                    _companies.c.name.in_(names),
                )
            )
        ).all()
        by_name: dict[str, list[uuid.UUID]] = {}
        for company_id, name in rows:
            by_name.setdefault(name, []).append(company_id)
        for name in names:
            matches = by_name.get(name, [])
            if len(matches) == 1:
                resolved[name] = matches[0]
            elif matches:
                resolved[name] = "impex.errors.ambiguous_match"
            else:
                resolved[name] = "impex.errors.unresolved_reference"
    return resolved


async def _create(ctx: RequestContext, values: dict[str, Any]) -> None:
    company_id = values.get("company_id")
    await ContactService(ctx).create(
        ContactCreate(
            first_name=values["first_name"],
            last_name=values.get("last_name"),
            email=values.get("email"),
            phone=values.get("phone"),
            job_title=values.get("job_title"),
            notes=values.get("notes"),
            company_ids=[company_id] if company_id else [],
            custom=values.get("custom") or {},
        )
    )


async def _update(ctx: RequestContext, contact: Any, values: dict[str, Any]) -> None:
    fields: dict[str, Any] = {key: values[key] for key in _TEXT_FIELDS if key in values}
    if "custom" in values:
        fields["custom"] = values["custom"]
    service = ContactService(ctx)
    if fields:
        await service.update(contact.id, ContactUpdate(**fields))
    if values.get("company_id"):
        # Idempotent attach; auto-promotes to primary only when the company has none yet.
        await service.link(contact.id, values["company_id"], is_primary=None)


CONTACT_IMPEX = ImpexDescriptor(
    entity_type="contact",
    read_permission="contacts.contact.read",
    write_permission="contacts.contact.write",
    natural_key="email",
    filters=("q", "company_id", "sort"),
    columns=(
        ImpexColumn("first_name", required=True),
        ImpexColumn("last_name"),
        ImpexColumn("email", data_type="email"),
        ImpexColumn("phone"),
        ImpexColumn("job_title"),
        ImpexColumn("notes"),
        # FK: resolved by exact company name or UUID; an empty cell never unlinks.
        ImpexColumn(
            "company",
            data_type="fk",
            field="company_id",
            clearable=False,
            getter=_first_company_name,
        ),
    ),
    fetch_page=_fetch_page,
    find_existing=_find_existing,
    create_row=_create,
    update_row=_update,
    fk_resolvers={"company": _resolve_company},
)
