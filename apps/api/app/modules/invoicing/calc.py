"""Document money math (issue #207) — pure functions, ``Decimal`` end-to-end.

The rules, chosen once and tested in ``tests/test_invoicing_calc.py``:

- **A line's amount** is ``quantity × unit_price`` rounded half-up to cents, in *entered*
  terms — tax-exclusive on a B2B document, tax-inclusive when the document says prices
  include tax.
- **Tax is computed per rate group over the document**, not per line: all lines sharing a
  ``(pct, category)`` are summed first, then taxed, then rounded **once**. That is the shape
  UBL's ``TaxSubtotal`` models and what Dutch/EU bookkeeping expects; per-line rounding
  drifts cents on long invoices (the #48 lesson: round once, on the sum).
- **Inclusive prices peel the tax out of the group gross** (``gross − gross/(1+r)``), so
  net + tax always reconciles exactly to what the customer was shown.
- **Exempt and reverse-charge groups charge zero** whatever pct the picker row carried —
  the pct is retained for display ("btw verlegd, 21%") and for UBL's category coding.

Floats never enter: every quantize is ``ROUND_HALF_UP`` on ``Decimal``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.modules.invoicing.models import TaxCategory

CENTS = Decimal("0.01")

#: Categories whose groups never charge tax, whatever their nominal pct.
UNTAXED_CATEGORIES = frozenset({TaxCategory.EXEMPT.value, TaxCategory.REVERSE_CHARGE.value})


def round_cents(value: Decimal) -> Decimal:
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


def line_amount(quantity: Decimal, unit_price: Decimal) -> Decimal:
    return round_cents(Decimal(quantity) * Decimal(unit_price))


@dataclass(frozen=True)
class LineInput:
    """What the calculator needs from a line — models and schemas both coerce into this."""

    quantity: Decimal
    unit_price: Decimal
    tax_rate_pct: Decimal
    tax_category: str = TaxCategory.STANDARD.value
    tax_name: str = ""


@dataclass(frozen=True)
class TaxGroup:
    """One rate bucket of the document — what the totals footer and UBL both print."""

    rate_pct: Decimal
    category: str
    name: str
    #: Net (tax-exclusive) base of the group, after inclusive-price extraction if any.
    base: Decimal
    tax: Decimal


@dataclass(frozen=True)
class Totals:
    subtotal: Decimal   # net, tax-exclusive
    tax_total: Decimal
    total: Decimal      # gross, what the customer pays
    groups: tuple[TaxGroup, ...]


def compute_totals(lines: list[LineInput], *, prices_include_tax: bool = False) -> Totals:
    """Totals + per-rate tax groups for a document. Deterministic: groups are ordered by
    descending pct then category, so the footer never reshuffles between saves."""
    buckets: dict[tuple[Decimal, str], dict] = {}
    for line in lines:
        pct = Decimal(line.tax_rate_pct)
        key = (pct, line.tax_category)
        bucket = buckets.setdefault(key, {"amount": Decimal(0), "name": line.tax_name})
        bucket["amount"] += line_amount(line.quantity, line.unit_price)
        # First non-empty name wins; lines of one group share a picker row in practice.
        if not bucket["name"] and line.tax_name:
            bucket["name"] = line.tax_name

    groups: list[TaxGroup] = []
    for (pct, category), bucket in sorted(
        buckets.items(), key=lambda item: (-item[0][0], item[0][1])
    ):
        amount: Decimal = bucket["amount"]
        taxable = category not in UNTAXED_CATEGORIES and pct != 0
        if not taxable:
            groups.append(
                TaxGroup(rate_pct=pct, category=category, name=bucket["name"],
                         base=amount, tax=Decimal("0.00"))
            )
            continue
        factor = Decimal(1) + pct / Decimal(100)
        if prices_include_tax:
            base = round_cents(amount / factor)
            tax = amount - base  # exact: net + tax reconciles to the shown gross
        else:
            base = amount
            tax = round_cents(amount * pct / Decimal(100))
        groups.append(
            TaxGroup(rate_pct=pct, category=category, name=bucket["name"], base=base, tax=tax)
        )

    subtotal = round_cents(sum((g.base for g in groups), Decimal(0)))
    tax_total = round_cents(sum((g.tax for g in groups), Decimal(0)))
    return Totals(
        subtotal=subtotal,
        tax_total=tax_total,
        total=subtotal + tax_total,
        groups=tuple(groups),
    )
