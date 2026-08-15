"""schakl records → SnelStart payloads (epic #377). Business-licensed — see LICENSE.

Pure functions: they take rows and reference data and return dicts. Nothing here touches the
database or the network, which is what makes the arithmetic — the part with legal consequences
— testable without a credential.

Three rules run through the whole file.

**Derive the btw-soort, do not ask an admin to type it.** ``GET /btwtarieven`` returns
date-ranged percentages per ``btwSoort``, so a schakl rate of 21,00 on an invoice dated today
*is* ``Hoog`` by lookup, and 9,00 *is* ``Laag``. Asking an admin to map them by hand offers them
a way to get it wrong about tax. What genuinely cannot be derived — which revenue account each
rate books to — is the only thing the settings screen asks for.

**Refuse rather than guess where money lands.** No default grootboek means the push is refused
with a message naming the rate that has no account, not booked to whatever came first. A wrong
account is quiet: it reconciles, it balances, and it is discovered at year end.

**The per-line net comes from ``invoicing.calc.line_nets``**, never re-derived here. On a
tax-inclusive document the sum of independently rounded line nets is not the group base, and a
second implementation of that reconciliation is how the UBL export and the SnelStart boeking
start disagreeing by a cent with an accountant reading both.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from app.modules.invoicing.calc import Totals, line_nets, round_cents
from app.modules.invoicing.models import InvoiceKind, TaxCategory

#: SnelStart's line-level btw-soort (``VerkoopBoekingRegelModel.btwSoort``).
SOORT_NONE = "Geen"
SOORT_LOW = "Laag"
SOORT_HIGH = "Hoog"
SOORT_OTHER = "Overig"

#: SnelStart's document-level btw-soort (``VerkoopBoekingBtwRegelModel.btwSoort``) — a
#: *different* vocabulary from the line's, which is easy to miss and answers ``BOE-0082``
#: (*"een btw-soort ontbreekt, of komt niet overeen met de btwsoort van de grootboekrekening"*)
#: when the two are swapped.
SALES_LOW = "VerkopenLaag"
SALES_HIGH = "VerkopenHoog"
SALES_OTHER = "VerkopenOverig"
SALES_REVERSE = "VerkopenVerlegd"

_LINE_TO_SALES = {
    SOORT_LOW: SALES_LOW,
    SOORT_HIGH: SALES_HIGH,
    SOORT_OTHER: SALES_OTHER,
}

#: What a schakl tax category means when the rate table cannot answer. A fallback, and every
#: use of it is reported (:attr:`VatChoice.derived` is ``False``), because "we guessed how to
#: tax this" is exactly the sentence a finance integration must say out loud.
_CATEGORY_FALLBACK = {
    TaxCategory.STANDARD.value: SOORT_HIGH,
    TaxCategory.REDUCED.value: SOORT_LOW,
    TaxCategory.ZERO.value: SOORT_NONE,
    TaxCategory.EXEMPT.value: SOORT_NONE,
    TaxCategory.REVERSE_CHARGE.value: SOORT_NONE,
}

#: SnelStart's own field limits, measured against the live API. Enforced here rather than
#: discovered as ``REL-0007`` / ``BOE-0058`` halfway through a batch.
MAX_RELATION_NAME = 50
MAX_INVOICE_NUMBER = 25
MAX_DESCRIPTION = 250
MAX_BOEKSTUK = 25
MAX_KVK = 12
MAX_IBAN = 50
MAX_BIC = 15
MAX_WEBSITE = 100


class MappingError(Exception):
    """This record cannot be expressed in SnelStart's model, and no retry will change that.

    Carries an i18n key rather than a sentence: it surfaces in an error envelope (§9) and on a
    per-row failure list, both of which are read in the tenant's own language.
    """

    def __init__(self, message_key: str, *, detail: str = "") -> None:
        super().__init__(message_key)
        self.message_key = message_key
        self.detail = detail


@dataclass(frozen=True)
class VatChoice:
    """How one rate was expressed, and whether we were sure."""

    #: The line-level soort (``Geen``/``Laag``/``Hoog``/``Overig``).
    soort: str
    #: The document-level soort, or ``None`` when this rate contributes no btw entry at all.
    sales_soort: str | None
    #: ``True`` when the administration's own rate table produced this. ``False`` means the
    #: category fallback did, and the caller reports it.
    derived: bool


@dataclass
class BoekingPlan:
    """A ``verkoopboeking`` payload plus everything the caller has to tell a human about it."""

    payload: dict[str, Any]
    #: Rates the rate table could not confirm — reported, never silently accepted.
    guessed_rates: list[str] = field(default_factory=list)
    #: Grootboek numbers used, so a sync run can say where the money went without re-reading.
    ledger_codes: list[str] = field(default_factory=list)


def payload_hash(payload: Mapping[str, Any]) -> str:
    """A stable digest of what we are about to send.

    Sorted keys and a compact separator so the same content always hashes the same, whatever
    order a dict was built in. Compared against the stored ``push_hash`` to skip a write that
    would change nothing — which on a nightly relations sync is the difference between five
    hundred round-trips and none.
    """
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode()).hexdigest()


def clip(value: Any, limit: int) -> str | None:
    """Trim to SnelStart's limit, or ``None`` for nothing worth sending.

    Trimming rather than refusing, because a description one character over is not a reason to
    fail an invoice — but a *name* is a different judgement and :func:`relation_payload` refuses
    there instead, since a client silently renamed to 50 characters is a record nobody
    recognises.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:limit]


