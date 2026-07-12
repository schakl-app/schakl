"""Shared FK resolvers for impex descriptors (issue #77).

Each resolver batch-resolves a whole file's raw references (an exact name or a UUID) into
tenant-scoped ids — two grouped queries per column, never one per row. They reference other
modules' tables as **bare tables by name** (the §6 cross-module read idiom the contacts
descriptor established): a resolver is a lookup, not a data path into another module.
"""

from __future__ import annotations

import uuid

from sqlalchemy import column, select, table

from app.core.tenancy import RequestContext

_users = table("users", column("id"), column("email"))
_memberships = table("memberships", column("user_id"), column("org_id"))


def name_or_id_resolver(table_name: str):
    """A resolver over ``table_name`` (id/name/org_id columns): UUID → by id, else exact name.

    A name carried by two rows is ambiguous — erroring beats silently picking one.
    """
    ref_table = table(table_name, column("id"), column("name"), column("org_id"))

    async def resolve(ctx: RequestContext, refs: list[str]) -> dict[str, uuid.UUID | str]:
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
                    select(ref_table.c.id).where(
                        ref_table.c.org_id == ctx.org.id,
                        ref_table.c.id.in_(by_id.values()),
                    )
                )
            ).scalars()
            found = set(rows)
            for ref, ref_id in by_id.items():
                resolved[ref] = ref_id if ref_id in found else "impex.errors.unresolved_reference"
        if names:
            matches: dict[str, list[uuid.UUID]] = {}
            rows = await ctx.session.execute(
                select(ref_table.c.id, ref_table.c.name).where(
                    ref_table.c.org_id == ctx.org.id,
                    ref_table.c.name.in_(names),
                )
            )
            for row_id, name in rows:
                matches.setdefault(name, []).append(row_id)
            for name in names:
                found_ids = matches.get(name, [])
                if len(found_ids) == 1:
                    resolved[name] = found_ids[0]
                elif not found_ids:
                    resolved[name] = "impex.errors.unresolved_reference"
                else:
                    resolved[name] = "impex.errors.ambiguous_match"
        return resolved

    return resolve


async def resolve_member_email(
    ctx: RequestContext, refs: list[str]
) -> dict[str, uuid.UUID | str]:
    """Resolve member references by e-mail (or UUID) — only users with a membership here."""
    by_id: dict[str, uuid.UUID] = {}
    emails: list[str] = []
    for ref in refs:
        try:
            by_id[ref] = uuid.UUID(ref)
        except ValueError:
            emails.append(ref.lower())

    member_ids = select(_memberships.c.user_id).where(_memberships.c.org_id == ctx.org.id)
    resolved: dict[str, uuid.UUID | str] = {}
    if by_id:
        rows = (
            await ctx.session.execute(
                select(_users.c.id).where(
                    _users.c.id.in_(by_id.values()), _users.c.id.in_(member_ids)
                )
            )
        ).scalars()
        found = set(rows)
        for ref, ref_id in by_id.items():
            resolved[ref] = ref_id if ref_id in found else "impex.errors.unresolved_reference"
    if emails:
        rows = await ctx.session.execute(
            select(_users.c.id, _users.c.email).where(
                _users.c.email.in_(emails), _users.c.id.in_(member_ids)
            )
        )
        by_email = {email: row_id for row_id, email in rows}
        for ref in refs:
            lowered = ref.lower()
            if ref not in resolved and lowered in emails:
                resolved[ref] = by_email.get(lowered, "impex.errors.unresolved_reference")
    return resolved
