"""A fabricated document, for judging a design against.

The template editor lives in Settings, where no invoice is in hand, so its preview needs
something to draw. Picking "the tenant's most recent invoice" would be worse than this: an
agency whose latest invoice happens to be one line of one kind would be choosing a layout
without ever seeing what a section header, a VAT split or a partial payment looks like in it.

So the sample exercises **every block the catalog offers**: two line kinds (so grouping and
section subtotals appear), two VAT rates (so the breakdown has more than one row), a
registered payment (so *Betaald* / *Te betalen* appear), a reference, a delivery date and a
customer number. It is plain data — no ORM, no session — which is also what lets the preview
route answer without touching the invoices table.

The *seller* half is not fabricated: the tenant's real identity and branding are what the
design has to sit around, so the caller passes those in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from app.i18n import translate
from app.modules.invoicing.calc import LineInput, compute_totals


@dataclass
class _SampleLine:
    description: str
    quantity: Decimal
    unit: str | None
    unit_price: Decimal
    tax_name: str
    tax_rate_pct: Decimal
    amount: Decimal
    line_kind: str
    tax_category: str = "standard"


@dataclass
class _SampleDoc:
    number: str
    currency: str
    locale: str
    status: str
    kind: str
    issue_date: date
    due_date: date
    delivery_date: date
    reference: str
    intro: str | None
    notes: str | None
    customer: dict[str, Any]
    subtotal: Decimal
    tax_total: Decimal
    total: Decimal
    paid_total: Decimal
    prices_include_tax: bool = False
    period_start: date | None = None
    period_end: date | None = None
    valid_until: date | None = None
    lines: list[_SampleLine] = field(default_factory=list)


def sample_document(locale: str, currency: str, today: date) -> tuple[Any, list[Any], list[Any]]:
    """``(doc, lines, tax_groups)`` for the preview. Deterministic given its arguments."""

    def t(key: str) -> str:
        return translate(key, locale)

    lines = [
        _SampleLine(
            description=t("invoicing.sample.line_hours"),
            quantity=Decimal("8"),
            unit=t("invoicing.sample.unit_hour"),
            unit_price=Decimal("95.00"),
            tax_name="21%",
            tax_rate_pct=Decimal("21"),
            amount=Decimal("760.00"),
            line_kind="hours",
        ),
        _SampleLine(
            description=t("invoicing.sample.line_subscription"),
            quantity=Decimal("1"),
            unit=None,
            unit_price=Decimal("365.00"),
            tax_name="21%",
            tax_rate_pct=Decimal("21"),
            amount=Decimal("365.00"),
            line_kind="subscription",
        ),
        _SampleLine(
            description=t("invoicing.sample.line_product"),
            quantity=Decimal("2"),
            unit=None,
            unit_price=Decimal("45.00"),
            tax_name="9%",
            tax_rate_pct=Decimal("9"),
            amount=Decimal("90.00"),
            line_kind="product",
        ),
    ]
    # The same calculator the real documents use, so the sample's numbers actually add up —
    # a preview whose VAT does not reconcile teaches the reader to distrust the design.
    totals = compute_totals(
        [
            LineInput(
                quantity=line.quantity,
                unit_price=line.unit_price,
                tax_rate_pct=line.tax_rate_pct,
                tax_category=line.tax_category,
                tax_name=line.tax_name,
            )
            for line in lines
        ],
        prices_include_tax=False,
    )
    doc = _SampleDoc(
        number=t("invoicing.sample.number"),
        currency=currency or "EUR",
        locale=locale,
        status="open",
        kind="invoice",
        issue_date=today,
        due_date=today + timedelta(days=14),
        delivery_date=today,
        reference=t("invoicing.sample.reference"),
        intro=None,
        notes=None,
        customer={
            "name": t("invoicing.sample.customer_name"),
            "attn": t("invoicing.sample.customer_attn"),
            "address_line1": t("invoicing.sample.customer_street"),
            "postal_code": "1017 CD",
            "city": t("invoicing.sample.customer_city"),
            "country": "NL",
            "vat_number": "NL001234567B01",
            "coc_number": "12345678",
            "email": "info@example.com",
            "client_number": "1042",
        },
        subtotal=totals.subtotal,
        tax_total=totals.tax_total,
        total=totals.total,
        # A partial payment, so *Betaald* and *Te betalen* are both on the sample.
        paid_total=Decimal("500.00"),
        lines=lines,
    )
    return doc, lines, list(totals.groups)
