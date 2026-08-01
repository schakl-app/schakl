"""A :mod:`app.core.party` reference as one spreadsheet cell (issue #77).

Four entities carry parties — a domain's registry and e-mail contact, a website's technical
owner, a hosting record's contact — so the "who is responsible" column is written once here
rather than four times in four modules, exactly as :mod:`app.core.impex.resolvers` does for
plain foreign keys.

The cell is a **token**, because a party is a type *and* (sometimes) a target and a flat cell
has room for one value:

===========================  ==========================================================
``agency``                   the tenant itself — the default almost everywhere
``company``                  the record's own client
``company:Acme B.V.``        a named client
``employee:jan@bureau.nl``   a colleague, by the e-mail they log in with
``contact:info@klant.nl``    a client person, by their e-mail address
(empty)                      no party set
===========================  ==========================================================

Export writes exactly what import reads, so a party round-trips. It deliberately does **not**
write the display label the API resolves for the UI ("Jan Jansen"): a label is ambiguous, not
unique, and not something the import could ever resolve back. When the referenced row has been
deleted the token keeps its type and falls back to the raw id, so re-importing the file reports
an unresolved reference on that row instead of silently clearing a field.

Resolution is batched per file like every other reference: at most four grouped queries for a
whole import, never one per row (docs/PERFORMANCE.md).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import column, func, select, table

from app.core.party.models import PartyType
from app.core.party.schemas import PartyRef
from app.core.party.service import PartyInput
from app.core.tenancy import RequestContext

#: Bare tables by name — a token lookup is a lookup, not a data path into another module (§6).
_companies = table("companies", column("id"), column("name"), column("org_id"))
_contacts = table("contacts", column("id"), column("email"), column("org_id"))
_users = table("users", column("id"), column("email"))
_memberships = table("memberships", column("user_id"), column("org_id"))

#: The token prefix per party type that carries a target. ``agency`` never does.
_PREFIXES = {
    PartyType.COMPANY.value: "company",
    PartyType.EMPLOYEE.value: "employee",
    PartyType.CONTACT.value: "contact",
}

_INVALID = "impex.errors.invalid_party"
_UNRESOLVED = "impex.errors.unresolved_reference"
_AMBIGUOUS = "impex.errors.ambiguous_match"


# --- export ------------------------------------------------------------------------- #
async def party_tokens(ctx: RequestContext, items: Sequence[PartyInput]) -> list[str]:
    """Stored ``(type, id, company_id)`` triples → the tokens an import reads back.

    Takes the same input shape as :meth:`~app.core.party.service.PartyService.resolve_many`, so
    a module that already builds that list for its ``_attach`` builds nothing new here.
    """
    company_ids: set[uuid.UUID] = set()
    user_ids: set[uuid.UUID] = set()
    contact_ids: set[uuid.UUID] = set()
    for ptype, pid, _ in items:
        if pid is None:
            continue
        if ptype == PartyType.COMPANY.value:
            company_ids.add(pid)
        elif ptype == PartyType.EMPLOYEE.value:
            user_ids.add(pid)
        elif ptype == PartyType.CONTACT.value:
            contact_ids.add(pid)

    names: dict[uuid.UUID, str] = {}
    if company_ids:
        rows = await ctx.session.execute(
            select(_companies.c.id, _companies.c.name).where(
                _companies.c.org_id == ctx.org.id, _companies.c.id.in_(company_ids)
            )
        )
        names.update({row_id: name for row_id, name in rows})
    if user_ids:
        rows = await ctx.session.execute(
            select(_users.c.id, _users.c.email).where(
                _users.c.id.in_(user_ids),
                _users.c.id.in_(
                    select(_memberships.c.user_id).where(_memberships.c.org_id == ctx.org.id)
                ),
            )
        )
        names.update({row_id: email for row_id, email in rows})
    if contact_ids:
        rows = await ctx.session.execute(
            select(_contacts.c.id, _contacts.c.email).where(
                _contacts.c.org_id == ctx.org.id, _contacts.c.id.in_(contact_ids)
            )
        )
        names.update({row_id: email for row_id, email in rows if email})

    tokens: list[str] = []
    for ptype, pid, _ in items:
        if ptype is None:
            tokens.append("")
        elif ptype == PartyType.AGENCY.value:
            tokens.append(PartyType.AGENCY.value)
        elif pid is None:
            # Only ``company`` reaches here with no id: "the record's own client".
            tokens.append(_PREFIXES.get(ptype, ""))
        else:
            # A target that no longer exists keeps its id, so the row errors on re-import
            # rather than quietly becoming "no party".
            tokens.append(f"{_PREFIXES.get(ptype, ptype)}:{names.get(pid) or pid}")
    return tokens


# --- import ------------------------------------------------------------------------- #
async def resolve_party(ctx: RequestContext, refs: list[str]) -> dict[str, PartyRef | str]:
    """Batch-resolve party tokens → :class:`PartyRef`, or an i18n key for that row's error.

    The returned ref is handed to the owning service's own ``PartyService.validate``, so the
    tenant-scoping and the "an employee needs an id" rules are enforced in exactly one place —
    this only turns text into the shape a form would have posted.
    """
    wanted: dict[str, list[str]] = {"company": [], "employee": [], "contact": []}
    parsed: dict[str, tuple[str, str] | PartyRef | str] = {}

    for ref in refs:
        token = ref.strip()
        lowered = token.lower()
        if lowered == PartyType.AGENCY.value:
            parsed[ref] = PartyRef(type=PartyType.AGENCY)
            continue
        if lowered == "company":
            parsed[ref] = PartyRef(type=PartyType.COMPANY)  # the record's own client
            continue
        kind, sep, target = token.partition(":")
        kind, target = kind.strip().lower(), target.strip()
        # An unprefixed cell is refused rather than guessed at: "jan@bureau.nl" is a plausible
        # colleague *and* a plausible client contact, and picking one silently would write the
        # wrong kind of party with every row valid (§17's mapping-fingerprint reasoning).
        if not sep or kind not in wanted or not target:
            parsed[ref] = _INVALID
            continue
        parsed[ref] = (kind, target)
        wanted[kind].append(target)

    lookups = {
        "company": await _by_company_name(ctx, wanted["company"]),
        "employee": await _by_member_email(ctx, wanted["employee"]),
        "contact": await _by_contact_email(ctx, wanted["contact"]),
    }
    types = {
        "company": PartyType.COMPANY,
        "employee": PartyType.EMPLOYEE,
        "contact": PartyType.CONTACT,
    }

    resolved: dict[str, PartyRef | str] = {}
    for ref, value in parsed.items():
        if not isinstance(value, tuple):
            resolved[ref] = value
            continue
        kind, target = value
        found = lookups[kind].get(target, _UNRESOLVED)
        resolved[ref] = (
            found if isinstance(found, str) else PartyRef(type=types[kind], id=found)
        )
    return resolved


def _split_ids(refs: list[str]) -> tuple[dict[str, uuid.UUID], list[str]]:
    by_id: dict[str, uuid.UUID] = {}
    rest: list[str] = []
    for ref in refs:
        try:
            by_id[ref] = uuid.UUID(ref)
        except ValueError:
            rest.append(ref)
    return by_id, rest


async def _by_company_name(
    ctx: RequestContext, refs: list[str]
) -> dict[str, uuid.UUID | str]:
    if not refs:
        return {}
    by_id, names = _split_ids(refs)
    resolved: dict[str, uuid.UUID | str] = {}
    if by_id:
        found = set(
            (
                await ctx.session.execute(
                    select(_companies.c.id).where(
                        _companies.c.org_id == ctx.org.id,
                        _companies.c.id.in_(by_id.values()),
                    )
                )
            ).scalars()
        )
        resolved.update(
            {ref: (rid if rid in found else _UNRESOLVED) for ref, rid in by_id.items()}
        )
    if names:
        matches: dict[str, list[uuid.UUID]] = {}
        rows = await ctx.session.execute(
            select(_companies.c.id, _companies.c.name).where(
                _companies.c.org_id == ctx.org.id, _companies.c.name.in_(names)
            )
        )
        for row_id, name in rows:
            matches.setdefault(name, []).append(row_id)
        for name in names:
            ids = matches.get(name, [])
            resolved[name] = (
                ids[0] if len(ids) == 1 else (_UNRESOLVED if not ids else _AMBIGUOUS)
            )
    return resolved


async def _by_member_email(
    ctx: RequestContext, refs: list[str]
) -> dict[str, uuid.UUID | str]:
    if not refs:
        return {}
    by_id, emails = _split_ids(refs)
    members = select(_memberships.c.user_id).where(_memberships.c.org_id == ctx.org.id)
    resolved: dict[str, uuid.UUID | str] = {}
    if by_id:
        found = set(
            (
                await ctx.session.execute(
                    select(_users.c.id).where(
                        _users.c.id.in_(by_id.values()), _users.c.id.in_(members)
                    )
                )
            ).scalars()
        )
        resolved.update(
            {ref: (rid if rid in found else _UNRESOLVED) for ref, rid in by_id.items()}
        )
    if emails:
        rows = await ctx.session.execute(
            select(_users.c.id, _users.c.email).where(
                _users.c.email.in_([e.lower() for e in emails]), _users.c.id.in_(members)
            )
        )
        by_email = {email: row_id for row_id, email in rows}
        for ref in emails:
            resolved[ref] = by_email.get(ref.lower(), _UNRESOLVED)
    return resolved


async def _by_contact_email(
    ctx: RequestContext, refs: list[str]
) -> dict[str, uuid.UUID | str]:
    if not refs:
        return {}
    by_id, emails = _split_ids(refs)
    resolved: dict[str, uuid.UUID | str] = {}
    if by_id:
        found = set(
            (
                await ctx.session.execute(
                    select(_contacts.c.id).where(
                        _contacts.c.org_id == ctx.org.id,
                        _contacts.c.id.in_(by_id.values()),
                    )
                )
            ).scalars()
        )
        resolved.update(
            {ref: (rid if rid in found else _UNRESOLVED) for ref, rid in by_id.items()}
        )
    if emails:
        # Case-insensitively, because ``contacts.email`` is stored as the user typed it (the
        # service only strips — see ContactService.find_by_email, which compares the same way).
        # Matching exactly would mean an export writing "Info@Klant.nl" no longer re-imports.
        matches: dict[str, list[uuid.UUID]] = {}
        lowered = [e.lower() for e in emails]
        rows = await ctx.session.execute(
            select(_contacts.c.id, func.lower(_contacts.c.email)).where(
                _contacts.c.org_id == ctx.org.id,
                func.lower(_contacts.c.email).in_(lowered),
            )
        )
        for row_id, email in rows:
            matches.setdefault(email, []).append(row_id)
        for ref in emails:
            # A client's address book may legitimately hold one address twice; erroring beats
            # attaching the record to whichever row came back first.
            ids = matches.get(ref.lower(), [])
            resolved[ref] = (
                ids[0] if len(ids) == 1 else (_UNRESOLVED if not ids else _AMBIGUOUS)
            )
    return resolved
