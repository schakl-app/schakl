"""Money math for invoicing (issue #207): the rules of ``calc.py``, pinned.

VAT rounding is legally specified and the #31 issue demands it be tested against known
cases — these are those cases. Also pins the numbering format contract.
"""

from decimal import Decimal

from app.modules.invoicing.calc import LineInput, compute_totals, line_amount
from app.modules.invoicing.numbering import format_number, format_valid


def line(qty: str, price: str, pct: str = "21", category: str = "standard") -> LineInput:
    return LineInput(
        quantity=Decimal(qty),
        unit_price=Decimal(price),
        tax_rate_pct=Decimal(pct),
        tax_category=category,
        tax_name=f"{pct}%",
    )


def test_exclusive_mixed_rates() -> None:
    totals = compute_totals(
        [line("10", "85"), line("1", "50"), line("2", "5", pct="9")],
        prices_include_tax=False,
    )
    assert totals.subtotal == Decimal("910.00")
    # 21% over 900 = 189.00; 9% over 10 = 0.90 — per rate group, never per line.
    assert totals.tax_total == Decimal("189.90")
    assert totals.total == Decimal("1099.90")
    assert [(g.rate_pct, g.base, g.tax) for g in totals.groups] == [
        (Decimal("21"), Decimal("900.00"), Decimal("189.00")),
        (Decimal("9"), Decimal("10.00"), Decimal("0.90")),
    ]


def test_tax_rounds_once_per_group_not_per_line() -> None:
    # Three 0.35 lines at 21%: per-line rounding would give 3 × 0.07 = 0.21;
    # the correct group computation is 1.05 × 21% = 0.2205 → 0.22.
    totals = compute_totals([line("1", "0.35")] * 3, prices_include_tax=False)
    assert totals.tax_total == Decimal("0.22")


def test_rounding_is_half_up() -> None:
    # 0.50 × 21% = 0.105 — banker's rounding would say 0.10; invoices say 0.11.
    totals = compute_totals([line("1", "0.50")], prices_include_tax=False)
    assert totals.tax_total == Decimal("0.11")


def test_line_amount_rounds_the_product_once() -> None:
    assert line_amount(Decimal("3"), Decimal("0.333")) == Decimal("1.00")


def test_inclusive_prices_extract_tax_exactly() -> None:
    totals = compute_totals([line("1", "121.00")], prices_include_tax=True)
    assert totals.subtotal == Decimal("100.00")
    assert totals.tax_total == Decimal("21.00")
    assert totals.total == Decimal("121.00")
    # An awkward gross still reconciles: base + tax == what the customer saw.
    awkward = compute_totals([line("1", "0.99")], prices_include_tax=True)
    assert awkward.subtotal + awkward.tax_total == Decimal("0.99")
    assert awkward.total == Decimal("0.99")


def test_reverse_charge_and_exempt_charge_nothing() -> None:
    totals = compute_totals(
        [
            line("1", "1000", pct="21", category="reverse_charge"),
            line("1", "200", pct="0", category="exempt"),
        ],
        prices_include_tax=False,
    )
    assert totals.subtotal == Decimal("1200.00")
    assert totals.tax_total == Decimal("0.00")
    assert totals.total == Decimal("1200.00")
    # The reverse-charge group keeps its nominal pct for display/UBL coding.
    assert totals.groups[0].rate_pct == Decimal("21")
    assert totals.groups[0].tax == Decimal("0.00")


def test_discount_line_is_a_negative_line() -> None:
    totals = compute_totals([line("1", "100"), line("1", "-10")], prices_include_tax=False)
    assert totals.subtotal == Decimal("90.00")
    assert totals.tax_total == Decimal("18.90")


def test_empty_document_is_all_zero() -> None:
    totals = compute_totals([], prices_include_tax=False)
    assert (totals.subtotal, totals.tax_total, totals.total) == (
        Decimal("0.00"), Decimal("0.00"), Decimal("0.00"),
    )


def test_number_formats() -> None:
    assert format_number("{year}-{seq:4}", year=2026, seq=7) == "2026-0007"
    assert format_number("F{yy}{seq:5}", year=2026, seq=123) == "F2600123"
    assert format_number("INV-{seq}", year=2026, seq=42) == "INV-42"


def test_format_validation() -> None:
    assert format_valid("{year}-{seq:4}")
    assert format_valid("INV-{seq}")
    assert not format_valid("")            # empty
    assert not format_valid("{year}")      # no seq
    assert not format_valid("{seq}{seq}")  # two seqs → ambiguous sequence
    assert not format_valid("{seq}-{maand}")  # unknown token
