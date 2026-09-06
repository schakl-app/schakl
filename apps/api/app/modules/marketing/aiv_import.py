"""Reading Search Console's Generative AI performance export (docs/GOOGLE_SEARCH_CONSOLE.md §6a).

Business-licensed — see LICENSE.

Search Console has reported impressions in AI Overviews and AI Mode since June 2026, draws
them in the console, offers an **export button**, and returns them through no API — the
discovery document's search-type enum is still the six it always was
(``google_search_console.client.GENERATIVE_AI_SEARCH_TYPES``). So the one honest way to get
the figure onto a client's dashboard and into their monthly report is the file that button
produces, uploaded by whoever has the console open. This module reads that file and nothing
else: what it returns is ``{day: impressions}``, and the service decides where it lands.

Written from what the report *documents* rather than from a file, because no property with
the report was to hand — so every parse is defensive in the direction of **refusing** rather
than guessing (the OXXA rule, CLAUDE.md §10):

* **The date column is the one that decides the file is the right file.** The report exports
  four tables — dates, pages, countries, devices — and only the dates one is a series. A zip
  is searched for the member whose header carries a date column; a lone CSV must carry one
  itself. A file with no such column is refused naming the columns it did have, never read as
  "zero everywhere".
* **The granularity is refused, not folded.** The console's chart can be grouped by day, week
  or month, and a weekly export headed ``Week`` carries one row per seven days. Storing that
  row as one day would print a month at a seventh of its size on the dashboard and a seventh
  again on the report; it is refused with a sentence saying to export by day.
* **``~`` and ``-`` are zeros**, as Google's own help says of every export; a thousands
  separator (``1.234`` / ``1,234``) is stripped, because impressions are whole numbers and a
  decimal comma cannot occur in one.
* **Headers are matched in English and Dutch**, because the console exports in the language
  the account is set to and this product's tenants are Dutch agencies with Dutch Google
  accounts.
* **Every cap is checked before the work it bounds**, the ``core/impex/parsing`` rule: the byte
  ceiling before decoding, a zip member's declared size before inflating it, the row ceiling
  while reading — and over any limit is an error, never a truncation.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date

from app.errors import AppError

#: A month of daily rows is thirty lines; a year is under four hundred. Two megabytes is a
#: file that is not this report.
MAX_BYTES = 2_000_000
MAX_ROWS = 2_000
#: Members of a zip larger than this on paper are not inflated at all.
_MAX_MEMBER_BYTES = 4_000_000

_ZIP_MAGIC = b"PK\x03\x04"

#: The column that makes a table a *series*: the report's own "Date" tab, in the two
#: languages the console exports for this product's tenants.
_DATE_HEADERS = {"date", "datum", "day", "dag"}
#: A grouped export: refused, because one row is not one day.
_GROUPED_HEADERS = {"week", "month", "maand"}
_IMPRESSION_HEADERS = ("impression", "vertoning", "impressie", "weergave")

_ISO_DATE = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})\s*$")
_NOT_DIGIT = re.compile(r"[^\d]")


@dataclass
class ParsedAiExport:
    """What the file said: one whole number per day, and the columns it said it in."""

    rows: dict[date, int] = field(default_factory=dict)
    #: Which member of the zip (or ``""`` for a bare CSV) the series was read from.
    member: str = ""
    #: The header of the table read, as the file spelled it — for the trail.
    columns: list[str] = field(default_factory=list)
    #: Rows the file carried that were not a dated figure (a blank line, a footer), counted so
    #: a response can say what was skipped without printing it.
    skipped: int = 0

    @property
    def date_from(self) -> date | None:
        return min(self.rows) if self.rows else None

    @property
    def date_to(self) -> date | None:
        return max(self.rows) if self.rows else None


def parse_export(raw: bytes) -> ParsedAiExport:
    """Read the export — a bare CSV or the zip the console's button produces — into a series.

    Refuses (as ``AppError``, 422) rather than guesses: too large, no readable table, no date
    column, a grouped granularity, or a table with no rows at all.
    """
    if len(raw) > MAX_BYTES:
        raise AppError(
            "validation",
            "errors.marketing_ai_export_too_large",
            status_code=422,
            details={"limit": MAX_BYTES},
        )
    if not raw.strip():
        raise AppError("validation", "errors.marketing_ai_export_empty", status_code=422)
    if raw[:4] == _ZIP_MAGIC:
        return _parse_zip(raw)
    return _parse_table(_decode(raw), member="")


def parse_rows(rows: list[tuple[str, int | float | str]]) -> ParsedAiExport:
    """The JSON twin's shape: ``[(iso date, impressions)]``, read by the same rules."""
    out = ParsedAiExport(columns=["date", "impressions"])
    if len(rows) > MAX_ROWS:
        raise AppError(
            "validation",
            "errors.marketing_ai_export_too_many_rows",
            status_code=422,
            details={"limit": MAX_ROWS},
        )
    for day_raw, value_raw in rows:
        day = _day(str(day_raw))
        if day is None:
            raise AppError(
                "validation",
                "errors.marketing_ai_export_bad_date",
                status_code=422,
                details={"value": str(day_raw)[:40]},
            )
        out.rows[day] = _count(value_raw)
    if not out.rows:
        raise AppError("validation", "errors.marketing_ai_export_empty", status_code=422)
    return out


