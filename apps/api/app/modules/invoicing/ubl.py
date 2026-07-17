"""UBL 2.1 invoice XML (issue #207) — the accounting bridge that works today.

Exact Online, SnelStart, Moneybird and e-Boekhouden all *import* UBL sales invoices, so a
standards-correct UBL document is the integration every Dutch bookkeeper can use before any
OAuth provider ships (#31 stays the live-sync issue). Shape follows EN 16931's UBL binding:

- **Tax per rate group** (``cac:TaxSubtotal``) — exactly the groups ``calc.py`` computed.
- **Line amounts are net.** On a tax-inclusive document each line's net is derived and the
  rounding drift is folded into the group's largest line, so ``Σ line nets == Σ group
  bases == TaxExclusiveAmount`` and a strict validator reconciles to the cent.
- **Category codes** from UNCL5305's EN-16931 subset: any positive rate is ``S`` (the
  percent differentiates standard/reduced), ``Z`` zero, ``E`` exempt, ``AE`` reverse
  charge — the latter two with the exemption reason validators demand.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from decimal import Decimal
from typing import Any

from app.modules.invoicing.calc import CENTS, TaxGroup, Totals, round_cents
from app.modules.invoicing.models import InvoiceKind, TaxCategory

_CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
_CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
_INVOICE_NS = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"

_CATEGORY_CODES = {
    TaxCategory.STANDARD.value: "S",
    TaxCategory.REDUCED.value: "S",   # EN 16931: the percent, not the code, says "reduced"
    TaxCategory.ZERO.value: "Z",
    TaxCategory.EXEMPT.value: "E",
    TaxCategory.REVERSE_CHARGE.value: "AE",
}

_UNIT_CODES = {"uur": "HUR", "hour": "HUR", "hours": "HUR", "u": "HUR", "h": "HUR"}


def _fmt(amount: Decimal) -> str:
    return str(amount.quantize(CENTS))


def _el(parent: ET.Element, ns: str, tag: str, text: str | None = None, **attrs: str):
    element = ET.SubElement(parent, f"{{{ns}}}{tag}", attrs)
    if text is not None:
        element.text = text
    return element


def _party(parent: ET.Element, role: str, details: dict[str, Any]) -> None:
    wrapper = _el(parent, _CAC, role)
    party = _el(wrapper, _CAC, "Party")
    name = details.get("name") or ""
    party_name = _el(party, _CAC, "PartyName")
    _el(party_name, _CBC, "Name", name)
    address = _el(party, _CAC, "PostalAddress")
    if details.get("address_line1"):
        _el(address, _CBC, "StreetName", details["address_line1"])
    if details.get("address_line2"):
        _el(address, _CBC, "AdditionalStreetName", details["address_line2"])
    if details.get("city"):
        _el(address, _CBC, "CityName", details["city"])
    if details.get("postal_code"):
        _el(address, _CBC, "PostalZone", details["postal_code"])
    if details.get("country"):
        country = _el(address, _CAC, "Country")
        _el(country, _CBC, "IdentificationCode", str(details["country"]).upper())
    if details.get("vat_number"):
        tax_scheme = _el(party, _CAC, "PartyTaxScheme")
        _el(tax_scheme, _CBC, "CompanyID", details["vat_number"])
        scheme = _el(tax_scheme, _CAC, "TaxScheme")
        _el(scheme, _CBC, "ID", "VAT")
    legal = _el(party, _CAC, "PartyLegalEntity")
    _el(legal, _CBC, "RegistrationName", name)
    if details.get("coc_number"):
        _el(legal, _CBC, "CompanyID", details["coc_number"])
    if details.get("email"):
        contact = _el(party, _CAC, "Contact")
        _el(contact, _CBC, "ElectronicMail", details["email"])


def _line_nets(lines: list[Any], groups: tuple[TaxGroup, ...], include_tax: bool) -> list[Decimal]:
    """Net amount per line; on inclusive documents the per-group rounding drift lands on the
    group's largest line so every sum reconciles exactly."""
    nets: list[Decimal] = []
    for line in lines:
        if include_tax and line.tax_category not in (
            TaxCategory.EXEMPT.value, TaxCategory.REVERSE_CHARGE.value
        ) and line.tax_rate_pct != 0:
            factor = Decimal(1) + Decimal(line.tax_rate_pct) / Decimal(100)
            nets.append(round_cents(Decimal(line.amount) / factor))
        else:
            nets.append(Decimal(line.amount))
    for group in groups:
        indexes = [
            i for i, line in enumerate(lines)
            if Decimal(line.tax_rate_pct) == group.rate_pct
            and line.tax_category == group.category
        ]
        if not indexes:
            continue
        delta = group.base - sum(nets[i] for i in indexes)
        if delta:
            largest = max(indexes, key=lambda i: abs(nets[i]))
            nets[largest] += delta
    return nets


