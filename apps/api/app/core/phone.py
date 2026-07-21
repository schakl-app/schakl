"""Shared phone-number validation (issue #256) — one implementation, not one per module.

Stored format is **E.164** (``+31612345678``): the only representation that is both validatable
and unambiguous across countries. Parsing goes through ``phonenumbers`` (the Python port of
Google's libphonenumber), never a hand-rolled regex — a regex can check the shape of a dial
code, not whether the number is possible in that country's plan.

Input must already be international (``+…``): a national number is ambiguous without a country,
and the country lives in the web's ``PhoneInput`` picker, which prefixes the dial code before
posting. This boundary is the authoritative gate every client (web, MCP, public API) shares;
client-side feedback is UX, not security.

``contacts.phone`` predates validation and holds freeform strings. Those stay readable: services
call this only when a phone value actually *changes*, so an unrelated edit to an old row never
fails on a number nobody touched.
"""

from __future__ import annotations

from typing import NoReturn

import phonenumbers

from app.errors import AppError


def normalize_phone(value: str | None, *, field: str = "phone") -> str | None:
    """Return ``value`` as E.164, ``None`` for blank, or raise 422 (standard envelope)."""
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        parsed = phonenumbers.parse(raw, None)
    except phonenumbers.NumberParseException:
        _reject(field)
    if not phonenumbers.is_valid_number(parsed):
        _reject(field)
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def _reject(field: str) -> NoReturn:
    raise AppError(
        "validation",
        "errors.invalid_phone",
        status_code=422,
        fields={field: "errors.invalid_phone"},
    )