# --------------------------------------------------------------------------- #
# btw
# --------------------------------------------------------------------------- #
def vat_choice(
    *,
    rate_pct: Decimal,
    category: str,
    on: date,
    rates: Sequence[Mapping[str, Any]],
) -> VatChoice:
    """Which SnelStart btw-soort a schakl rate is, on a given date.

    ``rates`` is the administration's own ``/btwtarieven`` — ``{btwSoort, btwPercentage,
    datumVanaf, datumTotEnMet}`` — which is why the answer is a *lookup* and not a table
    compiled into this file. The Dutch low rate was 6% until 2019 and 9% after; an invoice
    dated 2018 must still book as ``Laag``, and a constant would have had to be wrong about one
    of them.
    """
    if category == TaxCategory.REVERSE_CHARGE.value:
        # Verlegd: the line carries no btw, the document says so. BOE-0062 — verlegd may not be
        # combined with other btw-soorten on one boeking — is the caller's problem, not ours.
        return VatChoice(soort=SOORT_NONE, sales_soort=SALES_REVERSE, derived=True)

    pct = Decimal(rate_pct or 0)
    if pct == 0 or category in (TaxCategory.ZERO.value, TaxCategory.EXEMPT.value):
        return VatChoice(soort=SOORT_NONE, sales_soort=None, derived=True)

    candidates: list[str] = []
    for row in rates:
        soort = str(row.get("btwSoort") or "")
        if soort in ("", SOORT_NONE):
            continue
        try:
            percentage = Decimal(str(row.get("btwPercentage")))
        except (ArithmeticError, TypeError, ValueError):
            continue
        if percentage != pct:
            continue
        if not _covers(row, on):
            continue
        candidates.append(soort)

    if candidates:
        preferred = _CATEGORY_FALLBACK.get(category)
        soort = preferred if preferred in candidates else candidates[0]
        return VatChoice(soort=soort, sales_soort=_LINE_TO_SALES.get(soort), derived=True)

    soort = _CATEGORY_FALLBACK.get(category, SOORT_OTHER)
    return VatChoice(
        soort=soort, sales_soort=_LINE_TO_SALES.get(soort), derived=False
    )


def _covers(row: Mapping[str, Any], on: date) -> bool:
    """Is this rate row in force on ``on``?

    Both bounds are inclusive (``datumTotEnMet`` says so in its name), and a missing bound is
    treated as open — the live data uses 1900-01-01 and 2400-12-31 sentinels rather than nulls,
    but a row that ever loses one must not silently stop matching.
    """
    from app.integrations.snelstart.client import parse_moment

    start = parse_moment(row.get("datumVanaf"))
    end = parse_moment(row.get("datumTotEnMet"))
    if start is not None and on < start.date():
        return False
    return not (end is not None and on > end.date())


