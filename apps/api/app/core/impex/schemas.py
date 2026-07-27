"""Pydantic schemas for the impex surface (issue #77)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ImportRowError(BaseModel):
    """One validation failure, addressed the way a spreadsheet user counts.

    ``row`` 0 is the **header row** (unknown/duplicate/missing columns); data rows count from 1
    in file order. ``message_key`` is an i18n key (CLAUDE.md §9) — never user-facing English.
    """

    row: int
    field: str | None = None
    message_key: str


class ImportReport(BaseModel):
    """What an import did — or, on a dry run, what it *would* do.

    ``dry_run=false`` is all-or-nothing: with any error, ``applied`` stays ``False`` and nothing
    was written. ``errors`` carries the first slice; ``error_count`` is always the full count,
    so a truncated list never reads as "that's all of them" (docs/UX.md).
    """

    dry_run: bool
    rows: int = Field(description="Data rows in the file (header excluded).")
    creates: int
    updates: int
    error_count: int
    errors: list[ImportRowError]
    applied: bool


class ImpexColumnInfo(BaseModel):
    """One target column an import may write into — the mapping step's vocabulary.

    ``key`` is the stable header key and the only thing a mapping ever names. Labels are for
    display: ``label_key`` is an i18n key for the built-in columns, ``label_i18n`` the tenant's
    own per-locale labels for a custom field (§13 data, resolved by the client, never by the
    API — the API has no business picking the caller's locale for tenant content).
    """

    key: str
    #: ``builtin`` (the entity's own), ``extension`` (contributed by another module) or
    #: ``custom`` (a tenant-defined field). The UI groups on this; the engine does not branch
    #: on it except at write time.
    source: str
    #: The contributing module, for an ``extension`` column.
    module: str | None = None
    label_key: str | None = None
    label_i18n: dict[str, str] | None = None
    data_type: str
    required: bool
    readonly: bool
    clearable: bool
    options: list[str] = Field(default_factory=list)
    #: This column is one the upsert can match on.
    natural_key: bool = False
    #: Header spellings that pre-fill this column in the mapping step. Never accepted as a
    #: header key in their own right.
    aliases: list[str] = Field(default_factory=list)


class ImpexColumnsResponse(BaseModel):
    """Everything a client needs to render a mapping UI for one entity, in one call."""

    entity_type: str
    importable: bool
    natural_keys: list[str]
    columns: list[ImpexColumnInfo]


class ImpexSourceColumn(BaseModel):
    """One column **of the uploaded file**, addressed by position.

    Position, not header name: a spreadsheet export routinely carries duplicate headers and
    empty ones, both of which a name-keyed mapping cannot express, and a header spelling drifts
    while an index does not.
    """

    index: int
    header: str
    #: First few non-empty cells — the mapping step shows these, and they are what makes a
    #: wrong encoding or a shifted column obvious at a glance.
    samples: list[str] = Field(default_factory=list)
    #: Pre-filled target key, or ``None`` for "don't import".
    suggested_key: str | None = None
    #: How the suggestion was reached: ``key`` (exact header key), ``alias``, or ``position``
    #: — so the UI can present a weak guess differently from a certain one.
    match: str | None = None


class ImpexInspectReport(BaseModel):
    """What the file *is*, before anything is mapped or written.

    Touches no tenant rows: this reads the upload and compares it with the entity's column
    catalog. It is still write-gated — it is a step of an import, and the tighter gate is the
    honest one for a route that accepts an arbitrary upload.
    """

    source_format: str
    delimiter: str | None = None
    encoding: str | None = None
    sheet: str | None = None
    sheets: list[str] = Field(default_factory=list)
    rows: int = Field(description="Data rows (the header row excluded).")
    #: Cells carrying a formula the spreadsheet never calculated; those cells arrive empty.
    uncalculated_formulas: int = 0
    #: Digest of the inspected bytes. The import repeats it, and a mismatch is a 409 — the
    #: mapping is by position, so mapping one file and importing another silently writes the
    #: wrong columns into the right fields.
    fingerprint: str
    columns: list[ImpexSourceColumn]
    #: Required target columns nothing was suggested for — the wizard blocks on these.
    missing_required: list[str] = Field(default_factory=list)
    #: The natural key the file appears to carry, most stable first.
    suggested_match_key: str | None = None


class ImpexEntityInfo(BaseModel):
    """One CSV-capable entity type — the Instellingen → Import & export screen's catalog."""

    entity_type: str
    read_permission: str
    write_permission: str
    importable: bool
    filters: list[str]
    #: Column keys an import upserts on, most stable first. Empty = create-only (every row
    #: creates; nothing is ever matched against what is already there).
    natural_keys: list[str]
