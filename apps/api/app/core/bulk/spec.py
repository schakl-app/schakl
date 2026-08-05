"""Bulk edit / delete descriptors — how a module opts an entity into acting on a selection.

The same shape as impex (CLAUDE.md §17), custom fields (§13) and panels (§6): **core owns the
mechanics** — loading the selection in one query, resolving the shared values once, the per-row
savepoint, the routes — and a module only **describes its shape** here, declaring it on its
:class:`~app.registry.ModuleDescriptor`.

It describes very little, because the shape already exists. A bulk edit is "apply these values
to the rows I picked", which is exactly what an import does to the rows it matched — so a
:class:`BulkDescriptor` **borrows its** :class:`~app.core.impex.spec.ImpexDescriptor`: the
column vocabulary (types, options, clearability), the batched reference resolvers, and — the
load-bearing one — ``update_row``, which is the module's own service call. A second write path
is the one way a bulk edit could quietly stop meaning what an edit means: fifty selected rows
must get the same validation, the same activity line, the same events and the same custom-field
rules as fifty visits to the form.

What a module still has to say is what may be set **in bulk**, which is a product judgement no
column can carry. A domain's ``name`` is importable and must never be settable across a
selection; its client is both. Hence ``editable`` is an **allow-list, never a deny-list**, so a
column added to an import tomorrow is not silently bulk-writable today.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from app.core.impex.spec import ImpexColumn, ImpexDescriptor

if TYPE_CHECKING:
    from app.core.tenancy import RequestContext

#: The column types a bulk value may carry.
#:
#: ``phone`` and ``email`` are deliberately absent, and not as an oversight to fix later: both
#: are per-row facts — a national phone number is read in *that row's* country
#: (:mod:`app.core.phone`), an address is unique per contact — so one shared value across a
#: selection is either meaningless or a guaranteed conflict. A name is the same argument in
#: miniature, which is why it is the ``editable`` allow-list rather than the type list that
#: keeps ``name`` out.
BULK_TYPES: tuple[str, ...] = ("text", "select", "bool", "date", "number", "fk", "party")

#: Delete one already-loaded row through the owning module's own service (see the module
#: docstring). Takes the row rather than an id so core never has to know how to find it twice.
DeleteRow = Callable[["RequestContext", Any], Awaitable[None]]

#: ``(ctx, row, values) -> None`` — :data:`app.core.impex.spec.UpdateRow`, deliberately the
#: identical contract, so the descriptor can hand core the import's writer unchanged.
UpdateRow = Callable[["RequestContext", Any, dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True)
class BulkField:
    """One column a bulk edit may set, named by its impex column key.

    ``clearable`` overrides the column's own answer where the two questions genuinely differ.
    An import clears a contact's client by writing an empty cell; a bulk edit offers "set the
    client of these twelve contacts", and "unset the client of these twelve contacts" is a
    different act nobody reached for the same control to do — so the link is settable here and
    not clearable, while the import keeps both. Where the two agree, leave it ``None``.
    """

    key: str
    clearable: bool | None = None


@dataclass(frozen=True)
class BulkDescriptor:
    """Everything core needs to edit or delete a selection of one entity type.

    An entity that has an import shape borrows it and declares almost nothing. One that does
    not — an **invoice** is a numbered document, not a spreadsheet row, and a **contact moment**
    is the record of something that was said — still gets a bulk *delete* by naming its model,
    its permission and its service call. Deleting is not editing: it needs no column vocabulary,
    so requiring one would have kept the two entities where a batch is most obviously wanted
    (a run of draft invoices, a run of mis-logged e-mails) from having it at all.
    """

    #: The ORM model, loaded through ``ctx.repo(model).scoped_select()`` — which is what makes
    #: tenant isolation *and* the company horizon true by construction rather than by a check
    #: every descriptor would have to remember (CLAUDE.md §15).
    model: type[Any]
    #: The import shape this borrows: columns, options, reference resolvers and the writer.
    #: ``None`` for a delete-only entity, which needs none of them.
    impex: ImpexDescriptor | None = None
    #: Required when there is no ``impex`` to take it from; it is the route's path segment.
    entity: str | None = None
    #: The columns a bulk edit may set, in the order the dialog shows them.
    editable: tuple[BulkField, ...] = ()
    #: The entity's own delete permission. ``None`` means this entity has no bulk delete —
    #: no route is mounted, rather than one that always refuses.
    delete_permission: str | None = None
    delete_row: DeleteRow | None = None
    #: Overrides the import's writer where the module has none (tasks import create-only, so
    #: its ``update_row`` is an unreachable ``NotImplementedError``).
    update_row: UpdateRow | None = None
    #: Overrides the import's write permission; required when there is no ``impex``.
    write_permission_override: str | None = None

    @property
    def entity_type(self) -> str:
        name = self.entity or (self.impex.entity_type if self.impex else None)
        if name is None:  # check_descriptor refuses this at import time
            raise RuntimeError("bulk descriptor names no entity")
        return name

    @property
    def write_permission(self) -> str | None:
        return self.write_permission_override or (
            self.impex.write_permission if self.impex else None
        )

    @property
    def resolvers(self) -> Mapping[str, Any]:
        return self.impex.fk_resolvers if self.impex else {}

    @property
    def writer(self) -> UpdateRow | None:
        return self.update_row or (self.impex.update_row if self.impex else None)

    @property
    def columns(self) -> dict[str, ImpexColumn]:
        """The editable columns by key, with :class:`BulkField`'s overrides applied."""
        by_key = {column.key: column for column in self.impex.columns} if self.impex else {}
        out: dict[str, ImpexColumn] = {}
        for spec in self.editable:
            column = by_key[spec.key]
            out[spec.key] = (
                column if spec.clearable is None else replace(column, clearable=spec.clearable)
            )
        return out