# --------------------------------------------------------------------------- #
# companies → relaties
# --------------------------------------------------------------------------- #
def relation_payload(
    company: Any,
    *,
    country_id: str | None,
    invoice_email: str | None = None,
    contact_name: str | None = None,
    existing: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """A schakl company as a SnelStart ``relatie``.

    ``existing`` is the relation as SnelStart last returned it, and passing it is not an
    optimisation — it is a correctness requirement. ``PUT /relaties/{id}`` replaces the whole
    record, so a payload built only from schakl's fields would blank the bookkeeper's memo, the
    credit limit and the direct-debit mandate every time somebody edited a client's phone
    number. We merge onto what is there and overwrite only the fields schakl is the authority
    for.

    ``relatiesoort`` always keeps ``Klant``: ``BOE-0060`` refuses a sales boeking whose relation
    is not a customer, and a client of an agency that also supplies them would otherwise lose
    ``Leverancier`` on the first push.
    """
    name = (getattr(company, "name", "") or "").strip()
    if not name:
        raise MappingError("errors.snelstart.relation_name_missing")
    if len(name) > MAX_RELATION_NAME:
        # Refused, not trimmed. "Stichting Openbaar Onderwijs Noord-Holland Boven" cut to 50
        # characters is a record whose own bookkeeper cannot find it, and silently creating one
        # is worse than telling somebody to shorten the name they will have to search for.
        raise MappingError(
            "errors.snelstart.relation_name_too_long", detail=f"{len(name)}/{MAX_RELATION_NAME}"
        )

    payload: dict[str, Any] = dict(existing or {})
    # Read-only on the way back in: SnelStart returns them and rejects nothing, but sending a
    # server-computed field back is how a client starts writing values it does not own.
    for read_only in ("uri", "inkoopBoekingenUri", "verkoopBoekingenUri", "modifiedOn"):
        payload.pop(read_only, None)

    soorten = list(payload.get("relatiesoort") or [])
    if "Klant" not in soorten:
        soorten.append("Klant")
    payload["relatiesoort"] = soorten
    payload["naam"] = name

    address = _address_payload(company, country_id, contact_name)
    if address:
        payload["vestigingsAdres"] = {**(payload.get("vestigingsAdres") or {}), **address}

    for field_name, value in (
        ("telefoon", clip(getattr(company, "phone", None), 25)),
        ("btwNummer", clip(getattr(company, "vat_number", None), 32)),
        ("kvkNummer", clip(getattr(company, "coc_number", None), MAX_KVK)),
        ("websiteUrl", clip(getattr(company, "website", None), MAX_WEBSITE)),
    ):
        # An empty schakl field does not blank SnelStart's. A CRM that has never been filled in
        # is not an instruction to erase what the bookkeeper typed — the same "absent means
        # leave alone" rule §18 states for a bulk edit.
        if value is not None:
            payload[field_name] = value

    #: The relatiecode is SnelStart's own customer number and the natural join with schakl's
    #: ``client_number``. Only ever set on a *create*: renumbering an existing relation would
    #: rewrite what appears on every document that mentions it, and ``REL-0008`` refuses a
    #: duplicate anyway.
    if existing is None:
        code = _relatiecode(getattr(company, "client_number", None))
        if code is not None:
            payload["relatiecode"] = code

    email = (invoice_email or getattr(company, "invoice_email", None) or "").strip()
    if email:
        payload["email"] = clip(email, 255)
        # `shouldSend` stays whatever SnelStart has: whether *SnelStart* mails the invoice is
        # the bookkeeper's decision and turning it on here would double-send a document schakl
        # already sent. We supply the address; we do not choose to use it.
        current = dict(payload.get("factuurEmailVersturen") or {})
        current["email"] = clip(email, 255)
        current.setdefault("shouldSend", False)
        payload["factuurEmailVersturen"] = current

    return payload


def _relatiecode(client_number: Any) -> int | None:
    """schakl's client number as a SnelStart relatiecode, or nothing.

    ``client_number`` is free text in schakl (an agency may use ``KL-0042``), and
    ``relatiecode`` is a 32-bit integer. A code we cannot express is simply not sent — SnelStart
    allocates its own — rather than mangled into a number that collides with a real one.
    Negative codes are refused because SnelStart reserves them for its own system relations
    (``-1 Leverancier onbekend``, ``-2 Klant onbekend``).
    """
    if client_number in (None, ""):
        return None
    digits = "".join(ch for ch in str(client_number) if ch.isdigit())
    if not digits:
        return None
    try:
        code = int(digits)
    except ValueError:
        return None
    return code if 0 < code < 2_147_483_647 else None


def _address_payload(
    company: Any, country_id: str | None, contact_name: str | None
) -> dict[str, Any]:
    """schakl's split address as SnelStart's single ``straat`` line.

    schakl separates ``address_line1`` from ``house_number`` (the postcode lookup did that);
    SnelStart has one 50-character street field. Joining is lossless in the direction that
    matters and the only alternative — a second address field SnelStart does not have — is not
    one.
    """
    street = " ".join(
        part
        for part in (
            (getattr(company, "address_line1", None) or "").strip(),
            (getattr(company, "house_number", None) or "").strip(),
        )
        if part
    )
    address: dict[str, Any] = {}
    if street:
        address["straat"] = street[:50]
    for key, value in (
        ("postcode", clip(getattr(company, "postal_code", None), 25)),
        ("plaats", clip(getattr(company, "city", None), 50)),
        ("contactpersoon", clip(contact_name, 50)),
    ):
        if value is not None:
            address[key] = value
    if country_id:
        address["land"] = {"id": country_id}
    return address


# --------------------------------------------------------------------------- #
# invoices → verkoopboekingen
# --------------------------------------------------------------------------- #
def boeking_payload(
    invoice: Any,
    lines: Sequence[Any],
    totals: Totals,
    *,
    relation_id: str,
    ledger_for: Any,
    vat_rates: Sequence[Mapping[str, Any]],
    existing_id: str | None = None,
) -> BoekingPlan:
    """An issued schakl invoice as a SnelStart ``verkoopboeking``.

    A **boeking**, not a ``verkooporder``, and that is the load-bearing decision. An order is a
    document SnelStart lays out and prints; a boeking is the ledger entry. schakl already
    rendered this invoice, already owns its number and has usually already mailed the PDF —
    pushing an order would make SnelStart print a second, differently-designed copy of a
    document the client is holding. The boeking books the money and takes our PDF as its
    attachment, which is what an accountant asks for at year end.

    ``ledger_for(line)`` returns ``(grootboek_id, grootboek_number)`` for a line, or ``None``.
    Injected rather than looked up here so this stays a pure function, and because "which
    account" is a stored per-rate mapping only the service can read. It returns **both** halves
    because the API is addressed by uuid and a human is not: a sync run that reports *"booked to
    b3e1e950-…"* has told nobody anything.
    """
    number = clip(getattr(invoice, "number", None), MAX_INVOICE_NUMBER)
    if not number:
        # An unissued invoice has no number, and ``factuurnummer`` is required (BOE-0058). The
        # caller refuses drafts long before here; this is the belt.
        raise MappingError("errors.snelstart.invoice_not_issued")
    if not lines:
        raise MappingError("errors.snelstart.invoice_no_lines")

    issue_date = getattr(invoice, "issue_date", None) or date.today()
    credit = getattr(invoice, "kind", None) == InvoiceKind.CREDIT_NOTE.value
    # A credit note is the same boeking with every amount negated. SnelStart has no separate
    # document type for one, and schakl already stores a credit note's totals as negative — so
    # the sign travels on its own and this flag only decides the description.
    inclusive = bool(getattr(invoice, "prices_include_tax", False))
    nets = line_nets(list(lines), totals.groups, inclusive)

    guessed: list[str] = []
    ledger_codes: list[str] = []
    regels: list[dict[str, Any]] = []
    for line, net in zip(lines, nets, strict=True):
        choice = vat_choice(
            rate_pct=Decimal(line.tax_rate_pct or 0),
            category=line.tax_category,
            on=issue_date,
            rates=vat_rates,
        )
        if not choice.derived:
            label = f"{Decimal(line.tax_rate_pct or 0)}%"
            if label not in guessed:
                guessed.append(label)
        resolved = ledger_for(line)
        if not resolved:
            raise MappingError(
                "errors.snelstart.ledger_unmapped",
                detail=str(getattr(line, "tax_name", "") or line.tax_rate_pct),
            )
        ledger_id, ledger_code = resolved
        if ledger_code not in ledger_codes:
            ledger_codes.append(ledger_code)
        regels.append(
            {
                "omschrijving": clip(line.description, MAX_DESCRIPTION) or number,
                "grootboek": {"id": ledger_id},
                "bedrag": round_cents(Decimal(net)),
                "btwSoort": choice.soort,
            }
        )

    btw_rows: list[dict[str, Any]] = []
    for group in totals.groups:
        choice = vat_choice(
            rate_pct=group.rate_pct, category=group.category, on=issue_date, rates=vat_rates
        )
        if choice.sales_soort is None:
            continue
        tax = round_cents(Decimal(group.tax))
        if tax == 0 and choice.sales_soort != SALES_REVERSE:
            # A zero-btw line contributes no btw row. A *verlegd* one does, because the whole
            # point of the entry is to declare that the tax was shifted — a fact worth stating
            # even though its amount is nil.
            continue
        btw_rows.append({"btwSoort": choice.sales_soort, "btwBedrag": tax})

    payload: dict[str, Any] = {
        "factuurnummer": number,
        "factuurdatum": issue_date,
        "klant": {"id": relation_id},
        "omschrijving": _boeking_description(invoice, number, credit),
        "factuurbedrag": round_cents(Decimal(totals.total)),
        "boekingsregels": regels,
        "btw": btw_rows,
    }

    boekstuk = clip(getattr(invoice, "reference", None), MAX_BOEKSTUK)
    if boekstuk:
        payload["boekstuk"] = boekstuk

    term = _payment_term(invoice)
    if term is not None:
        payload["betalingstermijn"] = term

    if existing_id:
        # A PUT wants the id in the body as well; ALG-0101 refuses a mismatch with the URL.
        payload["id"] = existing_id

    return BoekingPlan(payload=payload, guessed_rates=guessed, ledger_codes=ledger_codes)


def _boeking_description(invoice: Any, number: str, credit: bool) -> str:
    """What the boeking is called in a ledger listing.

    The client's name, because that is what somebody scanning a debtor list is looking for —
    the invoice number is already its own column. A credit note says so, since a negative
    amount alone is not a label.
    """
    customer = getattr(invoice, "customer", None) or {}
    name = str(customer.get("name") or "").strip()
    prefix = "Creditnota" if credit else "Factuur"
    text = f"{prefix} {number}" + (f" — {name}" if name else "")
    return text[:MAX_DESCRIPTION]


def _payment_term(invoice: Any) -> int | None:
    """Days between issue and due, as SnelStart's ``betalingstermijn``.

    Derived rather than copied from the org's default, because an invoice may carry a due date
    somebody set by hand and SnelStart's dunning runs off this number. Clamped to the range
    ``REL-0003`` documents for a relation's term, and dropped entirely when the dates cannot
    produce one.
    """
    issue = getattr(invoice, "issue_date", None)
    due = getattr(invoice, "due_date", None)
    if not issue or not due:
        return None
    days = (due - issue).days
    return days if -365 <= days <= 1000 else None


# --------------------------------------------------------------------------- #
# products → artikelen
# --------------------------------------------------------------------------- #
def article_code_error(
    code: str, *, kind: str | None, max_length: int | None
) -> str | None:
    """Is this schakl product code writable as an ``artikelcode``? An i18n key, or ``None``.

    Both rules are **per administration** (``companyInfo.artikelcodeSoort`` and
    ``artikelcodeMaxLengte``) rather than properties of the API, which is why they are checked
    against stored observations and not against constants. A tenant whose administration is set
    to ``Numeriek`` cannot have a product called ``WEB-01`` in SnelStart, and finding that out
    per row halfway through a sync — as ``ART-0003`` — is a worse screen than being told once.
    """
    text = (code or "").strip()
    if not text:
        return "errors.snelstart.article_code_missing"
    if kind == "Numeriek" and not text.isdigit():
        return "errors.snelstart.article_code_not_numeric"
    if max_length and len(text) > max_length:
        return "errors.snelstart.article_code_too_long"
    return None


def article_payload(
    product: Any,
    *,
    revenue_group_id: str,
    existing: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """A schakl product as a SnelStart ``artikel``.

    Merged onto ``existing`` for the same reason a relation is: a PUT replaces the record, and
    stock levels, supplier links and sub-articles are SnelStart's own — schakl has no opinion
    about them and must not blank them to express that.

    The ``artikelOmzetgroep`` is what decides which grootboek and which btw-soort the article
    books to, so it is required rather than defaulted: an article in the wrong revenue group
    books VAT at the wrong rate, silently and for as long as nobody checks.
    """
    payload: dict[str, Any] = dict(existing or {})
    for read_only in ("uri", "modifiedOn", "technischeVoorraad", "vrijeVoorraad"):
        payload.pop(read_only, None)

    payload["artikelcode"] = (getattr(product, "code", "") or "").strip()
    payload["omschrijving"] = clip(getattr(product, "name", None), MAX_DESCRIPTION) or ""
    payload["artikelOmzetgroep"] = {"id": revenue_group_id}
    price = getattr(product, "unit_price", None)
    if price is not None:
        payload["verkoopprijs"] = round_cents(Decimal(price))
    unit = clip(getattr(product, "unit", None), 20)
    if unit is not None:
        payload["eenheid"] = unit
    payload["isNonActief"] = not bool(getattr(product, "active", True))
    return payload