def invoice_ubl(
    invoice: Any,
    lines: list[Any],
    totals: Totals,
    seller: dict[str, Any],
) -> bytes:
    """Serialize one issued invoice as UBL 2.1 XML (EN 16931 shape)."""
    ET.register_namespace("cac", _CAC)
    ET.register_namespace("cbc", _CBC)
    ET.register_namespace("", _INVOICE_NS)
    root = ET.Element(f"{{{_INVOICE_NS}}}Invoice")

    _el(root, _CBC, "CustomizationID", "urn:cen.eu:en16931:2017")
    _el(root, _CBC, "ID", invoice.number or str(invoice.id))
    if invoice.issue_date:
        _el(root, _CBC, "IssueDate", invoice.issue_date.isoformat())
    if invoice.due_date:
        _el(root, _CBC, "DueDate", invoice.due_date.isoformat())
    type_code = "381" if invoice.kind == InvoiceKind.CREDIT_NOTE.value else "380"
    _el(root, _CBC, "InvoiceTypeCode", type_code)
    if invoice.notes:
        _el(root, _CBC, "Note", invoice.notes)
    _el(root, _CBC, "DocumentCurrencyCode", invoice.currency)
    _el(root, _CBC, "BuyerReference", invoice.reference or invoice.number or "")

    _party(root, "AccountingSupplierParty", seller)
    _party(root, "AccountingCustomerParty", invoice.customer or {})

    if seller.get("iban"):
        means = _el(root, _CAC, "PaymentMeans")
        _el(means, _CBC, "PaymentMeansCode", "30")  # credit transfer
        if invoice.number:
            _el(means, _CBC, "PaymentID", invoice.number)
        account = _el(means, _CAC, "PayeeFinancialAccount")
        _el(account, _CBC, "ID", seller["iban"])

    currency = invoice.currency
    tax_total = _el(root, _CAC, "TaxTotal")
    _el(tax_total, _CBC, "TaxAmount", _fmt(totals.tax_total), currencyID=currency)
    for group in totals.groups:
        subtotal = _el(tax_total, _CAC, "TaxSubtotal")
        _el(subtotal, _CBC, "TaxableAmount", _fmt(group.base), currencyID=currency)
        _el(subtotal, _CBC, "TaxAmount", _fmt(group.tax), currencyID=currency)
        category = _el(subtotal, _CAC, "TaxCategory")
        _el(category, _CBC, "ID", _CATEGORY_CODES.get(group.category, "S"))
        _el(category, _CBC, "Percent", str(group.rate_pct))
        if group.category in (TaxCategory.EXEMPT.value, TaxCategory.REVERSE_CHARGE.value):
            _el(category, _CBC, "TaxExemptionReason", group.name or group.category)
        scheme = _el(category, _CAC, "TaxScheme")
        _el(scheme, _CBC, "ID", "VAT")

    nets = _line_nets(lines, totals.groups, invoice.prices_include_tax)
    line_extension = round_cents(sum(nets, Decimal(0)))
    paid = Decimal(getattr(invoice, "paid_total", 0) or 0)
    monetary = _el(root, _CAC, "LegalMonetaryTotal")
    _el(monetary, _CBC, "LineExtensionAmount", _fmt(line_extension), currencyID=currency)
    _el(monetary, _CBC, "TaxExclusiveAmount", _fmt(totals.subtotal), currencyID=currency)
    _el(monetary, _CBC, "TaxInclusiveAmount", _fmt(totals.total), currencyID=currency)
    if paid:
        _el(monetary, _CBC, "PrepaidAmount", _fmt(paid), currencyID=currency)
    _el(monetary, _CBC, "PayableAmount", _fmt(totals.total - paid), currencyID=currency)

    for index, (line, net) in enumerate(zip(lines, nets, strict=True), start=1):
        invoice_line = _el(root, _CAC, "InvoiceLine")
        _el(invoice_line, _CBC, "ID", str(index))
        unit_code = _UNIT_CODES.get((line.unit or "").lower(), "C62")
        _el(
            invoice_line, _CBC, "InvoicedQuantity", str(line.quantity), unitCode=unit_code
        )
        _el(invoice_line, _CBC, "LineExtensionAmount", _fmt(net), currencyID=currency)
        item = _el(invoice_line, _CAC, "Item")
        _el(item, _CBC, "Name", line.description[:100] or "—")
        classified = _el(item, _CAC, "ClassifiedTaxCategory")
        _el(classified, _CBC, "ID", _CATEGORY_CODES.get(line.tax_category, "S"))
        _el(classified, _CBC, "Percent", str(line.tax_rate_pct))
        scheme = _el(classified, _CAC, "TaxScheme")
        _el(scheme, _CBC, "ID", "VAT")
        price = _el(invoice_line, _CAC, "Price")
        unit_net = (
            round_cents(net / Decimal(line.quantity)) if line.quantity else Decimal("0.00")
        )
        _el(price, _CBC, "PriceAmount", _fmt(unit_net), currencyID=currency)

    ET.indent(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
