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
from typing import Any

from app.modules.invoicing.models import TaxCategory

CENTS = Decimal("0.01")

#: Categories whose groups never charge tax, whatever their nominal pct.
UNTAXED_CATEGORIES = frozenset({TaxCategory.EXEMPT.value, TaxCategory.REVERSE_CHARGE.value})


#: What a document still owes, in SQL, for the places that aggregate over the table rather
#: than over hydrated rows. Kept beside the Python rule below so the two cannot drift: a
#: dashboard that nets credit notes and a list that does not is worse than neither.
OUTSTANDING_SQL = "(total - paid_total - credited_total + applied_total)"


def round_cents(value: Decimal) -> Decimal:
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


def outstanding_of(doc: object) -> Decimal:
    """What the document still owes — positive when the client owes us, negative when we
    owe them, zero when it is settled whichever way it got there.

    Money leaves a balance two ways, and both count: a **payment**, and a **credit note**
    that wrote part of it off (``credited_total``). The mirror term ``applied_total`` is what
    a credit note's own source already absorbed, so the credit note is left owing only the
    part nobody has settled — which is exactly the refund. One expression covers both kinds
    because a plain invoice never applies and (guarded in the service) is never credited into.
    """
    return (
        Decimal(doc.total)  # type: ignore[attr-defined]
        - Decimal(doc.paid_total)  # type: ignore[attr-defined]
        - Decimal(doc.credited_total or 0)  # type: ignore[attr-defined]
        + Decimal(doc.applied_total or 0)  # type: ignore[attr-defined]
    )


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


def effective_rate_pct(subtotal: Decimal, tax_total: Decimal) -> Decimal:
    """The one rate that turns ``subtotal`` into ``tax_total``, to the cent of a percent.

    An imported document states its totals and not its rate mix, so the only rate it can be
    described by is the effective one — ``21.00`` for an ordinary Dutch invoice, ``15.00`` for
    one that was half high and half low. Two decimals because that is what ``tax_rate_pct``
    holds; zero for a zero (or negative-base) document rather than a division error.
    """
    subtotal = Decimal(subtotal)
    if subtotal == 0:
        return Decimal("0.00")
    return (Decimal(tax_total) / subtotal * Decimal(100)).quantize(CENTS, rounding=ROUND_HALF_UP)


def stated_totals(doc: Any, *, tax_name: str) -> Totals:
    """The totals of a document **as stored**, as one tax group — for an imported invoice.

    ``compute_totals`` is the authority for a document this platform priced: lines in, money
    out. An imported document is the other way round — the money is the fact (it is what the
    paper the client holds says) and its single summary line exists only so the document has a
    row to print. Recomputing its tax from that line at a two-decimal effective rate can be a
    cent off on a large mixed-rate invoice, and a preview that disagrees with the stored total
    by a cent is a bug somebody will report. So every reader that prints a breakdown — the
    renderer, UBL, ``InvoiceRead.tax_groups`` — takes this instead, and the numbers are the
    stored ones by construction.
    """
    subtotal = Decimal(doc.subtotal)
    tax_total = Decimal(doc.tax_total)
    return Totals(
        subtotal=subtotal,
        tax_total=tax_total,
        total=Decimal(doc.total),
        groups=(
            TaxGroup(
                rate_pct=effective_rate_pct(subtotal, tax_total),
                category=TaxCategory.STANDARD.value if tax_total else TaxCategory.ZERO.value,
                name=tax_name,
                base=subtotal,
                tax=tax_total,
            ),
        ),
    )


def line_nets(
    lines: list[Any], groups: tuple[TaxGroup, ...], include_tax: bool
) -> list[Decimal]:
    """Net (tax-exclusive) amount per line, reconciling exactly with the group bases.

    On a tax-inclusive document each line's net is its gross divided by its own rate, and a
    sum of independently rounded quotients does **not** equal the group base rounded once. So
    the per-group drift is folded into that group's largest line, which keeps the cent where it
    is least visible and makes ``sum(nets) == subtotal`` a guarantee rather than a hope.

    Shared rather than copied: every export that has to state a per-line net — UBL today
    (``ubl.py``), a SnelStart ``verkoopboeking``'s ``boekingsregels`` next — needs the same
    reconciliation, and a second implementation is how two exports of one invoice start
    disagreeing by a cent with an accountant looking at both.
    """
    nets: list[Decimal] = []
    for line in lines:
        untaxed = line.tax_category in (
            TaxCategory.EXEMPT.value,
            TaxCategory.REVERSE_CHARGE.value,
        )
        if include_tax and not untaxed and line.tax_rate_pct != 0:
            factor = Decimal(1) + Decimal(line.tax_rate_pct) / Decimal(100)
            nets.append(round_cents(Decimal(line.amount) / factor))
        else:
            nets.append(Decimal(line.amount))
    for group in groups:
        indexes = [
            i
            for i, line in enumerate(lines)
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
