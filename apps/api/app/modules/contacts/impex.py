"""CSV import/export shape for contacts (issue #77).

Upsert matches on ``email`` — the one natural key a contact spreadsheet reliably carries; a
row without one always creates. The ``company`` column is the FK case the issue calls out:
export writes the contact's first-listed (primary-first) company name, import resolves the
cell **by exact name (label or legal name) or UUID**, tenant-scoped, and an unresolved or
ambiguous reference is a row error — never a silently orphaned contact. A contact linked to
several companies keeps its extra links on a round-trip (the import only ever *adds* a link, an
empty cell never unlinks); only the first link is what the CSV can express.

``companies`` belongs to another module, so the reference is resolved by core's shared
``name_or_id_resolver`` — which reaches the table as a bare name (CLAUDE.md §3 — modules never
import each other's internals). This file held a hand-rolled copy of that function until the
client label / legal-name split gave the two copies something to disagree about.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import select

from app.core.impex import ImpexColumn, ImpexDescriptor
from app.core.impex.resolvers import name_or_id_resolver
from app.core.impex.spec import ImpexExtension
from app.core.tenancy import RequestContext
from app.modules.contacts.models import CompanyContact, Contact
from app.modules.contacts.schemas import ContactCreate, ContactUpdate
from app.modules.contacts.service import ContactService

_TEXT_FIELDS = ("first_name", "last_name", "email", "phone", "job_title", "notes")


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


async def _find_existing(
    ctx: RequestContext, key: str, values: list[str]
) -> dict[str, list[Any]]:
    stmt = ctx.repo(Contact).scoped_select().where(Contact.email.in_(values))
    found: dict[str, list[Any]] = {}
    for contact in (await ctx.session.execute(stmt)).scalars():
        found.setdefault(contact.email, []).append(contact)
    return found


async def _create(ctx: RequestContext, values: dict[str, Any]) -> Any:
    company_id = values.get("company_id")
    return await ContactService(ctx).create(
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


async def _company_primary_contact(ctx: RequestContext, companies: Sequence[Any]) -> None:
    """Attach each company's primary contact for the export, in **one** query for the page.

    The company list service has no idea these columns exist, so without this the export goes
    N+1 across the whole client list (docs/PERFORMANCE.md is a first-class rule, not advice).
    The attribute is stashed on the ORM object for the getters below to read; nothing is
    flushed, and a company with no contact simply keeps ``None``.
    """
    ids = [company.id for company in companies if getattr(company, "id", None)]
    for company in companies:
        company._impex_primary_contact = None  # noqa: SLF001 — our own transient attribute
    if not ids:
        return
    stmt = (
        select(CompanyContact.company_id, Contact)
        .join(Contact, Contact.id == CompanyContact.contact_id)
        .where(
            CompanyContact.org_id == ctx.org.id,
            CompanyContact.company_id.in_(ids),
            CompanyContact.is_primary.is_(True),
        )
    )
    primary = {company_id: contact for company_id, contact in await ctx.session.execute(stmt)}
    for company in companies:
        company._impex_primary_contact = primary.get(company.id)  # noqa: SLF001


def _contact_field(name: str):
    def getter(company: Any) -> Any:
        contact = getattr(company, "_impex_primary_contact", None)
        return getattr(contact, name, None) if contact else None

    return getter


def _same_person(contact: Any, first_name: str, last_name: str) -> bool:
    return (
        (contact.first_name or "").strip().casefold() == first_name.casefold()
        and (contact.last_name or "").strip().casefold() == last_name.casefold()
    )


async def _match_contact(
    ctx: RequestContext, company: Any, email: str | None, first_name: str, last_name: str
) -> Any | None:
    """Find the person this row is about, so a re-import updates instead of duplicating.

    E-mail first — the same natural key the contact import upserts on, and the only one that
    is stable org-wide. Then, **within this one company**, by name: without that fallback a
    client list carrying contact names but no addresses grows a fresh copy of every contact on
    every import, and adding e-mails to a list imported earlier orphans the people already
    there instead of filling their address in (both seen in a browser run).

    Name matching is deliberately scoped to the host company and never org-wide. "Jan" at one
    client and "Jan" at another are two people, and merging them would be far worse than the
    duplicate this is preventing.
    """
    if email:
        found = await _find_existing(ctx, "email", [email])
        matched = next(iter(found.get(email, [])), None)
        if matched is not None:
            return matched
    if not first_name:
        return None
    for contact, _ in await ContactService(ctx).contacts_for_company(company.id):
        # Only a contact with no address of its own: one who *has* a different e-mail is a
        # different person who happens to share a name, not this row.
        if not contact.email and _same_person(contact, first_name, last_name):
            return contact
    return None


async def _apply_to_company(ctx: RequestContext, company: Any, values: dict[str, Any]) -> None:
    """Write the contact columns of one imported company row — through contacts' own service.

    A company whose contact columns are all empty gets nothing at all; a row with a name or an
    address updates the person it matches (see :func:`_match_contact`) or creates them.

    ``is_primary=None`` is deliberate: it promotes the first contact of a company and never
    demotes an existing primary, so an import cannot silently reassign who the client's main
    contact is.
    """
    service = ContactService(ctx)
    email = (values.get("email") or "").strip() or None
    first_name = (values.get("first_name") or "").strip()
    last_name = (values.get("last_name") or "").strip()
    contact = await _match_contact(ctx, company, email, first_name, last_name)

    if contact is not None:
        fields = {
            key: value
            for key, value in values.items()
            if key in _TEXT_FIELDS and value is not None
        }
        # Never rewrite the key we found them by; do fill in an address they did not have.
        if contact.email:
            fields.pop("email", None)
        if fields:
            await service.update(contact.id, ContactUpdate(**fields))
    elif first_name or email:
        contact = await service.create(
            ContactCreate(
                first_name=first_name or (email or "").split("@")[0],
                last_name=values.get("last_name"),
                email=email,
                phone=values.get("phone"),
                job_title=values.get("job_title"),
            )
        )
    if contact is not None:
        await service.link(contact.id, company.id, is_primary=None)


#: The contact person a client list carries in the same row (issue #77).
#:
#: Companies must not import contacts' internals (§6), so contacts *contributes* these columns
#: to the company shape — the panels pattern, applied to import/export. Every key is namespaced
#: with ``contact_`` so it can never collide with a column companies owns, and none is
#: ``required``: a client list without contact people must import exactly as before.
#:
#: None is ``clearable`` either, which is a deliberate asymmetry with the entity's own columns.
#: An empty ``city`` on a company row means "this client has no city"; an empty ``contact_phone``
#: on the same row means "this client list doesn't carry phone numbers" — a *company* import has
#: no standing to wipe a contact's details, and a round-tripped export must not either.
CONTACT_ON_COMPANY_EXTENSION = ImpexExtension(
    entity_type="company",
    module="contacts",
    # Both gates the write path actually goes through. Declaring only one would let a caller
    # holding it see columns whose import then 403s halfway and rolls back the whole file.
    write_permissions=("contacts.contact.write", "contacts.link.write"),
    columns=(
        ImpexColumn(
            "contact_first_name",
            clearable=False,
            field="first_name",
            getter=_contact_field("first_name"),
            aliases=("voornaam", "first name", "contactpersoon", "contact person"),
        ),
        ImpexColumn(
            "contact_last_name",
            clearable=False,
            field="last_name",
            getter=_contact_field("last_name"),
            aliases=("achternaam", "last name", "surname"),
        ),
        ImpexColumn(
            "contact_email",
            data_type="email",
            clearable=False,
            field="email",
            getter=_contact_field("email"),
            aliases=("e-mail contactpersoon", "contact email", "contactpersoon e-mail"),
        ),
        ImpexColumn(
            "contact_phone",
            # The org's country, never the *company's*: a contact is read in the organisation's
            # country wherever it is written from, and the preview must agree with the write.
            data_type="phone",
            clearable=False,
            field="phone",
            getter=_contact_field("phone"),
            aliases=("telefoon contactpersoon", "contact phone", "mobiel"),
        ),
        ImpexColumn(
            "contact_job_title",
            clearable=False,
            field="job_title",
            getter=_contact_field("job_title"),
            aliases=("functie", "job title", "rol"),
        ),
    ),
    apply=_apply_to_company,
    hydrate=_company_primary_contact,
)


CONTACT_IMPEX = ImpexDescriptor(
    entity_type="contact",
    read_permission="contacts.contact.read",
    write_permission="contacts.contact.write",
    natural_keys=("email",),
    filters=("q", "company_id", "sort"),
    columns=(
        ImpexColumn("first_name", required=True),
        ImpexColumn("last_name"),
        ImpexColumn("email", data_type="email"),
        # No country of its own: a national number is read in the org's, exactly as
        # ``ContactService`` reads one (issue #289).
        ImpexColumn("phone", data_type="phone"),
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
    # The shared resolver, not a copy of it: it answers to a client's label *and*
    # its legal name (``app/core/naming.py``), and this file held a verbatim
    # duplicate that would have kept answering to only one of them.
    fk_resolvers={"company": name_or_id_resolver("companies")},
)
