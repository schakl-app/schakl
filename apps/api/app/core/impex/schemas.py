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
