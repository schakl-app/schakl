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
* ``natural_keys`` names the columns the import upserts on, **in priority order** (company:
  ``client_number`` then ``name``): the first one a row actually fills decides that row's
  match — a hit updates, a miss creates. Never a raw ``id``: ids don't survive a trip through a
  spreadsheet, klantnummers and emails do. Priority order matters because the keys are not
  equally stable — a company can be renamed but keeps its number, so a file carrying both must
  match on the number or the rename imports as a second company.
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


#: All tenant rows whose ``key`` column (one of ``natural_keys``) is in ``values`` →
#: ``{value: [rows]}``. Takes the key because the query genuinely differs per key
#: (``client_number IN …`` is not ``name IN …``) and a resolver must never have to infer which
#: one it was handed from the shape of the values. Returns full rows (not ids): the import needs
#: the current ``custom`` JSONB to merge before validating.
FindExisting = Callable[["RequestContext", str, list[str]], Awaitable[dict[str, list[Any]]]]

#: Create one entity from the coerced values dict (keys = column ``field``s + ``custom``) and
#: **return it**. The created row is what an :class:`ImpexExtension` attaches to, so a create
#: that returns ``None`` silently drops every contributed column.
CreateRow = Callable[["RequestContext", dict[str, Any]], Awaitable[Any]]

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
    #: i18n key for the mapping step's label; ``None`` → ``impex.column.<entity>.<key>``. The
    #: **header** is always the stable key — this is only ever what a human is shown.
    label_key: str | None = None
    #: Header spellings this column is recognised by when *suggesting* a mapping — nl and en,
    #: lowercased. Aliases never widen the header-key contract: an unmapped import still
    #: accepts only real keys, so a file that used to fail still fails the same way and an
    #: export still round-trips exactly. They exist so the wizard pre-fills correctly.
    aliases: tuple[str, ...] = ()

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
    #: Column keys the upsert matches on, most stable first; empty = create-only import (no
    #: reliable natural key exists — a time entry, a task title that legitimately repeats).
    natural_keys: tuple[str, ...]
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


#: Write the extension's own columns for one imported host row. Runs **inside the import's
#: transaction**, right after the host was created or updated, so the host and everything
#: contributed to it commit or roll back together.
ApplyExtension = Callable[["RequestContext", Any, dict[str, Any]], Awaitable[None]]

#: Load whatever the extension's ``getter``s read, for a whole export page at once. Without
#: it an export that carries contributed columns goes N+1 (docs/PERFORMANCE.md).
HydrateExtension = Callable[["RequestContext", Sequence[Any]], Awaitable[None]]


@dataclass(frozen=True)
class ImpexExtension:
    """Columns one module contributes to **another** module's entity import/export.

    The company import wants the client's contact person in the same row — but a company must
    not import a contact's internals (§6). So contacts *contributes* those columns to the
    company shape, exactly as ``panels.py`` contributes a panel to the company page: core
    resolves descriptor → extensions → custom fields into one flat column list, and the only
    place the difference survives is at write time, where the values are handed back to the
    contributing module's own service.

    Two rules that are not negotiable, because they are what keep the seam honest:

    * ``apply`` **never runs on a dry run.** A preview cannot execute another module's writes,
      so anything that could fail inside ``apply`` must be expressible on the columns
      themselves (type, options) — a row that will fail there previews clean.
    * **No contributed column may be ``required``.** Contacts has no business making
      ``contact_email`` mandatory on every company import. Asserted at mount time.
    """

    entity_type: str                   # the *host* entity, e.g. "company"
    module: str                        # the contributor — namespaces and groups the UI
    #: Keys are already namespaced by the contributor (``contact_email``, never ``email``);
    #: a collision with the host's own keys is a mount-time error, not a request-time surprise.
    columns: tuple[ImpexColumn, ...]
    #: The contributor's own gates, **all** of them. A caller missing any never sees these
    #: columns rather than hitting a mid-import 403 that rolls the whole file back.
    write_permissions: tuple[str, ...]
    apply: ApplyExtension
    hydrate: HydrateExtension | None = None
    position: int = 100
