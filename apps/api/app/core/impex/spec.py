"""Impex descriptors — how a module opts an entity into CSV import/export (issue #77).

The same shape as custom fields (§13) and panels (§6): **core owns the mechanics** — CSV
parsing/serialisation, validation, dry-run, upsert orchestration, the routes — and a module
only **describes its shape** here, declaring it on its :class:`~app.registry.ModuleDescriptor`.
A new attachable module gets import/export by writing one descriptor, no core edits.

Vocabulary:

* An :class:`ImpexColumn` is one CSV column. Headers are the **stable keys**, never localized
  labels, so an export re-imports cleanly into the same org (round-trip). The tenant's custom
  fields (§13) are *not* declared here — core appends them at request time from the definitions,
  keyed by definition ``key``.
* ``natural_key`` names the column the import upserts on (company: ``name``, contact:
  ``email``): a match updates, no match creates. Never a raw ``id`` — ids don't survive a trip
  through a spreadsheet, names and emails do.
* ``fk_resolvers`` turn a human reference (an exact name, or a UUID) into a tenant-scoped id.
  An unresolved or ambiguous reference is a **row error**, never a silent orphan.

Every callable receives the tenant-bound :class:`~app.core.tenancy.RequestContext` and must go
through the module's own service/repository — the descriptor is a shape, not a data path
(Golden Rule 1).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from app.core.tenancy import RequestContext


class FetchPage(Protocol):
    """One page of the entity's **filtered** list — the module's own list service, so an export
    honours exactly the filters/sort the list endpoint does, never a duplicated query."""

    def __call__(
        self, ctx: RequestContext, *, limit: int, offset: int, filters: dict[str, Any]
    ) -> Awaitable[Sequence[Any]]: ...


#: All tenant rows whose natural key is in ``values`` → ``{value: [rows]}``. Returns full rows
#: (not ids): the import needs the current ``custom`` JSONB to merge before validating.
FindExisting = Callable[["RequestContext", list[str]], Awaitable[dict[str, list[Any]]]]

#: Create one entity from the coerced values dict (keys = column ``field``s + ``custom``).
CreateRow = Callable[["RequestContext", dict[str, Any]], Awaitable[None]]

#: Update one existing entity (second arg: a row ``find_existing`` returned).
UpdateRow = Callable[["RequestContext", Any, dict[str, Any]], Awaitable[None]]

#: Batch-resolve raw FK references (exact name or UUID string) → per reference either the
#: resolved tenant-scoped id, or an i18n error key ("impex.errors.unresolved_reference" /
#: "impex.errors.ambiguous_match") that becomes that row's error.
FkResolver = Callable[["RequestContext", list[str]], Awaitable[dict[str, uuid.UUID | str]]]


@dataclass(frozen=True)
class ImpexColumn:
    """One CSV column of an entity's import/export shape.

    ``data_type`` drives core's coercion on import:

    * ``text`` — trimmed string.
    * ``email`` — validated address (row error ``errors.invalid_email`` otherwise).
    * ``select`` — must be one of ``options`` (row error ``impex.errors.invalid_option``).
    * ``date`` — ISO ``YYYY-MM-DD`` (what export writes; row error ``impex.errors.invalid_date``).
    * ``time`` — ``HH:MM`` wall clock (row error ``impex.errors.invalid_time``).
    * ``number`` — decimal, ``.`` or ``,`` separator (row error ``impex.errors.invalid_number``).
    * ``bool`` — ``true``/``false``/``ja``/``nee``/``yes``/``no``/``1``/``0``
      (row error ``impex.errors.invalid_bool``).
    * ``fk`` — raw reference handed to the descriptor's resolver; the resolved id lands in
      ``field`` (e.g. column ``company`` → ``company_id``).

    An **empty cell** on an update clears the field when ``clearable`` (the round-trip rule:
    exporting a NULL writes "", importing "" restores NULL) and leaves it untouched otherwise —
    a non-nullable field like ``status`` cannot be "cleared", and an FK column never unlinks.
    A ``required`` column must be present in the header and non-empty in every row.
    """

    key: str
    data_type: str = "text"
    required: bool = False
    clearable: bool = True
    field: str | None = None
    options: tuple[str, ...] = ()
    #: Export accessor; ``None`` → ``getattr(row, target)``.
    getter: Callable[[Any], Any] | None = None
    #: Exported but never imported (a derived value: worked minutes, an approval flag, the
    #: entry's owner). The import accepts the column in the header — an export must re-import
    #: unchanged (round-trip) — and ignores its cells.
    readonly: bool = False

    @property
    def target(self) -> str:
        """The key this column writes into the values dict (defaults to the CSV key)."""
        return self.field or self.key


@dataclass(frozen=True)
class ImpexDescriptor:
    """Everything core needs to import/export one entity type as CSV."""

    entity_type: str                 # the custom-fields entity slug, e.g. "company"
    read_permission: str             # declared on the export route (§15 deny-by-default)
    write_permission: str            # declared on the import route
    columns: tuple[ImpexColumn, ...]
    #: Column key the upsert matches on; ``None`` = create-only import (no reliable natural
    #: key exists — a time entry, a task title that legitimately repeats).
    natural_key: str | None
    #: Which of the core filter params (see ``router.FILTER_PARAMS``) this entity's list
    #: supports; they mirror the entity's own list endpoint.
    filters: tuple[str, ...]
    fetch_page: FetchPage
    find_existing: FindExisting
    create_row: CreateRow
    update_row: UpdateRow
    fk_resolvers: Mapping[str, FkResolver] = field(default_factory=dict)
    #: False = export-only: no import route is mounted (approval-bearing records like leave
    #: must be requested, never bulk-written).
    importable: bool = True