def check_descriptor(descriptor: BulkDescriptor) -> None:
    """Fail at **import time** on a descriptor that cannot work — never at request time.

    An ``editable`` key that names no column, or names a derived one, is a code bug in the
    module that declared it, and a code bug should stop the app coming up rather than surface
    as one tenant's confusing 500 halfway through a batch. This is ``impex.router``'s
    ``_check_extensions``, applied to the same kind of contribution.
    """
    if descriptor.entity is None and descriptor.impex is None:
        raise RuntimeError(f"bulk: {descriptor.model!r} names neither an entity nor an import")
    where = f"bulk: {descriptor.entity_type!r}"
    if not descriptor.editable and descriptor.delete_permission is None:
        raise RuntimeError(f"{where} declares neither an editable column nor a delete")
    if descriptor.editable:
        # The columns, the resolvers and the writer all come from the import shape; an editable
        # entity without one would be declaring a vocabulary nothing can read.
        if descriptor.impex is None:
            raise RuntimeError(f"{where} declares editable columns but borrows no import shape")
        if descriptor.write_permission is None:
            raise RuntimeError(f"{where} declares editable columns but no write permission")
        if descriptor.writer is None:
            raise RuntimeError(f"{where} declares editable columns but no writer")
    by_key = {column.key: column for column in descriptor.impex.columns} if descriptor.impex else {}
    seen: set[str] = set()
    for spec in descriptor.editable:
        column = by_key.get(spec.key)
        if column is None:
            raise RuntimeError(f"{where} declares editable column {spec.key!r}, which it has no")
        if spec.key in seen:
            raise RuntimeError(f"{where} declares editable column {spec.key!r} twice")
        seen.add(spec.key)
        if column.readonly:
            raise RuntimeError(
                f"{where} declares editable column {spec.key!r}, which the import exports "
                "read-only (a derived value is not something a selection can be set to)"
            )
        if column.data_type not in BULK_TYPES:
            raise RuntimeError(
                f"{where} declares editable column {spec.key!r} of type "
                f"{column.data_type!r}, which is per-row and cannot be shared by a selection"
            )
        if column.data_type in ("fk", "party") and spec.key not in descriptor.resolvers:
            raise RuntimeError(
                f"{where} declares editable reference column {spec.key!r} with no resolver on "
                "the import descriptor — it would refuse every value it was given"
            )
    if (descriptor.delete_permission is None) != (descriptor.delete_row is None):
        raise RuntimeError(f"{where} declares only one half of its delete (permission + row)")
