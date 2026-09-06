"""A custom value as the text a **document** prints it (CLAUDE.md §13, invoicing).

Two halves, and both belong to core rather than to the invoicing module: **which** of a
tenant's fields are meant for paper (:func:`printable`), and **what** a stored value reads
as (:func:`format_value`). A tenant marks a definition with ``config_json.print_on_document``
in Instellingen → Aangepaste velden; nothing else about the definition changes, so the flag
needs no migration and a field switched on today prints on the next render.

The stored value is the *raw* half — an option's ``value``, an ISO date, ``True`` — and
paper wants the other half: the option's own label in the document's language, ``30-06-2026``,
*Ja*. The web already has this mapping (``$lib/core/customfields/format.ts``); this is the
same rule for the renderer, which is sandboxed and receives strings only.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.core.customfields.models import CustomFieldDefinition
from app.i18n import translate

#: The ``config_json`` key a definition carries when its value belongs on the document.
PRINT_ON_DOCUMENT = "print_on_document"


def pick_locale(texts: Any, locale: str) -> str:
    """A tenant's per-locale text in ``locale``, else English, else Dutch, else nothing."""
    if not isinstance(texts, dict):
        return ""
    for candidate in (locale, "en", "nl"):
        text = texts.get(candidate)
        if isinstance(text, str) and text.strip():
            return text.strip()
    return ""


def prints_on_document(definition: CustomFieldDefinition) -> bool:
    return bool((definition.config_json or {}).get(PRINT_ON_DOCUMENT))


def printable(definitions: Iterable[CustomFieldDefinition]) -> list[CustomFieldDefinition]:
    """The active definitions a tenant asked to see on the document, in position order."""
    return [d for d in definitions if d.active and prints_on_document(d)]


def _option_label(definition: CustomFieldDefinition, value: Any, locale: str) -> str:
    for option in definition.options_json or []:
        if isinstance(option, dict) and option.get("value") == value:
            return pick_locale(option.get("label_i18n"), locale) or str(value)
    return str(value)


def _date_text(raw: Any) -> str:
    if isinstance(raw, datetime | date):
        return raw.strftime("%d-%m-%Y")
    text = str(raw)
    try:
        return date.fromisoformat(text[:10]).strftime("%d-%m-%Y")
    except ValueError:
        return text


def _number_text(raw: Any, locale: str) -> str:
    try:
        amount = Decimal(str(raw)).normalize()
    except (InvalidOperation, ValueError):
        return str(raw)
    if amount == amount.to_integral_value():
        amount = amount.quantize(Decimal(1))
    text = f"{amount:f}"
    return text.replace(".", ",") if locale.startswith(("nl", "de")) else text


def format_value(definition: CustomFieldDefinition, value: Any, locale: str) -> str:
    """``value`` as printable text in ``locale``. Empty for an empty value. Never raises —
    a definition retyped after the fact can leave an array or an object behind a text field,
    and a document must print through that rather than 500 on it."""
    if value is None or value == "" or value == []:
        return ""
    kind = definition.data_type
    if kind == "boolean":
        return translate("common.yes" if value else "common.no", locale)
    if kind == "select":
        return _option_label(definition, value, locale)
    if kind == "multi_select":
        values = value if isinstance(value, list) else [value]
        return ", ".join(_option_label(definition, v, locale) for v in values)
    if kind in ("date", "datetime"):
        return _date_text(value)
    if kind == "number":
        return _number_text(value, locale)
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if isinstance(value, dict):
        return ""
    return str(value)


def document_entries(
    definitions: Sequence[CustomFieldDefinition], custom: dict[str, Any] | None, locale: str
) -> list[tuple[str, str]]:
    """``(label, value)`` pairs for the flagged definitions that hold a value on this record.

    Label first because that is what the document prints beside it; a flagged field with
    nothing behind it yields nothing, for the reason ``render/context._entries`` states — an
    empty label on paper is worse than the field being absent.
    """
    out: list[tuple[str, str]] = []
    values = custom or {}
    for definition in printable(definitions):
        text = format_value(definition, values.get(definition.key), locale)
        if not text:
            continue
        label = pick_locale(definition.label_i18n, locale) or definition.key
        out.append((label, text))
    return out


def document_note(
    definitions: Sequence[CustomFieldDefinition], custom: dict[str, Any] | None, locale: str
) -> str:
    """The same pairs folded into one clause for a **line**: ``Website: klant.nl, Pakket: Pro``.

    A subscription's flagged fields ride the invoice line it raises, and a line is one string —
    the editor's description is a single-line input and the renderer prints the cell as is —
    so the pairs join on one line rather than stacking.
    """
    pairs = document_entries(definitions, custom, locale)
    return ", ".join(f"{label}: {value}" for label, value in pairs)
