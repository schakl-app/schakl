"""How a module describes what deleting one of its records *means* (docs/TRASH.md).

Two contributions, both the panels pattern (CLAUDE.md §6): core owns the mechanics — the
columns, the hidden-everywhere predicate, the routes, the retention sweep — and a module only
**describes its shape**.

* The **owning** module declares a :class:`TrashableSpec` on its ``ModuleDescriptor``: which
  model, which permission the act rides, how a row is named on the trash screen.
* Every **other** module that holds rows *about* that entity registers a :class:`TrashDependent`
  — "this client has 12 issued invoices" — and says whether that **blocks** the delete or merely
  **goes with it**. Core composes the counts into the confirmation dialog, the refusal and the
  purge, and names no module.

The block/goes-with split is the whole design, so it is worth stating once. A record that must
outlive the client in the real world or in the books — an issued invoice, a domain the agency
still pays a registrar for, an agreement with billing history, a project, logged hours — is a
**blocker**: a client holding one cannot be deleted, only archived, and the dialog says so with
the numbers and offers the archive. What is *not* a record (a to-do, a link to a contact person,
a draft) goes with the client, is named in the dialog with its count, hides while the client is
in the trash, and is gone when the client is purged. "Somebody deleted the client" must never be
how a bookkeeper learns an invoice is missing (§10, payments: gate what the agency does, never
rewrite what has already happened to them).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from app.core.tenancy import RequestContext

#: ``(session, org_id, entity_ids) -> {entity_id: count}`` — batched, because the trash screen
#: asks about a page of rows at once and the dialog about one; a per-row query would be N×M.
Counter = Callable[[AsyncSession, uuid.UUID, Sequence[uuid.UUID]], Awaitable[dict[uuid.UUID, int]]]

#: ``(ctx, entity_id) -> None`` — what a contributing module does with its rows the moment
#: before the record is purged. Only for what a database cascade cannot do *well*: a cascade
#: deletes a task's planned blocks and tells the Google mirror nothing, so the tasks module
#: takes its tasks out through its own schedule service instead. Most dependents need none.
Purger = Callable[["RequestContext", uuid.UUID], Awaitable[None]]


@dataclass(frozen=True)
class TrashDependent:
    """Rows another module holds about a trashable record, and what they mean for its delete."""

    #: Namespaced by the contributor (``invoicing.invoices_issued``) — asserted at registration.
    key: str
    #: i18n key of the counted noun (``trash.dependent.invoicing.invoices_issued``, with a
    #: ``_one`` sibling): the web prints it with the count, the API never picks a locale.
    label_key: str
    #: ``True``: while the count is non-zero the record can be neither trashed nor purged.
    blocks: bool
    count: Counter
    purge: Purger | None = None


@dataclass(frozen=True)
class TrashableSpec:
    """One entity a module lets the tenant put in the trash."""

    #: ``__entity_type__`` of the model — the slug the routes, the trail and the web all use.
    entity_type: str
    model: type[Any]
    #: The act is the entity's own delete — trashing, restoring and purging all ride it. No new
    #: capability (the bulk router's reasoning): a trash is what *delete* means now, and the
    #: person who could delete a row is the person who may undo it.
    delete_permission: str
    #: How a row is named on the trash screen and in the trail's ``purged`` line.
    label: Callable[[Any], str]


_dependents: dict[str, list[TrashDependent]] = {}


def register_trash_dependent(entity_type: str, dependent: TrashDependent) -> None:
    """Called from a contributing module's package ``__init__`` (the ``subscribe`` shape)."""
    if "." not in dependent.key:
        raise ValueError(f"trash dependent key must be namespaced by its module: {dependent.key!r}")
    bucket = _dependents.setdefault(entity_type, [])
    if any(existing.key == dependent.key for existing in bucket):
        return  # idempotent under a re-import, exactly as the registry's other seams are
    bucket.append(dependent)


def dependents_of(entity_type: str) -> list[TrashDependent]:
    return list(_dependents.get(entity_type, ()))


def count_by_column(table: str, column: str, *, where: str = "") -> Counter:
    """The one query almost every dependent is: ``count(*)`` per anchor id over the module's
    own table. Tenant-scoped by ``org_id`` (and by RLS underneath), grouped so a page of trash
    rows costs one round trip per dependent rather than one per row.

    ``table``/``column``/``where`` are the contributor's own literals, never caller input."""

    sql = text(
        f"SELECT {column} AS anchor, count(*) AS n FROM {table} "  # noqa: S608 - module literals
        f"WHERE org_id = :oid AND {column} IN :ids {where} GROUP BY {column}"
    ).bindparams(bindparam("ids", expanding=True))

    async def count(
        session: AsyncSession, org_id: uuid.UUID, ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, int]:
        if not ids:
            return {}
        rows = await session.execute(sql, {"oid": org_id, "ids": list(ids)})
        return {row.anchor: int(row.n) for row in rows}

    return count
