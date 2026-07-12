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

Imports are capped at :data:`MAX_IMPORT_ROWS` data rows per request. Larger files belong to a
background ARQ job with progress + a result report — explicitly deferred (issue #77 phase note);
the cap is what keeps the synchronous path honest until that lands.
"""

from __future__ import annotations

import csv
import io
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from fastapi.responses import Response
from pydantic import EmailStr, TypeAdapter, ValidationError

from app.core.customfields.models import CustomFieldDefinition
from app.core.customfields.service import CustomFieldsService
from app.core.impex.schemas import ImportReport, ImportRowError
from app.core.impex.spec import ImpexColumn, ImpexDescriptor
from app.core.tenancy import RequestContext
from app.errors import AppError

#: Page size for the export's batched fetch through the module's list service.
EXPORT_PAGE_SIZE = 500
#: Synchronous import ceiling; row MAX+1 onward is a 413, never a silent truncation.
MAX_IMPORT_ROWS = 2000
#: Byte ceiling — a 2000-row CSV is well under this; anything bigger is not a CSV import.
MAX_IMPORT_BYTES = 5 * 1024 * 1024
#: Row errors returned per report; ``error_count`` always carries the full number.
ERRORS_RETURNED = 50
#: Multi-select custom values join/split on this in a CSV cell.
MULTI_VALUE_SEPARATOR = "|"

_email_adapter: TypeAdapter[str] = TypeAdapter(EmailStr)


def _cell(value: Any) -> str:
    """Serialize one value for a CSV cell: ISO dates, plain numbers, ``true``/``false``."""
    if value is None:
        return ""
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, list):
        return MULTI_VALUE_SEPARATOR.join(str(item) for item in value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


@dataclass
class _Row:
    """One parsed data row on its way through validation."""

    index: int                                    # 1-based data row number (header is 0)
    values: dict[str, Any] = field(default_factory=dict)
    fk: dict[str, tuple[ImpexColumn, str]] = field(default_factory=dict)
    custom: dict[str, str] = field(default_factory=dict)  # raw cells, "" = clear
    errors: list[tuple[str | None, str]] = field(default_factory=list)
    nk: str | None = None
    nk_duplicate: bool = False
    ambiguous: bool = False


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
        self.ctx.require(d.read_permission)  # the route declares it too; defence-in-depth
        custom_defs = await self._custom_definitions(d)

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

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow([c.key for c in d.columns] + [cd.key for cd in custom_defs])
        for row in rows:
            cells = [
                _cell(c.getter(row) if c.getter else getattr(row, c.target, None))
                for c in d.columns
            ]
            custom = getattr(row, "custom", None) or {}
            cells.extend(_cell(custom.get(cd.key)) for cd in custom_defs)
            writer.writerow(cells)

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
        self, d: ImpexDescriptor, raw: bytes, *, dry_run: bool
    ) -> ImportReport:
        self.ctx.require(d.write_permission)  # the route declares it too; defence-in-depth
        header, data = self._parse(raw)

        defs = list(await self.custom_fields.definitions(d.entity_type))
        by_key = {c.key: c for c in d.columns}
        # A custom field whose key shadows a built-in column is unreachable here on purpose:
        # two columns with one header cannot round-trip.
        custom_defs = {cd.key: cd for cd in defs if cd.key not in by_key}

        header_errors = self._check_header(d, header, by_key, custom_defs, defs)
        if header_errors:
            # A broken header makes per-row results meaningless (the typo'd column may be the
            # natural key), so report it alone rather than 2000 misleading row errors.
            return ImportReport(
                dry_run=dry_run,
                rows=len(data),
                creates=0,
                updates=0,
                error_count=len(header_errors),
                errors=header_errors[:ERRORS_RETURNED],
                applied=False,
            )

        columns = [
            ("column", by_key[name])
            if name in by_key
            else ("custom", custom_defs[name])
            for name in header
        ]
        rows = [self._parse_row(index, cells, columns) for index, cells in enumerate(data, 1)]

        nk_target = by_key[d.natural_key].target
        self._mark_natural_keys(d, rows, nk_target)
        existing = await self._find_existing(d, rows)
        fk_resolved = await self._resolve_fks(d, rows)

        errors: list[ImportRowError] = []
        plans: list[tuple[str, Any, dict[str, Any]]] = []
        creates = updates = 0
        for row in rows:
            entity = self._plan_row(d, row, existing, fk_resolved)
            self._validate_custom(row, defs, set(custom_defs), entity)
            if row.errors:
                errors.extend(
                    ImportRowError(row=row.index, field=f, message_key=key)
                    for f, key in row.errors
                )
            elif entity is not None:
                updates += 1
                plans.append(("update", entity, row.values))
            else:
                creates += 1
                plans.append(("create", None, row.values))

        applied = False
        if not dry_run and not errors:
            # All-or-nothing: everything below runs in this request's transaction, so a failure
            # anywhere (an event handler, a unique index) rolls the whole import back.
            for mode, entity, values in plans:
                if mode == "create":
                    await d.create_row(self.ctx, values)
                else:
                    await d.update_row(self.ctx, entity, values)
            applied = True

        return ImportReport(
            dry_run=dry_run,
            rows=len(data),
            creates=creates,
            updates=updates,
            error_count=len(errors),
            errors=errors[:ERRORS_RETURNED],
            applied=applied,
        )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    async def _custom_definitions(self, d: ImpexDescriptor) -> list[CustomFieldDefinition]:
        built_in = {c.key for c in d.columns}
        defs = await self.custom_fields.definitions(d.entity_type)
        return [cd for cd in defs if cd.key not in built_in]

    def _parse(self, raw: bytes) -> tuple[list[str], list[list[str]]]:
        """Bytes → (header, data rows). Tolerates a BOM and a `;` delimiter (Dutch Excel)."""
        if len(raw) > MAX_IMPORT_BYTES:
            raise AppError(
                "file_too_large", "impex.errors.file_too_large", status_code=413
            )
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise AppError("invalid_file", "impex.errors.invalid_file") from exc
        try:
            dialect: csv.Dialect | type[csv.Dialect] = csv.Sniffer().sniff(
                text[:4096], delimiters=",;"
            )
        except csv.Error:
            dialect = csv.excel
        try:
            parsed = [row for row in csv.reader(io.StringIO(text), dialect) if any(row)]
        except csv.Error as exc:
            raise AppError("invalid_file", "impex.errors.invalid_file") from exc
        if not parsed:
            raise AppError("empty_file", "impex.errors.empty_file")
        header, data = [cell.strip() for cell in parsed[0]], parsed[1:]
        if not data:
            raise AppError("empty_file", "impex.errors.empty_file")
        if len(data) > MAX_IMPORT_ROWS:
            raise AppError("too_many_rows", "impex.errors.too_many_rows", status_code=413)
        return header, data

    def _check_header(
        self,
        d: ImpexDescriptor,
        header: list[str],
        by_key: dict[str, ImpexColumn],
        custom_defs: dict[str, CustomFieldDefinition],
        defs: list[CustomFieldDefinition],
    ) -> list[ImportRowError]:
        errors: list[ImportRowError] = []
        seen: set[str] = set()
        for name in header:
            if name in seen:
                errors.append(
                    ImportRowError(row=0, field=name, message_key="impex.errors.duplicate_column")
                )
            elif name not in by_key and name not in custom_defs:
                errors.append(
                    ImportRowError(row=0, field=name, message_key="impex.errors.unknown_column")
                )
            seen.add(name)
        required = [c.key for c in d.columns if c.required] + [
            cd.key for cd in defs if cd.required and cd.key in custom_defs
        ]
        errors.extend(
            ImportRowError(row=0, field=key, message_key="impex.errors.missing_column")
            for key in required
            if key not in seen
        )
        return errors

    def _parse_row(
        self, index: int, cells: list[str], columns: list[tuple[str, Any]]
    ) -> _Row:
        """Coerce one data row against the mapped header — every failure is a row error."""
        row = _Row(index=index)
        for position, (kind, spec) in enumerate(columns):
            cell = (cells[position] if position < len(cells) else "").strip()
            if kind == "custom":
                # Kept verbatim (even "" — it means *clear* on update); the §13 validator
                # coerces and checks it later, once the update target is known.
                row.custom[spec.key] = cell
                continue
            column: ImpexColumn = spec
            if cell == "":
                if column.required:
                    row.errors.append((column.key, "errors.required"))
                elif column.clearable and column.data_type != "fk":
                    row.values[column.target] = None
                # else: not clearable — leave the field (or the links) untouched.
            elif column.data_type == "fk":
                row.fk[column.key] = (column, cell)
            elif column.data_type == "email":
                try:
                    row.values[column.target] = _email_adapter.validate_python(cell)
                except ValidationError:
                    row.errors.append((column.key, "errors.invalid_email"))
            elif column.data_type == "select":
                if cell in column.options:
                    row.values[column.target] = cell
                else:
                    row.errors.append((column.key, "impex.errors.invalid_option"))
            else:
                row.values[column.target] = cell
        return row

    def _mark_natural_keys(
        self, d: ImpexDescriptor, rows: list[_Row], nk_target: str
    ) -> None:
        """A natural key may appear once per file: the second occurrence would silently
        overwrite what the first just imported."""
        seen: set[str] = set()
        for row in rows:
            value = row.values.get(nk_target)
            row.nk = value if isinstance(value, str) and value else None
            if row.nk is None:
                continue
            if row.nk in seen:
                row.nk_duplicate = True
                row.errors.append((d.natural_key, "impex.errors.duplicate_in_file"))
            seen.add(row.nk)

    async def _find_existing(
        self, d: ImpexDescriptor, rows: list[_Row]
    ) -> dict[str, list[Any]]:
        values = sorted({row.nk for row in rows if row.nk and not row.nk_duplicate})
        return await d.find_existing(self.ctx, values) if values else {}

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
        existing: dict[str, list[Any]],
        fk_resolved: dict[str, dict[str, uuid.UUID | str]],
    ) -> Any | None:
        """Resolve FKs and the upsert target; returns the entity to update, or None to create."""
        for key, (column, ref) in row.fk.items():
            outcome = fk_resolved.get(key, {}).get(ref)
            if isinstance(outcome, uuid.UUID):
                row.values[column.target] = outcome
            else:
                row.errors.append(
                    (column.key, outcome or "impex.errors.unresolved_reference")
                )
        if row.nk is None or row.nk_duplicate:
            return None
        matches = existing.get(row.nk, [])
        if len(matches) > 1:
            row.ambiguous = True
            row.errors.append((d.natural_key, "impex.errors.ambiguous_match"))
            return None
        return matches[0] if matches else None

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
