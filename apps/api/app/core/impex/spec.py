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

#: Batch-resolve raw references (an exact name, an e-mail, a UUID string, a party token) → per
#: reference either the resolved value, or an i18n error key
#: ("impex.errors.unresolved_reference" / "impex.errors.ambiguous_match") that becomes that
#: row's error.
#:
#: **A str return is always an error**, anything else is a success — that is the whole contract,
#: and it is what lets a resolver hand back something other than an id (a ``party`` column
#: resolves to a :class:`~app.core.party.schemas.PartyRef`, which the owning service then
#: validates as it would one from a form) without core learning the shape.
FkResolver = Callable[["RequestContext", list[str]], Awaitable[dict[str, Any]]]

#: A module's **cross-column** pre-check for one row: ``(ctx, values, existing)`` → the row's
#: errors as ``(column key, i18n key)`` pairs, empty when the row is fine. ``existing`` is the
#: row the upsert matched (``None`` for a create), so a rule may depend on what is already
#: stored — "you may not lower a registered payment" needs the current figure.
#:
#: It exists for the same reason ``data_type="phone"`` does (#289): the engine's import is
#: all-or-nothing, so a check that lives only in ``create_row`` fails as one request-level
#: 422 naming no row, after the report was built. A rule stated here runs in the plan phase,
#: on the dry run and the commit alike, and the preview names the row and the column. Being a
#: *pre*-check it must never reject what the write would accept — share the rule with the
#: service (call the same function) rather than restating it.
RowValidator = Callable[
    ["RequestContext", dict[str, Any], Any], Awaitable[Sequence[tuple[str, str]]]
]


