"""Translating between a Timeon customer and a schakl ``Company``. Business-licensed — see LICENSE.

For its first months this integration *paired* clients and wrote nothing about them, in either
direction: a client made in schakl never reached Timeon, a customer made in Timeon was a warning
on every run, and a corrected address stayed corrected on one side (``docs/TIMEON.md`` §5b). This
file is what a client means on each side, in the neutral shape :mod:`mapping` compares hours in
and :mod:`project_mapping` compares projects in.

**Ten fields, because ten is what both systems can say**: the name, the client number, whether
the client is still active, the e-mail address, the phone number, street, postal code, city,
country and VAT number. Everything else — schakl's legal name, status beyond "archived or not",
assignees and custom fields; Timeon's PO number, remarks, customer group, default distance — has
no counterpart, is never compared, and is *carried* across a save (:func:`customer_update_payload`),
because Timeon's saves replace.

Five mapping decisions carry a reason.

**Timeon's three address lines are a street, a postal code and a city.** Its own form labels
``addressLine1`` "address", ``addressLine2`` "postcode" and ``addressLine3`` "city", and its
address picker reads them back under exactly those names. schakl keeps the street and the house
number apart (#241), so they are joined going out and split coming in — and the split is
conservative: a line that does not end in something that reads as a house number stays whole in
``address_line1``, which renders identically wherever the two are joined back together.

**A value is compared in a canonical form and written in its own.** ``4331 AB`` and ``4331AB``
are one postal code, ``NL 8123.45.678 B01`` and ``NL812345678B01`` one VAT number, ``06 1234 5678``
and ``+31612345678`` one phone number. Comparing the raw strings would report a difference on
every run that no write could ever settle, since each side would keep its own spelling.

**A key Timeon's list row does not carry is a sentinel, not an empty value.** Its OpenAPI
document describes no response bodies, so which fields a ``customer/list`` row holds is known
only from what its own screens read off one. A field whose key is absent canonicalises to
:data:`~mapping.UNRESOLVED` — *we do not know* — rather than to ``""``, because reading "absent"
as "empty" would make the sync offer to blank a VAT number here on the strength of a column the
list never had. The same goes for a value that cannot cross: a phone number no country's plan
recognises, an address field holding three e-mail addresses, a country id the table cannot name.

**A customer who is a person has no name to write.** Timeon's customer is a company (``name``)
or a person (``firstname`` / ``lastname``, ``name`` null). schakl has one name. The person's
joined name is what a client created *here* is called, and after that the field is the sentinel:
writing a name onto the row would turn a person into a company over there.

**The e-mail address is the invoice address.** Timeon holds one address per customer and sends
its invoices to it; schakl's counterpart for that is ``invoice_email``.
"""

from __future__ import annotations

import re
from typing import Any

import phonenumbers

from app.integrations.timeon.mapping import UNRESOLVED

#: What the sync compares on a client, and therefore what it may write. In reading order.
CUSTOMER_FIELDS = (
    "name",
    "number",
    "active",
    "email",
    "phone",
    "address",
    "postal_code",
    "city",
    "country",
    "vat_number",
)

#: schakl's one status that means "we are done with this client".
ARCHIVED = "archived"

#: The keys ``CustomerUpdate`` accepts (Timeon's schema, ``additionalProperties: false``). A save
#: replaces, so everything here is carried over from the customer as just read.
_UPDATE_KEYS = (
    "customerID",
    "name",
    "isActive",
    "addressLine1",
    "addressLine2",
    "addressLine3",
    "firstname",
    "lastname",
    "phoneNumber",
    "customerNumber",
    "emailAddress",
    "remark",
    "vatNumber",
    "countryID",
    "defaultDistance",
    "autoApprove",
    "defaultBillable",
    "internalRemark",
    "poNumber",
    "customerGroupID",
)

#: A street line ending in a house number: ``Dorpsstraat 12``, ``Lange Noordstraat 4-6``,
#: ``Plein 1940 12a``. Deliberately narrow — the number is the **last word** and starts with a
#: digit — so a line it does not recognise (``Kade 12 bis``, ``Postbus``) is left whole.
_HOUSE_NUMBER = re.compile(r"^(?P<street>.*[^\W\d].*)\s+(?P<number>\d+[\w\-/]{0,8})$")

_EMAIL = re.compile(r"^[^@\s;,]+@[^@\s;,]+\.[^@\s;,]+$")


def _text(value: Any) -> str:
    return " ".join(str(value).split()) if value not in (None, "") else ""


def _squash(value: Any) -> str:
    """Upper-cased with every space and dot removed: one postal code, one VAT number."""
    return re.sub(r"[\s.]", "", str(value or "")).upper()


