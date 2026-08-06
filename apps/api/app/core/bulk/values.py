"""Turn the one shared payload a bulk edit carries into the values every row is written with.

Two rules shape this file, and both are borrowed from surfaces that learned them the hard way.

**A bad value is the caller's, not a row's** (``interactions.service._bulk_links``). Every row
in the selection would fail on it identically, so an unknown status or an unresolvable client
is a 422 for the whole call, resolved *once*, before anything is touched — never fifty
identical entries in a failure list. Row-level trouble is what the result reports.

**Absent means leave alone; explicit ``null`` means clear** (``InteractionBulkLinks``). A bulk
dialog opens blank over rows that disagree with each other, so "I did not fill this in" can
never mean "empty it on all of them" — that would wipe, on every row the user did not look at,
exactly the field they had not thought about. Clearing is a thing you ask for, and only where
the column says it is possible at all.

Cells arrive as strings for the same reason the import's do: the values come from a form, the
vocabulary is already written down as :class:`~app.core.impex.spec.ImpexColumn` types, and one
coercion the two surfaces share is one fewer place for "the preview accepted what the write
rejects" to live (issue #289).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from app.core.bulk.spec import BulkDescriptor
from app.errors import AppError

if TYPE_CHECKING:
    from app.core.tenancy import RequestContext

#: Mirrors the import's vocabulary (``app.core.impex.service``) so a "ja" pasted into a
#: spreadsheet and a "ja" chosen in a dialog mean the same thing.
_TRUE_WORDS = {"true", "ja", "yes", "1"}
_FALSE_WORDS = {"false", "nee", "no", "0"}


def _invalid(key: str, message_key: str) -> AppError:
    """422 naming the field, so the dialog can put the message under the control that caused it."""
    return AppError("validation", message_key, status_code=422, fields={key: message_key})


def _scalar(key: str, column: Any, raw: str) -> Any:
    """One non-reference cell, coerced by its declared type."""
    if column.data_type == "select":
        if raw not in column.options:
            raise _invalid(key, "impex.errors.invalid_option")
        return raw
    if column.data_type == "bool":
        lowered = raw.strip().lower()
        if lowered in _TRUE_WORDS:
            return True
        if lowered in _FALSE_WORDS:
            return False
        raise _invalid(key, "impex.errors.invalid_bool")
    if column.data_type == "date":
        try:
            return dt.date.fromisoformat(raw.strip()).isoformat()
        except ValueError as exc:
            raise _invalid(key, "impex.errors.invalid_date") from exc
    if column.data_type == "number":
        try:
            return str(Decimal(raw.strip().replace(",", ".")))
        except (InvalidOperation, ValueError) as exc:
            raise _invalid(key, "impex.errors.invalid_number") from exc
    return raw.strip()


async def resolve_values(
    ctx: RequestContext, descriptor: BulkDescriptor, sent: dict[str, str | None]
) -> dict[str, Any]:
    """The payload, resolved once into the ``values`` dict every row's writer is handed.

    Keys are the writer's own targets (``field or key``, so column ``company`` writes into
    ``company_id``) — exactly what the import hands ``update_row``, which is why the module's
    write path needs no bulk-specific branch.
    """
    columns = descriptor.columns
    unknown = sorted(set(sent) - set(columns))
    if unknown:
        # An unknown key is never "ignore it": a dialog that posted `staus` would report
        # success having changed nothing at all.
        raise _invalid(unknown[0], "impex.errors.unknown_column")

    values: dict[str, Any] = {}
    references: dict[str, tuple[Any, str]] = {}
    for key, raw in sent.items():
        column = columns[key]
        if raw is None or raw.strip() == "":
            # ``required`` overrules ``clearable`` for the same reason it does on an import:
            # a subscription's client is a NOT NULL column that happens to be settable, and a
            # blank one is "this file doesn't carry it", never "detach twelve agreements".
            if column.required or not column.clearable:
                raise _invalid(key, "errors.required")
            values[column.target] = None
            continue
        if column.data_type in ("fk", "party"):
            references[key] = (column, raw.strip())
        else:
            values[column.target] = _scalar(key, column, raw)

    for key, (column, raw) in references.items():
        resolver = descriptor.resolvers[key]  # guaranteed by check_descriptor
        resolved = (await resolver(ctx, [raw])).get(raw)
        # The import's contract, unchanged: **a ``str`` return is an error key and anything
        # else is the resolved value**, which is what lets a party column hand back a whole
        # ``PartyRef`` without core learning its shape (CLAUDE.md §17).
        if resolved is None or isinstance(resolved, str):
            raise _invalid(key, resolved or "impex.errors.unresolved_reference")
        values[column.target] = resolved
    return values
