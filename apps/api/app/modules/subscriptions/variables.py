"""Note variables (#259), resolved on the API — the twin of the web's ``variables.ts``.

A standard subscription's notes are authored once with ``{{company_name}}``-style placeholders
and **the placeholders stay in storage**; they are resolved only at the edges. The web resolves
them wherever it *shows* a note. The API resolves them where a note leaves the building without
a browser in between: the cycle cron drafting an invoice at four in the morning, and the
editor's period picker handing the note over ready-made, so a hand-picked month and a
cron-drafted one print the same sentence (docs/INVOICING.md).

Same contract as the web, stated once more because two copies that disagree is the bug: a known
token with a value takes the value, a known token *without* one becomes the empty string (an
invoice must never print a raw variable), and an **unknown** token is left verbatim so its author
can see and fix it. The vocabulary is the same eight slugs, in the same order, and a test pins the
two lists against each other.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from app.core.money import fmt_money
from app.i18n import translate

#: The variables a note may draw on — the web's ``SUBSCRIPTION_NOTE_VARIABLES``, verbatim.
NOTE_VARIABLES: tuple[str, ...] = (
    "company_name",
    "subscription_name",
    "type",
    "amount",
    "interval",
    "included_hours",
    "start_date",
    "brand_name",
)

_TOKEN_RE = re.compile(r"\{\{\s*([a-zA-Z_]+)\s*\}\}")


def resolve_note_variables(source: str | None, values: dict[str, str | None]) -> str:
    """``source`` with every known ``{{key}}`` replaced (see the module docstring)."""
    if not source:
        return ""

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in NOTE_VARIABLES:
            return match.group(0)
        return values.get(key) or ""

    return _TOKEN_RE.sub(replace, source)


def _number_text(raw: Any, locale: str) -> str:
    """``fmtNumber``'s printed twin: a whole number bare, a fraction with the locale's comma."""
    try:
        amount = Decimal(str(raw)).normalize()
    except (InvalidOperation, ValueError):
        return str(raw)
    if amount == amount.to_integral_value():
        amount = amount.quantize(Decimal(1))
    text = f"{amount:f}"
    return text.replace(".", ",") if locale.startswith(("nl", "de")) else text


def note_values(
    *,
    company_name: str | None,
    subscription_name: str | None,
    type_label: str | None,
    amount: Decimal | None,
    currency: str,
    interval: str | None,
    included_hours: Decimal | None,
    start_date: date | None,
    brand_name: str | None,
    locale: str,
) -> dict[str, str | None]:
    """The resolved values for one agreement — the web's ``subscriptionNoteValues``, formatted
    the way the *document* formats the same figures (its money, its ``dd-mm-yyyy``)."""
    return {
        "company_name": company_name or None,
        "subscription_name": subscription_name or None,
        "type": type_label or None,
        "amount": fmt_money(amount, currency, locale) if amount is not None else None,
        "interval": translate(f"subscriptions.interval.{interval}", locale) if interval else None,
        "included_hours": (
            _number_text(included_hours, locale) if included_hours is not None else None
        ),
        "start_date": start_date.strftime("%d-%m-%Y") if start_date else None,
        "brand_name": brand_name or None,
    }