def phone_token(value: Any, region: str | None) -> str:
    """A phone number as E.164, ``""`` for none, or the sentinel where no plan recognises it.

    schakl stores E.164 and refuses anything else (#256); Timeon stores what was typed. A number
    that cannot be parsed could never be written here, so it is not a difference the sync may
    report — it is a value that cannot cross.
    """
    raw = _text(value)
    if not raw:
        return ""
    try:
        parsed = phonenumbers.parse(raw, None if raw.startswith("+") else region)
    except phonenumbers.NumberParseException:
        return UNRESOLVED
    if not phonenumbers.is_valid_number(parsed):
        return UNRESOLVED
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def email_token(value: Any) -> str:
    """One address, lower-cased — or the sentinel for the list Timeon lets its field hold."""
    raw = _text(value)
    if not raw:
        return ""
    return raw.lower() if _EMAIL.match(raw) else UNRESOLVED


def join_address(street: Any, number: Any) -> str:
    return _text(f"{street or ''} {number or ''}")


def split_address(line: str) -> tuple[str | None, str | None]:
    """``"Dorpsstraat 12"`` → ``("Dorpsstraat", "12")``; anything else stays whole."""
    line = _text(line)
    if not line:
        return None, None
    found = _HOUSE_NUMBER.match(line)
    if found is None:
        return line, None
    return found.group("street").strip(), found.group("number").strip()


def is_person(row: dict[str, Any]) -> bool:
    """A customer with no company name and a surname: Timeon's "person" variant."""
    return not _text(row.get("name")) and bool(_text(row.get("lastname")))


def display_name(row: dict[str, Any]) -> str:
    """What this customer is called, whichever variant it is."""
    return _text(row.get("name")) or _text(
        f"{row.get('firstname') or ''} {row.get('lastname') or ''}"
    )


def country_maps(rows: list[dict[str, Any]]) -> tuple[dict[str, str], dict[str, str]]:
    """``(iso by Timeon id, Timeon id by iso)`` from Timeon's country table.

    Read defensively: the code is spelled ``isO2`` on the wire (a serialiser's idea of
    ``ISO2``), and any of the obvious spellings is accepted so a corrected one does not silently
    empty the table.
    """
    iso_by_id: dict[str, str] = {}
    id_by_iso: dict[str, str] = {}
    for row in rows:
        ident = row.get("countryID")
        code = next(
            (
                str(row[key]).strip().upper()
                for key in ("isO2", "iso2", "ISO2", "isoCode", "code")
                if row.get(key)
            ),
            "",
        )
        if ident in (None, "") or len(code) != 2:
            continue
        iso_by_id[str(ident)] = code
        id_by_iso.setdefault(code, str(ident))
    return iso_by_id, id_by_iso


def neutral_from_company(company: Any, *, region: str | None) -> dict[str, Any]:
    """schakl's client in the ten comparable fields. ``region`` reads a national phone number
    that predates validation; everything written since #256 is E.164 already."""
    country = _text(getattr(company, "country", None)).upper()
    return {
        "name": _text(getattr(company, "name", "")),
        "number": _text(getattr(company, "client_number", None)),
        "active": str(getattr(company, "status", "")) != ARCHIVED,
        "email": email_token(getattr(company, "invoice_email", None)),
        "phone": phone_token(getattr(company, "phone", None), country or region),
        "address": join_address(
            getattr(company, "address_line1", None), getattr(company, "house_number", None)
        ),
        "postal_code": _squash(getattr(company, "postal_code", None)),
        "city": _text(getattr(company, "city", None)),
        "country": country,
        "vat_number": _squash(getattr(company, "vat_number", None)),
    }


def neutral_from_customer_row(
    row: dict[str, Any], *, iso_by_id: dict[str, str], region: str | None
) -> dict[str, Any]:
    """Timeon's customer in the same ten fields, with the sentinel for what cannot be known."""

    def field(key: str, shape: Any) -> Any:
        return shape(row.get(key)) if key in row else UNRESOLVED

    country = UNRESOLVED
    if "countryID" in row:
        ident = row.get("countryID")
        country = "" if ident in (None, "", 0) else iso_by_id.get(str(ident), UNRESOLVED)
    phone_region = country if country not in ("", UNRESOLVED) else region
    return {
        "name": UNRESOLVED if is_person(row) else _text(row.get("name")),
        "number": field("customerNumber", _text),
        # Absent reads as *we do not know*, never as inactive: the fake's and the importer's
        # rows carry no flag, and treating that as "closed" would archive a register.
        "active": bool(row["isActive"]) if row.get("isActive") is not None else UNRESOLVED,
        "email": field("emailAddress", email_token),
        "phone": field("phoneNumber", lambda v: phone_token(v, phone_region)),
        "address": field("addressLine1", _text),
        "postal_code": field("addressLine2", _squash),
        "city": field("addressLine3", _text),
        "country": country,
        "vat_number": field("vatNumber", _squash),
    }


def is_blank(value: Any) -> bool:
    """Nothing to lose: an empty field holds nobody's opinion. ``False`` is a value."""
    return value is None or value == ""


