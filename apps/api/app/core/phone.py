"""Shared phone-number validation (issue #256) — one implementation, not one per module.

Stored format is **E.164** (``+31612345678``): the only representation that is both validatable
and unambiguous across countries. Parsing goes through ``phonenumbers`` (the Python port of
Google's libphonenumber), never a hand-rolled regex — a regex can check the shape of a dial
code, not whether the number is possible in that country's plan.

The web's ``PhoneInput`` picker prefixes the dial code before posting, so a form submit already
arrives international. A **bulk import does not**: no real client list writes ``+31612345678``,
it writes ``0612345678``, and rejecting a whole file over that is the importer being wrong, not
the file. So callers may pass a ``region`` — the ISO country the number should be read *in* —
which is used only when the number carries no ``+`` of its own. Where that region comes from is
the caller's business (a record's own country, else ``org_settings.default_country``); this
module never guesses one, because guessing silently turns a Belgian number into a Dutch one.

This boundary is the authoritative gate every client (web, MCP, public API) shares; client-side
feedback is UX, not security.

``contacts.phone`` predates validation and holds freeform strings. Those stay readable: services
call this only when a phone value actually *changes*, so an unrelated edit to an old row never
fails on a number nobody touched.
"""

from __future__ import annotations

from typing import NoReturn

import phonenumbers

from app.errors import AppError


def normalize_phone(
    value: str | None, *, field: str = "phone", region: str | None = None
) -> str | None:
    """Return ``value`` as E.164, ``None`` for blank, or raise 422 (standard envelope).

    ``region`` (ISO 3166-1 alpha-2) reads a *national* number as belonging to that country. It
    is ignored for a number that already starts with ``+`` — an explicit country always wins
    over an assumed one, so a Belgian number in a Dutch org's import stays Belgian.
    """
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        parsed = phonenumbers.parse(raw, None if raw.startswith("+") else _region(region))
    except phonenumbers.NumberParseException:
        _reject(field)
    if not phonenumbers.is_valid_number(parsed):
        _reject(field)
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def _region(region: str | None) -> str | None:
    """An uppercased 2-letter region code, or ``None`` — never a value libphonenumber chokes on."""
    cleaned = (region or "").strip().upper()
    return cleaned if len(cleaned) == 2 and cleaned.isalpha() else None


def format_phone_international(value: str | None) -> str | None:
    """How a stored phone reads on a document (PDF seller block): E.164 renders international
    ("+31 20 624 1111"); a legacy freeform value prints exactly as stored — reformatting it
    would guess its country, which the retrofit promised not to do. The web's
    ``core/phone.ts::formatPhone`` is this function's mirror."""
    if not value or not value.startswith("+"):
        return value
    try:
        parsed = phonenumbers.parse(value, None)
    except phonenumbers.NumberParseException:
        return value
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL)


def _reject(field: str) -> NoReturn:
    raise AppError(
        "validation",
        "errors.invalid_phone",
        status_code=422,
        fields={field: "errors.invalid_phone"},
    )