# --- the container ------------------------------------------------------------------------- #
def _parse_zip(raw: bytes) -> ParsedAiExport:
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
        members = archive.infolist()
    except zipfile.BadZipFile as exc:
        raise AppError(
            "validation", "errors.marketing_ai_export_unreadable", status_code=422
        ) from exc
    seen: list[str] = []
    for info in members:
        if info.is_dir() or not info.filename.lower().endswith((".csv", ".tsv", ".txt")):
            continue
        if info.file_size > _MAX_MEMBER_BYTES:
            raise AppError(
                "validation",
                "errors.marketing_ai_export_too_large",
                status_code=422,
                details={"limit": MAX_BYTES, "member": info.filename},
            )
        text = _decode(archive.read(info))
        header = _header(text)
        seen.append(info.filename)
        if header is None:
            continue
        if _find(header, _DATE_HEADERS) is not None or _find(header, _GROUPED_HEADERS) is not None:
            return _parse_table(text, member=info.filename)
    # No member carried a date column: the file is some other export (a pages table alone,
    # say) or not this report's at all. Name what was in it, so the sentence can be checked.
    raise AppError(
        "validation",
        "errors.marketing_ai_export_no_dates",
        status_code=422,
        details={"members": seen[:20]},
    )


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def _sniff(sample: str) -> str:
    """Comma, semicolon or tab — whichever the header line uses most. Google writes commas;
    a Dutch Excel re-save writes semicolons; a Sheets copy pastes tabs."""
    first = sample.splitlines()[0] if sample.splitlines() else sample
    counts = {sep: first.count(sep) for sep in (",", ";", "\t")}
    best = max(counts, key=lambda sep: counts[sep])
    return best if counts[best] else ","


def _header(text: str) -> list[str] | None:
    reader = csv.reader(io.StringIO(text), delimiter=_sniff(text))
    for row in reader:
        if any(cell.strip() for cell in row):
            return [cell.strip() for cell in row]
    return None


def _find(header: list[str], names: set[str] | tuple[str, ...]) -> int | None:
    for index, cell in enumerate(header):
        key = cell.strip().lower()
        if isinstance(names, set):
            if key in names:
                return index
        elif any(key.startswith(name) for name in names):
            return index
    return None


# --- the table ----------------------------------------------------------------------------- #
def _parse_table(text: str, *, member: str) -> ParsedAiExport:
    delimiter = _sniff(text)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    header: list[str] | None = None
    out = ParsedAiExport(member=member)
    for row in reader:
        if header is None:
            if not any(cell.strip() for cell in row):
                continue
            header = [cell.strip() for cell in row]
            out.columns = header
            if _find(header, _GROUPED_HEADERS) is not None and _find(header, _DATE_HEADERS) is None:
                raise AppError(
                    "validation",
                    "errors.marketing_ai_export_grouped",
                    status_code=422,
                    details={"columns": header[:10]},
                )
            date_at = _find(header, _DATE_HEADERS)
            value_at = _find(header, _IMPRESSION_HEADERS)
            if date_at is None or value_at is None:
                raise AppError(
                    "validation",
                    "errors.marketing_ai_export_unrecognised",
                    status_code=422,
                    details={"columns": header[:10]},
                )
            continue
        if len(out.rows) + out.skipped >= MAX_ROWS:
            raise AppError(
                "validation",
                "errors.marketing_ai_export_too_many_rows",
                status_code=422,
                details={"limit": MAX_ROWS},
            )
        if not any(cell.strip() for cell in row):
            continue
        day = _day(row[date_at]) if date_at < len(row) else None
        if day is None:
            # A total line, a footer, a note — the export has them and none is a day.
            out.skipped += 1
            continue
        out.rows[day] = _count(row[value_at] if value_at < len(row) else "")
    if header is None:
        raise AppError("validation", "errors.marketing_ai_export_empty", status_code=422)
    if not out.rows:
        raise AppError("validation", "errors.marketing_ai_export_empty", status_code=422)
    return out


def _day(raw: str) -> date | None:
    match = _ISO_DATE.match(raw or "")
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _count(raw: int | float | str) -> int:
    """A whole number of impressions. ``~`` and ``-`` are Google's zeros; separators go."""
    if isinstance(raw, (int, float)):
        return max(0, int(raw))
    digits = _NOT_DIGIT.sub("", raw or "")
    return int(digits) if digits else 0