@dataclass(frozen=True)
class ImpexColumn:
    """One CSV column of an entity's import/export shape.

    ``data_type`` drives core's coercion on import:

    * ``text`` — trimmed string.
    * ``email`` — validated address (row error ``errors.invalid_email`` otherwise).
    * ``phone`` — normalised to E.164 (row error ``errors.invalid_phone`` otherwise), read in
      ``region_field``'s country or the org's (see :mod:`app.core.phone`).
    * ``select`` — must be one of ``options`` (row error ``impex.errors.invalid_option``).
    * ``date`` — ISO ``YYYY-MM-DD`` (what export writes; row error ``impex.errors.invalid_date``).
    * ``time`` — ``HH:MM`` wall clock (row error ``impex.errors.invalid_time``).
    * ``number`` — decimal, ``.`` or ``,`` separator (row error ``impex.errors.invalid_number``).
    * ``bool`` — ``true``/``false``/``ja``/``nee``/``yes``/``no``/``1``/``0``
      (row error ``impex.errors.invalid_bool``).
    * ``fk`` — raw reference handed to the descriptor's resolver; the resolved id lands in
      ``field`` (e.g. column ``company`` → ``company_id``).
    * ``party`` — the same, for a :mod:`app.core.party` reference: the cell is a token
      (``agency``, ``company``, ``employee:jan@bureau.nl``, ``contact:info@klant.nl``,
      ``company:Acme``) and what lands in ``field`` is a ``PartyRef`` the owning service
      validates exactly as it would one from the form. Unlike ``fk`` it *is* clearable —
      "no contact set" is a real state, "no company" for a domain is not.

    An **empty cell** on an update clears the field when ``clearable`` (the round-trip rule:
    exporting a NULL writes "", importing "" restores NULL) and leaves it untouched otherwise —
    a non-nullable field like ``status`` cannot be "cleared". This holds for references too:
    whether an emptied cell *detaches* is a property of the link, not of it being a link. A
    hosting record with no client is shared infrastructure, a real state the file must be able
    to express; a domain with no client is nonsense, so its column says ``clearable=False``.
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
    #: **header** is always the stable key — this is only ever what a human is shown. It is also
    #: what the wizard *recognises* a header by, in every locale the instance ships, so a column
    #: needs no hand-written ``aliases`` to be found by its own Dutch or English name.
    label_key: str | None = None
    #: ``select`` only: where this column's **options** are named, as a template over the option
    #: value (``"domains.status.{option}"``). An export always writes the canonical value and an
    #: import always accepts it, but a sheet a human typed says "Geparkeerd" or "Parked", not
    #: "parked" — so the coercion also accepts every locale's label for an option, and stores
    #: the canonical value either way. ``None`` → only the values themselves (case-insensitively).
    option_label_key: str | None = None
    #: ``phone`` only: the **values key** naming the country a *national* number in this row
    #: belongs to (companies: their own ``country``). ``None`` reads it in the org's country.
    #: Mirrors the owning service's own rule — the row's own country wins, the org's is the
    #: fallback — because a preview that resolved a different region than the write would
    #: either pass a row the write then rejects, or store a Belgian number as a Dutch one.
    region_field: str | None = None
    #: Header spellings this column is recognised by when *suggesting* a mapping — nl and en,
    #: lowercased. Aliases never widen the header-key contract: an unmapped import still
    #: accepts only real keys, so a file that used to fail still fails the same way and an
    #: export still round-trips exactly. They exist so the wizard pre-fills correctly.
    aliases: tuple[str, ...] = ()

    @property
    def target(self) -> str:
        """The key this column writes into the values dict (defaults to the CSV key)."""
        return self.field or self.key


def locale_label_columns(
    source: str = "label_i18n",
    *,
    prefix: str = "label",
    aliases: Mapping[str, tuple[str, ...]] | None = None,
) -> tuple[ImpexColumn, ...]:
    """One column per locale the instance ships, for a tenant's ``label_i18n`` dict.

    Tenant-defined catalogs (subscription types, and every ``label_i18n`` catalog after them)
    carry their labels per locale, and a CSV cell holds one value — so the shape is one column
    per locale (``label_nl``, ``label_en``), never a single "label" whose meaning depends on
    who exported it.

    The locales come from :data:`app.config.settings.supported_locales`, so §8's promise holds
    here too: adding a locale is adding a JSON file plus that setting, and the import/export
    grows the column by itself. The descriptor reassembles the dict from the ``<prefix>_<locale>``
    values it gets back — core never learns which catalog it is writing to.
    """
    from app.config import settings

    by_locale = aliases or {}
    return tuple(
        ImpexColumn(
            f"{prefix}_{locale}",
            getter=lambda row, loc=locale: (getattr(row, source, None) or {}).get(loc),
            aliases=by_locale.get(locale, ()),
        )
        for locale in settings.supported_locales
    )


def merge_locale_labels(
    values: Mapping[str, Any], current: Mapping[str, str] | None = None, *, prefix: str = "label"
) -> dict[str, str] | None:
    """The inverse of :func:`locale_label_columns`: ``{label_nl: …}`` → ``{"nl": …}``.

    Merged over ``current`` so a file carrying only ``label_nl`` edits the Dutch label and
    leaves the English one alone — the same "an absent column is not an empty value" rule the
    whole engine runs on. Returns ``None`` when the file carried no label column at all, which
    the caller reads as "don't touch the labels".
    """
    from app.config import settings

    present = {
        locale: values[f"{prefix}_{locale}"]
        for locale in settings.supported_locales
        if f"{prefix}_{locale}" in values
    }
    if not present:
        return None
    merged = dict(current or {})
    for locale, label in present.items():
        if label:
            merged[locale] = label
        else:
            merged.pop(locale, None)  # an emptied cell removes that locale's label
    return merged


@dataclass(frozen=True)
class ImpexDescriptor:
    """Everything core needs to import/export one entity type as CSV."""

    entity_type: str                 # the custom-fields entity slug, e.g. "company"
    read_permission: str             # declared on the export route (§15 deny-by-default)
    write_permission: str            # declared on the import route
    columns: tuple[ImpexColumn, ...]
    #: Column keys the upsert matches on, most stable first; empty = create-only import (no
    #: reliable natural key exists — a time entry, a task title that legitimately repeats).
    #: A ``fk``/``party`` column may be named here: ``find_existing`` then receives the raw
    #: cells (``example.nl``), not the ids they resolve to.
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
    #: Optional cross-column pre-check per row (see :data:`RowValidator`). Runs after the
    #: column coercions, the FK resolution and the custom-field check, only on rows that have
    #: no error yet — a rule over values that failed to parse would report the same cell twice.
    validate_row: RowValidator | None = None


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
