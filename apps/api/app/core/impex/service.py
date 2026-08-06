"""The impex engine (issue #77): CSV export, and validated dry-run/commit import.

Core owns every mechanic — modules only hand in an :class:`ImpexDescriptor`. Two hard rules:

* **Everything goes through the module's own tenant-scoped service.** ``fetch_page`` is the
  module's list (same filters, same sort, same org scoping); ``create_row``/``update_row`` are
  its real write path, so an imported row fires the same validation, events and side effects a
  form submit would. Import is not a backdoor around the service layer.
* **The API stays the authority on validity.** Every row is validated — column types, required
  (built-in *and* the tenant's required custom fields, via the §13 validator), select options,
  FK resolution — before anything is written. ``dry_run=false`` is all-or-nothing: one request,
  one transaction (``require_context`` commits or rolls back the lot), and a report with errors
  means nothing was applied.

Reading the bytes — CSV, TSV, a pasted block, Excel — is :mod:`app.core.impex.parsing`, which
also owns every size cap (:data:`~app.core.impex.parsing.MAX_IMPORT_ROWS` data rows per
request). Larger files belong to a background ARQ job with progress + a result report —
explicitly deferred (issue #77 phase note); the cap is what keeps the synchronous path honest
until that lands.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from functools import cache
from typing import Any

from fastapi.responses import Response
from pydantic import EmailStr, TypeAdapter, ValidationError

from app.config import settings
from app.core.customfields.models import CustomFieldDefinition
from app.core.customfields.service import CustomFieldsService
from app.core.impex.parsing import ParsedTable, parse_source
from app.core.impex.schemas import (
    ImpexColumnInfo,
    ImpexColumnsResponse,
    ImpexInspectReport,
    ImpexSourceColumn,
    ImportReport,
    ImportRowError,
)
from app.core.impex.spec import ImpexColumn, ImpexDescriptor, ImpexExtension
from app.core.phone import normalize_phone
from app.core.region import is_valid_country, org_default_country
from app.core.tenancy import RequestContext
from app.errors import AppError
from app.i18n import translations

#: Page size for the export's batched fetch through the module's list service.
EXPORT_PAGE_SIZE = 500
#: Row errors returned per report; ``error_count`` always carries the full number.
ERRORS_RETURNED = 50
#: Multi-select custom values join/split on this in a CSV cell.
MULTI_VALUE_SEPARATOR = "|"
#: Data rows scanned for the mapping step's sample cells, and samples kept per column. The
#: samples are what make a wrong encoding or a shifted column obvious before anything is
#: written — three from the first ten rows is enough to see, and cheap enough to always show.
SAMPLE_ROWS = 10
SAMPLE_VALUES = 3
#: Data types whose cell is a *reference* — resolved once per file by the descriptor's resolver
#: rather than coerced in place, because resolution costs a query. ``fk`` yields an id;
#: ``party`` yields a whole :class:`~app.core.party.schemas.PartyRef`. They share the machinery
#: because the batching, the row-level error and the "never a silent orphan" rule are identical;
#: they differ only in what the resolver hands back, which is the resolver's business.
_REFERENCE_TYPES = ("fk", "party")


def _fingerprint(raw: bytes) -> str:
    """Identify the inspected bytes so the import can refuse a *different* file.

    A mapping addresses columns by position, so mapping one file and importing another writes
    the wrong columns into the right fields — every row valid, every value wrong. Truncated:
    this is a change detector, not a secret.
    """
    return hashlib.sha256(raw).hexdigest()[:32]


def _normalise(header: str) -> str:
    """Fold a header or a cell for recognition: case, spacing and punctuation are not signal.

    "Naam", "naam", "NAAM " and "Naam:" are one column to a human, and the mapping step is
    only ever *suggesting* — the strict key contract lives in :meth:`_header_columns`.
    """
    return re.sub(r"[^a-z0-9]+", "", header.strip().lower())


@cache
def _select_vocabulary(
    options: tuple[str, ...], option_label_key: str | None
) -> dict[str, str]:
    """Folded cell → canonical option: every spelling of a ``select`` value we accept.

    The canonical values are laid down **first** so a locale label can never shadow another
    option's own value, then each locale's label for each option (§8's catalogs, read through
    :func:`app.i18n.translations`). What is stored is always the canonical value, so a file that
    said "Geparkeerd" and a file that said "parked" import identically and both export back as
    ``parked`` — the round-trip rule is untouched, only the set of inputs it forgives is wider.

    Cached on the arguments, which are the column's own frozen fields: the catalogs are
    themselves ``lru_cache``d and an entity's option list does not change within a process.
    """
    vocabulary = {_normalise(option): option for option in options}
    if option_label_key:
        for option in options:
            for label in translations(option_label_key.format(option=option)):
                vocabulary.setdefault(_normalise(label), option)
    return vocabulary

_email_adapter: TypeAdapter[str] = TypeAdapter(EmailStr)

_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")
_TRUE_WORDS = frozenset({"true", "ja", "yes", "1"})
_FALSE_WORDS = frozenset({"false", "nee", "no", "0"})


#: Leading characters a spreadsheet treats as the start of a formula/DDE. A text cell beginning
#: with one is prefixed with an apostrophe so Excel/LibreOffice render it as text, not execute it
#: (audit F10 — CSV injection). Only *text* is neutralised; numbers/dates format as themselves.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _neutralize(text: str) -> str:
    return f"'{text}" if text[:1] in _FORMULA_PREFIXES else text


def _cell(value: Any) -> str:
    """Serialize one value for a CSV cell: ISO dates, plain numbers, ``true``/``false``."""
    if value is None:
        return ""
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, list):
        return _neutralize(MULTI_VALUE_SEPARATOR.join(str(item) for item in value))
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, str):
        return _neutralize(value)
    return str(value)


@dataclass
class _Row:
    """One parsed data row on its way through validation."""

    index: int                                    # 1-based data row number (header is 0)
    values: dict[str, Any] = field(default_factory=dict)
    fk: dict[str, tuple[ImpexColumn, str]] = field(default_factory=dict)
    #: ``{column key: (column, contributing module or None, raw cell)}`` — held back until the
    #: whole row is parsed and its upsert target known, because both are inputs: the region
    #: comes from a *sibling* column the file may list after this one, and an unchanged value
    #: on an existing row is grandfathered (:meth:`ImpexService._normalize_phones`).
    phone: dict[str, tuple[ImpexColumn, str | None, str]] = field(default_factory=dict)
    custom: dict[str, str] = field(default_factory=dict)  # raw cells, "" = clear
    #: Coerced values destined for a contributing module, keyed by extension module name.
    #: Kept apart from ``values`` because they are written by a *different* service, after the
    #: host row exists.
    extension: dict[str, dict[str, Any]] = field(default_factory=dict)
    errors: list[tuple[str | None, str]] = field(default_factory=list)
    nk: str | None = None
    #: Which of the descriptor's ``natural_keys`` this row matched on — rows in one file may
    #: legitimately use different ones (a klantnummer here, a bare name there).
    nk_key: str | None = None
    nk_duplicate: bool = False
    ambiguous: bool = False


@dataclass(frozen=True)
class _Target:
    """One column an import can write into, whatever kind it is.

    The point of this type is that everything upstream of the write — header checking, mapping
    validation, coercion, FK resolution, the natural key — treats a tenant's custom field, a
    contributed contact column and the entity's own ``name`` identically. The three only part
    company at the moment a value is actually stored.
    """

    key: str
    source: str                                   # builtin | extension | custom
    column: ImpexColumn | None = None
    definition: CustomFieldDefinition | None = None
    extension: ImpexExtension | None = None

    @property
    def module(self) -> str | None:
        return self.extension.module if self.extension else None


class ImpexService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.custom_fields = CustomFieldsService(ctx)

    # ------------------------------------------------------------------ #
    # Export
    # ------------------------------------------------------------------ #
    async def export_csv(self, d: ImpexDescriptor, filters: dict[str, Any]) -> Response:
        """The **whole** filtered list as CSV — not just a page — via the module's list service.

        Headers are the stable column keys plus the tenant's custom-field keys, so the file
        re-imports into the same org unchanged (round-trip). UTF-8 with BOM: without it Excel
        guesses a legacy codepage and mangles every accented name.
        """
        # Both gates, mirroring the route (defence-in-depth): bulk capability *and* the
        # entity's own read permission.
        self.ctx.require("impex.export")
        self.ctx.require(d.read_permission)
        targets = await self._targets(d)
        extensions = self._extensions(d)

        rows: list[Any] = []
        offset = 0
        while True:
            page = await d.fetch_page(
                self.ctx, limit=EXPORT_PAGE_SIZE, offset=offset, filters=filters
            )
            rows.extend(page)
            if len(page) < EXPORT_PAGE_SIZE:
                break
            offset += EXPORT_PAGE_SIZE

        for extension in extensions:
            if extension.hydrate:
                # Once for the whole export, never once per row: the host's list service has
                # no idea the contributor's columns exist (docs/PERFORMANCE.md).
                await extension.hydrate(self.ctx, rows)

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([target.key for target in targets])
        for row in rows:
            writer.writerow(_cell(self._export_value(target, row)) for target in targets)

        return Response(
            content=("\ufeff" + buffer.getvalue()).encode("utf-8"),  # BOM: Excel-safe UTF-8
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{d.entity_type}-export.csv"'
            },
        )

    # ------------------------------------------------------------------ #
    # Import
    # ------------------------------------------------------------------ #
    async def import_csv(
        self,
        d: ImpexDescriptor,
        raw: bytes,
        *,
        dry_run: bool,
        sheet: str | None = None,
        pasted: bool = False,
        has_header: bool = True,
        mapping: str | None = None,
        match_key: str | None = None,
        fingerprint: str | None = None,
    ) -> ImportReport:
        """Validate a file against the entity's shape, then (unless ``dry_run``) apply it.

        **Without ``mapping`` this behaves exactly as it did before column mapping existed**:
        the header must be the stable keys, an unknown one is a fatal header error, aliases are
        not consulted. That is deliberate — it is what keeps an export round-tripping, keeps
        the existing tests meaningful, and keeps the API surface a caller already automated
        against from shifting under them. Mapping is opt-in, and opting in is explicit.
        """
        self.ctx.require("impex.import")  # the route declares both too; defence-in-depth
        self.ctx.require(d.write_permission)
        if not d.importable:
            # Defensive: the router doesn't even mount an import route for these.
            raise AppError("not_found", "errors.not_found", status_code=404)
        table = parse_source(raw, sheet=sheet, pasted=pasted, has_header=has_header)
        if fingerprint and fingerprint != _fingerprint(raw):
            # The mapping is by position: mapping one file and importing another writes the
            # wrong columns into the right fields, with no error anywhere.
            raise AppError("source_changed", "impex.errors.source_changed", status_code=409)

        targets = await self._targets(d)
        by_key = {target.key: target for target in targets}
        defs = [target.definition for target in targets if target.definition]

        # Before the mapping, so a nonsense ``match_key`` is a 422 in its own right rather than
        # arriving as "that column is missing" — the caller asked for something impossible, and
        # a row-level report would send them looking at their file for a bug in their request.
        natural_keys = self._natural_keys(d, by_key, match_key)
        columns, setup_errors = (
            self._map_columns(table, mapping, by_key, match_key)
            if mapping is not None
            else self._header_columns(table, by_key)
        )
        if setup_errors:
            # A broken header or mapping makes per-row results meaningless (the missing column
            # may be the natural key), so report it alone rather than 2000 misleading rows.
            return ImportReport(
                dry_run=dry_run,
                rows=len(table.rows),
                creates=0,
                updates=0,
                error_count=len(setup_errors),
                errors=setup_errors[:ERRORS_RETURNED],
                applied=False,
            )

        rows = [
            self._parse_row(index, cells, columns)
            for index, cells in enumerate(table.rows, 1)
        ]

        existing: dict[str, dict[str, list[Any]]] = {}
        if natural_keys:
            self._mark_natural_keys(natural_keys, rows, by_key)
            existing = await self._find_existing(d, rows)
        # else: create-only entity (no reliable natural key) — every valid row creates.
        fk_resolved = await self._resolve_fks(d, rows)

        resolved = [(row, self._plan_row(d, row, existing, fk_resolved)) for row in rows]
        self._mark_duplicate_targets(resolved)

        custom_keys = {target.key for target in targets if target.source == "custom"}
        default_region = await self._default_region(rows)
        errors: list[ImportRowError] = []
        plans: list[tuple[str, Any, _Row]] = []
        creates = updates = 0
        for row, entity in resolved:
            self._normalize_phones(row, entity, default_region)
            self._validate_custom(row, defs, custom_keys, entity)
            if row.errors:
                errors.extend(
                    ImportRowError(row=row.index, field=f, message_key=key)
                    for f, key in row.errors
                )
            elif entity is not None:
                updates += 1
                plans.append(("update", entity, row))
            else:
                creates += 1
                plans.append(("create", None, row))

        applied = False
        if not dry_run and not errors:
            # All-or-nothing: everything below runs in this request's transaction, so a failure
            # anywhere (an event handler, a unique index, a contributed write) rolls the whole
            # import back.
            extensions = {e.module: e for e in self._extensions(d)}
            for mode, entity, row in plans:
                if mode == "create":
                    entity = await d.create_row(self.ctx, row.values)
                else:
                    await d.update_row(self.ctx, entity, row.values)
                for module, values in row.extension.items():
                    if not values:
                        continue  # the row carried nothing for this contributor
                    # Only ever after the host row exists, and only through the contributing
                    # module's own service — the host never learns the contributor's internals.
                    await extensions[module].apply(self.ctx, entity, values)
            applied = True

        return ImportReport(
            dry_run=dry_run,
            rows=len(table.rows),
            creates=creates,
            updates=updates,
            error_count=len(errors),
            errors=errors[:ERRORS_RETURNED],
            applied=applied,
        )

    # ------------------------------------------------------------------ #
    # Columns & inspection
    # ------------------------------------------------------------------ #
    async def columns_for(self, d: ImpexDescriptor) -> ImpexColumnsResponse:
        """Every column this caller may map into, in the order the UI should offer them."""
        self.ctx.require(d.read_permission)
        targets = await self._targets(d)
        return ImpexColumnsResponse(
            entity_type=d.entity_type,
            importable=d.importable,
            natural_keys=[key for key in d.natural_keys if key in {t.key for t in targets}],
            columns=[self._column_info(d, target) for target in targets],
        )

    async def inspect(
        self,
        d: ImpexDescriptor,
        raw: bytes,
        *,
        sheet: str | None = None,
        pasted: bool = False,
        has_header: bool = True,
    ) -> ImpexInspectReport:
        """What the uploaded file is, and which column probably goes where.

        Reads the upload and compares it with the entity's column catalog; touches no tenant
        rows. Suggestions are a convenience — nothing here decides what is written, and a
        suggestion the user leaves alone still travels the same validation as one they typed.
        """
        self.ctx.require("impex.import")
        self.ctx.require(d.write_permission)
        table = parse_source(raw, sheet=sheet, pasted=pasted, has_header=has_header)
        targets = await self._targets(d)
        columns = self._suggest(d, table, targets)

        suggested = {column.suggested_key for column in columns}
        return ImpexInspectReport(
            source_format=table.source_format,
            delimiter=table.delimiter,
            encoding=table.encoding,
            sheet=table.sheet,
            sheets=list(table.sheets),
            rows=len(table.rows),
            uncalculated_formulas=table.uncalculated_formulas,
            fingerprint=_fingerprint(raw),
            columns=columns,
            missing_required=[
                target.key
                for target in targets
                if self._required(target) and target.key not in suggested
            ],
            suggested_match_key=next(
                (key for key in d.natural_keys if key in suggested), None
            ),
        )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _extensions(self, d: ImpexDescriptor) -> list[ImpexExtension]:
        """Contributed column sets this caller may actually write.

        Filtered on the contributor's **own** permissions, and filtered *here* rather than at
        write time: a caller who cannot write contacts never sees the contact columns, instead
        of discovering it as a 403 halfway through a commit that then rolls the file back.
        The export header is caller-dependent for the same reason, and by the same rule.
        """
        from app.registry import registry

        return [
            extension
            for extension in registry.impex_extensions_for(
                d.entity_type, settings.enabled_modules
            )
            if all(self.ctx.can(permission) for permission in extension.write_permissions)
        ]

    async def _targets(self, d: ImpexDescriptor) -> list[_Target]:
        """The entity's full column vocabulary: its own, contributed, then the tenant's.

        One resolution point for both directions and every caller, so header checking,
        coercion and FK resolution need no idea which kind a column is. A later key wins
        nothing: a collision with a built-in key is dropped rather than shadowing it, because
        two columns under one header cannot round-trip.
        """
        targets = [_Target(key=c.key, source="builtin", column=c) for c in d.columns]
        seen = {target.key for target in targets}
        for extension in self._extensions(d):
            for column in extension.columns:
                if column.key not in seen:
                    targets.append(
                        _Target(
                            key=column.key,
                            source="extension",
                            column=column,
                            extension=extension,
                        )
                    )
                    seen.add(column.key)
        for definition in await self.custom_fields.definitions(d.entity_type):
            if definition.key not in seen:
                targets.append(
                    _Target(key=definition.key, source="custom", definition=definition)
                )
                seen.add(definition.key)
        return targets

    def _export_value(self, target: _Target, row: Any) -> Any:
        if target.source == "custom":
            return (getattr(row, "custom", None) or {}).get(target.key)
        column = target.column
        assert column is not None  # noqa: S101 — builtin/extension always carry one
        return column.getter(row) if column.getter else getattr(row, column.target, None)

    def _readonly(self, target: _Target) -> bool:
        return bool(target.column and target.column.readonly)

    def _required(self, target: _Target) -> bool:
        if target.definition is not None:
            return bool(target.definition.required)
        # A contributed column is never required — asserted at mount time, so this is the
        # single place the rule has to be read back.
        return bool(target.column and target.column.required and target.source == "builtin")

    def _column_info(self, d: ImpexDescriptor, target: _Target) -> ImpexColumnInfo:
        column = target.column
        definition = target.definition
        return ImpexColumnInfo(
            key=target.key,
            source=target.source,
            module=target.module,
            label_key=(column.label_key if column else None)
            or (f"impex.column.{d.entity_type}.{target.key}" if column else None),
            # Tenant data: handed over as-is for the client to resolve, exactly as every other
            # custom-field surface does. The API never picks a locale for tenant content.
            label_i18n=dict(definition.label_i18n or {}) if definition else None,
            data_type=column.data_type if column else str(definition.data_type),
            required=self._required(target),
            readonly=bool(column and column.readonly),
            clearable=column.clearable if column else True,
            options=list(column.options) if column else [],
            natural_key=target.key in d.natural_keys,
            aliases=list(column.aliases) if column else [],
        )

    def _natural_keys(
        self, d: ImpexDescriptor, by_key: dict[str, _Target], match_key: str | None
    ) -> tuple[str, ...]:
        """The upsert keys to try, in priority order — narrowed to one if the caller said so.

        Forcing the key is what lets a file that carries *both* a klantnummer and a name be
        matched on the name deliberately (a first import of numbered rows whose numbers are the
        old system's, say). Outside ``natural_keys`` it is a 422, not a silent fallback.
        """
        if match_key is None:
            return tuple(key for key in d.natural_keys if key in by_key)
        if match_key not in d.natural_keys:
            raise AppError(
                "invalid_match_key", "impex.errors.invalid_match_key", status_code=422
            )
        return (match_key,) if match_key in by_key else ()

    def _header_columns(
        self, table: ParsedTable, by_key: dict[str, _Target]
    ) -> tuple[list[_Target | None], list[ImportRowError]]:
        """The pre-mapping contract: the header **is** the mapping, and must be exact keys.

        Unchanged on purpose (see :meth:`import_csv`) — aliases are not consulted here, so a
        file that failed before fails identically and an export still round-trips.
        """
        errors: list[ImportRowError] = []
        seen: set[str] = set()
        for name in table.header:
            if name in seen:
                errors.append(
                    ImportRowError(row=0, field=name, message_key="impex.errors.duplicate_column")
                )
            elif name not in by_key:
                errors.append(
                    ImportRowError(row=0, field=name, message_key="impex.errors.unknown_column")
                )
            seen.add(name)
        errors.extend(
            ImportRowError(row=0, field=target.key, message_key="impex.errors.missing_column")
            for target in by_key.values()
            if self._required(target) and target.key not in seen
        )
        return [by_key.get(name) for name in table.header], errors

    def _map_columns(
        self,
        table: ParsedTable,
        mapping: str,
        by_key: dict[str, _Target],
        match_key: str | None,
    ) -> tuple[list[_Target | None], list[ImportRowError]]:
        """An explicit ``{file column index: target key}`` mapping.

        Indices rather than header names: a spreadsheet export carries duplicate and empty
        headers, neither of which a name-keyed mapping can express, and an index survives the
        header-normalisation drift a name does not. **An unmapped column is skipped**, not an
        error — the whole point is to accept a file with columns this system knows nothing
        about, which is the direct inverse of the header path's rule.

        Every problem is reported as a row-0 error rather than raised, so the preview shows all
        of them at once instead of the user fixing one 422 at a time.
        """
        errors: list[ImportRowError] = []
        try:
            parsed = json.loads(mapping)
            if not isinstance(parsed, dict):
                raise ValueError
            pairs = [(int(index), str(key)) for index, key in parsed.items()]
        except (ValueError, TypeError):
            return [], [ImportRowError(row=0, message_key="impex.errors.invalid_mapping")]

        columns: list[_Target | None] = [None] * table.width
        claimed: dict[str, int] = {}
        for index, key in sorted(pairs):
            header = table.header[index] if 0 <= index < len(table.header) else str(index)
            if not 0 <= index < table.width:
                errors.append(
                    ImportRowError(
                        row=0, field=key, message_key="impex.errors.invalid_mapping"
                    )
                )
            elif key not in by_key:
                errors.append(
                    ImportRowError(
                        row=0, field=header, message_key="impex.errors.unknown_column"
                    )
                )
            elif key in claimed:
                errors.append(
                    ImportRowError(
                        row=0, field=header, message_key="impex.errors.duplicate_column"
                    )
                )
            else:
                claimed[key] = index
                columns[index] = by_key[key]

        errors.extend(
            ImportRowError(row=0, field=target.key, message_key="impex.errors.missing_column")
            for target in by_key.values()
            if self._required(target) and target.key not in claimed
        )
        if match_key and match_key not in claimed:
            # Matching on a column the file does not carry would make every row a create.
            errors.append(
                ImportRowError(
                    row=0, field=match_key, message_key="impex.errors.missing_column"
                )
            )
        return columns, errors

    def _labels(self, d: ImpexDescriptor, target: _Target) -> list[str]:
        """What this product calls this column, in every locale it ships.

        A tenant's custom field carries its own ``label_i18n``; everything else is named in the
        §8 catalogs under the same key the mapping step already displays. Reading them is what
        makes recognition bilingual **by following the keys** rather than by a second list of
        spellings — a new column is recognised in Dutch and English the moment its label lands
        in ``messages/{en,nl}.json``, which §8 requires in that same change anyway.
        """
        if target.definition is not None:
            return [str(v) for v in (target.definition.label_i18n or {}).values() if v]
        if target.column is None:
            return []
        return translations(
            target.column.label_key or f"impex.column.{d.entity_type}.{target.key}"
        )

    def _suggest(
        self, d: ImpexDescriptor, table: ParsedTable, targets: list[_Target]
    ) -> list[ImpexSourceColumn]:
        """Pre-fill each file column with the target it most likely means.

        Three pools, tried in that order and **global** rather than per column, so a certain
        signal always beats a weaker one whichever column carries it: the stable key, then the
        column's own label in any locale, then a hand-written alias. All three are folded — the
        exact-key check used to be case-sensitive while only the alias check folded, so a header
        spelled "Status" matched neither branch even though ``status`` is a column key.

        A guess is never silently applied to an import: it fills the mapping step, which the user
        sees next to real sample cells from their own file, and confirms or corrects.
        """
        by_key = {target.key: target for target in targets}
        writable = [t for t in targets if not self._readonly(t)]
        by_folded: dict[str, tuple[str, str]] = {}
        for kind, pool in (
            ("key", [(t, t.key) for t in writable]),
            ("label", [(t, la) for t in writable for la in self._labels(d, t)]),
            ("alias", [(t, a) for t in writable if t.column for a in t.column.aliases]),
        ):
            for target, spelling in pool:
                by_folded.setdefault(_normalise(spelling), (target.key, kind))

        columns: list[ImpexSourceColumn] = []
        claimed: set[str] = set()
        for index in range(table.width):
            header = table.header[index] if index < len(table.header) else ""
            samples = [
                row[index] for row in table.rows[:SAMPLE_ROWS] if index < len(row) and row[index]
            ]
            key: str | None = None
            match: str | None = None
            folded = _normalise(header)
            if header in by_key:
                key, match = header, "key"
            elif folded in by_folded:
                key, match = by_folded[folded]
            if key is not None and (key in claimed or self._readonly(by_key[key])):
                # A second column claiming one target, or an export-only column: suggest
                # nothing rather than a mapping that would have to be undone.
                key, match = None, None
            if key is not None:
                claimed.add(key)
            columns.append(
                ImpexSourceColumn(
                    index=index,
                    header=header,
                    samples=samples[:SAMPLE_VALUES],
                    suggested_key=key,
                    match=match,
                )
            )
        return columns

    def _parse_row(
        self, index: int, cells: list[str], columns: list[_Target | None]
    ) -> _Row:
        """Coerce one data row against the mapped columns — every failure is a row error."""
        row = _Row(index=index)
        for position, target in enumerate(columns):
            if target is None:
                continue  # an unmapped file column: skipped, never an error
            cell = (cells[position] if position < len(cells) else "").strip()
            if target.source == "custom":
                # Kept verbatim (even "" — it means *clear* on update); the §13 validator
                # coerces and checks it later, once the update target is known.
                row.custom[target.key] = cell
                continue
            column = target.column
            assert column is not None  # noqa: S101
            if column.readonly:
                continue  # exported-only (derived) — present for round-trip, never written
            # Contributed values go to the contributing module, not into the host's own
            # create/update payload — the only place in the pipeline the kinds differ.
            values = (
                row.extension.setdefault(target.module or "", {})
                if target.source == "extension"
                else row.values
            )
            if cell == "":
                if column.required:
                    row.errors.append((column.key, "errors.required"))
                elif column.clearable:
                    values[column.target] = None
                # else: not clearable — leave the field (or the link) untouched.
                #
                # ``clearable`` decides this for **every** type including ``fk``: whether an
                # emptied cell detaches is a property of the link, not of it being a link. A
                # hosting record with no client is shared infrastructure — a real state the
                # file must be able to express — while a domain with no client is nonsense,
                # and each says so with one flag rather than one blanket rule.
            elif column.data_type in _REFERENCE_TYPES:
                row.fk[column.key] = (column, cell)
            elif column.data_type == "email":
                try:
                    values[column.target] = _email_adapter.validate_python(cell)
                except ValidationError:
                    row.errors.append((column.key, "errors.invalid_email"))
            elif column.data_type == "phone":
                # Deferred: needs the row's country column (which may come later in the file)
                # and its upsert target. Resolved in _normalize_phones once both exist.
                row.phone[column.key] = (
                    column, target.module if target.source == "extension" else None, cell
                )
            elif column.data_type == "select":
                # Folded, and against every locale's label as well as the value itself: the
                # engine already reads "Ja"/"Nee" for a bool, and demanding the exact lowercase
                # enum token here made every hand-made status column fail on "Active".
                option = _select_vocabulary(
                    column.options, column.option_label_key
                ).get(_normalise(cell))
                if option is not None:
                    values[column.target] = option
                else:
                    row.errors.append((column.key, "impex.errors.invalid_option"))
            elif column.data_type == "date":
                try:
                    values[column.target] = date.fromisoformat(cell).isoformat()
                except ValueError:
                    row.errors.append((column.key, "impex.errors.invalid_date"))
            elif column.data_type == "time":
                if _TIME_RE.match(cell):
                    hours, minutes = cell.split(":")
                    values[column.target] = f"{int(hours):02d}:{minutes}"
                else:
                    row.errors.append((column.key, "impex.errors.invalid_time"))
            elif column.data_type == "number":
                try:
                    values[column.target] = str(Decimal(cell.replace(",", ".")))
                except InvalidOperation:
                    row.errors.append((column.key, "impex.errors.invalid_number"))
            elif column.data_type == "bool":
                lowered = cell.lower()
                if lowered in _TRUE_WORDS:
                    values[column.target] = True
                elif lowered in _FALSE_WORDS:
                    values[column.target] = False
                else:
                    row.errors.append((column.key, "impex.errors.invalid_bool"))
            else:
                values[column.target] = cell
        return row

    def _mark_natural_keys(
        self, natural_keys: tuple[str, ...], rows: list[_Row], by_key: dict[str, _Target]
    ) -> None:
        """Pick each row's match key — the first of ``natural_keys`` the row actually fills.

        A natural key value may appear once per file: the second occurrence would silently
        overwrite what the first just imported. Counted **per key**, since a klantnummer and a
        name are different namespaces; the *same company* reached through two different keys is
        caught later, once the rows have resolved to entities (:meth:`_mark_duplicate_targets`).

        A **reference** column may be the key too — a website has no name of its own and is
        identified by its domain. The raw cell is what ``find_existing`` gets in that case (the
        domain name the file carries, not the id it resolves to), because matching runs before
        resolution: the two are independent lookups and neither should wait on the other.
        """
        seen: dict[str, set[str]] = {}
        for row in rows:
            for key in natural_keys:
                target = by_key[key].column
                value = row.values.get(target.target if target else key)
                if value is None and key in row.fk:
                    value = row.fk[key][1]
                if isinstance(value, str) and value.strip():
                    row.nk_key, row.nk = key, value
                    break
            if row.nk is None:
                continue
            bucket = seen.setdefault(row.nk_key or "", set())
            if row.nk in bucket:
                row.nk_duplicate = True
                row.errors.append((row.nk_key, "impex.errors.duplicate_in_file"))
            bucket.add(row.nk)

    async def _find_existing(
        self, d: ImpexDescriptor, rows: list[_Row]
    ) -> dict[str, dict[str, list[Any]]]:
        """``{natural key: {value: [rows]}}`` — one batched resolver call per key **used**.

        A file that only carries names never queries client numbers.
        """
        wanted: dict[str, set[str]] = {}
        for row in rows:
            if row.nk and row.nk_key and not row.nk_duplicate:
                wanted.setdefault(row.nk_key, set()).add(row.nk)
        return {
            key: await d.find_existing(self.ctx, key, sorted(values))
            for key, values in wanted.items()
        }

    def _mark_duplicate_targets(self, rows: list[tuple[_Row, Any]]) -> None:
        """Two rows resolving to the **same existing entity** is a duplicate, whichever keys
        they used.

        Per-key dedup cannot see this: row 1 keyed on ``client_number=K001`` and row 5 keyed on
        ``name=Acme`` sit in different buckets while pointing at one company, and the later row
        would silently overwrite what the earlier one just imported.
        """
        claimed: dict[Any, int] = {}
        for row, entity in rows:
            if entity is None or row.errors:
                continue
            identity = getattr(entity, "id", None)
            if identity is None:
                continue
            if identity in claimed:
                row.errors.append((row.nk_key, "impex.errors.duplicate_in_file"))
            else:
                claimed[identity] = row.index

    async def _resolve_fks(
        self, d: ImpexDescriptor, rows: list[_Row]
    ) -> dict[str, dict[str, uuid.UUID | str]]:
        """One batched resolver call per FK column for the whole file, never one per row."""
        references: dict[str, set[str]] = {}
        for row in rows:
            for key, (_, ref) in row.fk.items():
                references.setdefault(key, set()).add(ref)
        resolved: dict[str, dict[str, uuid.UUID | str]] = {}
        for key, refs in references.items():
            resolver = d.fk_resolvers.get(key)
            resolved[key] = await resolver(self.ctx, sorted(refs)) if resolver else {}
        return resolved

    def _plan_row(
        self,
        d: ImpexDescriptor,
        row: _Row,
        existing: dict[str, dict[str, list[Any]]],
        fk_resolved: dict[str, dict[str, uuid.UUID | str]],
    ) -> Any | None:
        """Resolve FKs and the upsert target; returns the entity to update, or None to create."""
        for key, (column, ref) in row.fk.items():
            outcome = fk_resolved.get(key, {}).get(ref)
            # A resolver reports failure as an i18n **key** and success as a resolved *object*
            # — an id, or a whole ``PartyRef``. Testing for "is a string" rather than "is a
            # UUID" is what lets a resolver return something richer than an id without core
            # learning its shape; every error key is a str and no resolved value ever is.
            if outcome is None or isinstance(outcome, str):
                row.errors.append(
                    (column.key, outcome or "impex.errors.unresolved_reference")
                )
            else:
                row.values[column.target] = outcome
        if row.nk is None or row.nk_duplicate:
            return None
        matches = existing.get(row.nk_key or "", {}).get(row.nk, [])
        if len(matches) > 1:
            row.ambiguous = True
            row.errors.append((row.nk_key, "impex.errors.ambiguous_match"))
            return None
        return matches[0] if matches else None

    async def _default_region(self, rows: list[_Row]) -> str | None:
        """The org's country — read **once** for the whole file, and only if it is needed.

        A file with no phone column, or one whose phone cells are all blank, never touches
        ``org_settings`` (docs/PERFORMANCE.md), exactly as the services' own lazy lookup
        doesn't.
        """
        if not any(row.phone for row in rows):
            return None
        return await org_default_country(self.ctx.session, self.ctx.org.id)

    def _normalize_phones(
        self, row: _Row, entity: Any | None, default_region: str | None
    ) -> None:
        """Store each phone cell as E.164, or make it this row's error (issue #289).

        Phone validation lives in the service (:mod:`app.core.phone`), which runs *after* the
        importer has built its report — so an invalid number used to escape the dry run and
        come back as a request-level 422 naming no row. The whole file then looked suspect,
        including the blank cells and the 85 valid numbers, when one cell was a digit short.

        Two rules keep this a genuine pre-check rather than a second, stricter gate:

        * **The region is resolved exactly as the owning service resolves it** — the row's own
          country column (the value it will be *written* with, else the stored one), otherwise
          the org's. A different region here would reject a number the write accepts.
        * **An unchanged value on an existing row is not revalidated**, mirroring issue #256's
          grandfathering: rows predating validation hold freeform strings, and re-importing an
          export must not fail on a number nobody edited. A *contributed* column (§17) has no
          such comparison available — the contributor matches its own row at write time, which
          a dry run must not do — so its cells are always validated, as a create would be.
        """
        for column, module, cell in row.phone.values():
            values = row.extension.setdefault(module, {}) if module else row.values
            stored = (
                getattr(entity, column.target, None)
                if entity is not None and not module
                else None
            )
            if stored is not None and cell == stored:
                values[column.target] = cell  # grandfathered; the service skips it too
                continue
            try:
                values[column.target] = normalize_phone(
                    cell,
                    field=column.key,
                    region=self._phone_region(column, values, entity, default_region),
                )
            except AppError as exc:
                row.errors.append((column.key, exc.message_key))

    def _phone_region(
        self,
        column: ImpexColumn,
        values: dict[str, Any],
        entity: Any | None,
        default_region: str | None,
    ) -> str | None:
        """Which country a national number in this row is read in.

        Presence beats truthiness: a row that explicitly *clears* its country is read in the
        org's, not in the country it is about to stop having — which is what the entity's own
        service does with the same two values.
        """
        if column.region_field:
            if column.region_field in values:
                code = values[column.region_field]
            elif entity is not None:
                code = getattr(entity, column.region_field, None)
            else:
                code = None
            if is_valid_country(code):
                return str(code).upper()
        return default_region

    def _validate_custom(
        self,
        row: _Row,
        defs: list[CustomFieldDefinition],
        custom_keys: set[str],
        entity: Any | None,
    ) -> None:
        """The §13 dynamic validator, run per row against the tenant's definitions.

        On an update the file's cells are merged over the entity's current values first (an
        empty cell clears its key), so ``required`` judges the row as it *would be stored* —
        an update that doesn't mention a required field keeps its existing value and passes.
        Definitions were loaded **once** for the whole file (docs/PERFORMANCE.md).
        """
        if row.nk_duplicate or row.ambiguous:
            return  # no meaningful target to merge against; the row already carries its error
        if entity is not None and not row.custom:
            return  # an update whose file has no custom columns leaves custom untouched
        merged: dict[str, Any] = {}
        if entity is not None:
            current = getattr(entity, "custom", None) or {}
            merged = {k: v for k, v in current.items() if k in custom_keys}
        for key, cell in row.custom.items():
            if cell == "":
                merged.pop(key, None)
            elif next(cd for cd in defs if cd.key == key).data_type == "multi_select":
                merged[key] = [
                    part.strip()
                    for part in cell.split(MULTI_VALUE_SEPARATOR)
                    if part.strip()
                ]
            else:
                merged[key] = cell
        try:
            cleaned = self.custom_fields.validate_values(defs, merged)
        except AppError as exc:
            row.errors.extend((f, key) for f, key in (exc.fields or {}).items())
            return
        row.values["custom"] = cleaned