def company_values(
    row: dict[str, Any], remote: dict[str, Any], fields: list[str] | tuple[str, ...]
) -> dict[str, Any]:
    """The named fields as the columns a pull writes on a ``Company``.

    Taken from the **row** where the spelling is somebody's (a postal code keeps its space, a
    VAT number its dots) and from the neutral value where the canonical form *is* what schakl
    stores (the phone number, the country). ``status`` is set only across the archived line:
    a lead that Timeon calls active stays a lead.
    """
    values: dict[str, Any] = {}
    for name in fields:
        value = remote.get(name)
        if value == UNRESOLVED:
            continue
        if name == "name":
            if value:
                values["name"] = value
        elif name == "number":
            values["client_number"] = value or None
        elif name == "active":
            values["status"] = "active" if value else ARCHIVED
        elif name == "email":
            values["invoice_email"] = value or None
        elif name == "phone":
            values["phone"] = value or None
        elif name == "address":
            values["address_line1"], values["house_number"] = split_address(value)
        elif name == "postal_code":
            values["postal_code"] = _text(row.get("addressLine2")) or None
        elif name == "city":
            values["city"] = value or None
        elif name == "country":
            values["country"] = value or None
        elif name == "vat_number":
            values["vat_number"] = _text(row.get("vatNumber")) or None
    return values


def _remote_fields(
    company: Any,
    local: dict[str, Any],
    fields: list[str] | tuple[str, ...],
    id_by_iso: dict[str, str],
) -> dict[str, Any]:
    """The named fields in Timeon's vocabulary. A country Timeon's table cannot name is left
    out rather than sent as nothing — the caller sees it missing and does not call it agreed."""
    out: dict[str, Any] = {}
    for name in fields:
        value = local.get(name)
        if value == UNRESOLVED:
            continue
        if name == "name":
            out["name"] = value
        elif name == "number":
            out["customerNumber"] = value or None
        elif name == "active":
            out["isActive"] = bool(value)
        elif name == "email":
            out["emailAddress"] = value or None
        elif name == "phone":
            out["phoneNumber"] = value or None
        elif name == "address":
            out["addressLine1"] = value or None
        elif name == "postal_code":
            out["addressLine2"] = _text(getattr(company, "postal_code", None)) or None
        elif name == "city":
            out["addressLine3"] = value or None
        elif name == "country":
            if not value:
                out["countryID"] = None
            elif value in id_by_iso:
                out["countryID"] = int(id_by_iso[value])
        elif name == "vat_number":
            out["vatNumber"] = _text(getattr(company, "vat_number", None)) or None
    return out


#: Which Timeon key carries each field, so a caller can tell what a payload actually said.
REMOTE_KEY = {
    "name": "name",
    "number": "customerNumber",
    "active": "isActive",
    "email": "emailAddress",
    "phone": "phoneNumber",
    "address": "addressLine1",
    "postal_code": "addressLine2",
    "city": "addressLine3",
    "country": "countryID",
    "vat_number": "vatNumber",
}

#: What ``CreateCustomer`` accepts. Narrower than the save: a VAT number and the active flag
#: cannot be said at birth, so they follow on the next run as ordinary differences.
_CREATE_FIELDS = ("name", "number", "email", "phone", "address", "postal_code", "city", "country")


def customer_create_payload(
    company: Any,
    local: dict[str, Any],
    *,
    id_by_iso: dict[str, str],
    fallback_country: str | None,
) -> dict[str, Any]:
    """The body for ``POST /api/customer`` — a company, never a person.

    Timeon's own dialog will not submit without a country and opens on the organisation's own;
    a client with none here therefore goes over under the org's default country, which is the
    same assumption schakl already makes when it reads that client's phone number.
    """
    payload = {
        key: value
        for key, value in _remote_fields(company, local, _CREATE_FIELDS, id_by_iso).items()
        if value is not None
    }
    if "countryID" not in payload and (fallback_country or "").upper() in id_by_iso:
        payload["countryID"] = int(id_by_iso[(fallback_country or "").upper()])
    payload["defaultBillable"] = True
    return payload


def created_fields(payload: dict[str, Any], local: dict[str, Any]) -> tuple[str, ...]:
    """The fields a create actually said — what the two sides agree on from birth."""
    said = []
    for name in CUSTOMER_FIELDS:
        if name == "active":
            said.append(name)  # a new customer is active, and only active clients are sent
        elif name in _CREATE_FIELDS and (
            REMOTE_KEY[name] in payload or is_blank(local.get(name))
        ):
            if name == "country" and is_blank(local.get(name)) and "countryID" in payload:
                continue  # sent under the org's default: Timeon now says more than schakl does
            said.append(name)
    return tuple(said)


def customer_update_payload(
    current: dict[str, Any],
    company: Any,
    local: dict[str, Any],
    fields: list[str],
    *,
    id_by_iso: dict[str, str],
) -> tuple[dict[str, Any], list[str]]:
    """The body for ``customer/save`` and the fields it carries: the customer **as just read**,
    with only the named fields changed.

    Whole, because the save replaces. *Only* the named fields, because the others are either in
    step or undecided — and an undecided field overwritten as a side effect of a rename is the
    sync answering a question it had just reported it could not answer.
    """
    payload = {key: current[key] for key in _UPDATE_KEYS if current.get(key) is not None}
    ours = _remote_fields(company, local, fields, id_by_iso)
    payload.update(ours)
    written = [name for name in fields if REMOTE_KEY[name] in ours]
    return payload, written
