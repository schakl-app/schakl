"""``app.core.billing`` — the calendar rules every recurring charge is built on.

Pure arithmetic, so it is pinned without a database. Two of the rules here were found by an
audit of a demo instance rather than designed, and both are the kind that pass every functional
test while being wrong: a subscription whose ``next_invoice_date`` sat seventeen months in the
past was **absent** from "nog te factureren" (its anchor predated the row, and the floor bounded
the anchor along with the walk) while the cron drafted one historic period for it every night —
the two halves of one question disagreeing, which is the failure the shared seam exists to
prevent.
"""

from __future__ import annotations

from datetime import date

from app.core.billing import add_months, first_boundary_ahead, period_boundaries, period_span


def test_the_floor_bounds_the_walk_but_never_the_anchor() -> None:
    """The anchor is the cycle's own statement of what is billed next, whoever set it and
    whenever the row was made; a floor that hid it would hide exactly what the cron drafts."""
    created = date(2026, 8, 26)
    boundaries, truncated = period_boundaries(
        start_date=date(2024, 6, 1),
        anchor=date(2025, 4, 1),
        months=1,
        floor=created,
    )
    assert boundaries == [date(2025, 4, 1)]
    assert truncated is False


def test_boundaries_forward_of_the_anchor_up_to_today_are_offered() -> None:
    """The calendar has passed the cycle — a worker that did not run, an anchor set into the
    past on purpose, a resumed agreement — and every boundary it passed is outstanding whether
    the cron catches up tonight or nobody ever drafts it."""
    boundaries, truncated = period_boundaries(
        start_date=date(2024, 6, 1),
        anchor=date(2025, 4, 1),
        months=1,
        floor=date(2026, 8, 26),
        until=date(2025, 7, 15),
    )
    assert boundaries == [date(2025, 4, 1), date(2025, 5, 1), date(2025, 6, 1), date(2025, 7, 1)]
    assert truncated is False


def test_forward_walk_respects_the_end_date_and_the_cap() -> None:
    boundaries, truncated = period_boundaries(
        start_date=date(2024, 1, 1),
        anchor=date(2024, 2, 1),
        months=1,
        end_date=date(2024, 4, 1),
        until=date(2026, 1, 1),
    )
    # A cycle past the agreed end has nothing left to invoice.
    assert boundaries == [date(2024, 2, 1), date(2024, 3, 1), date(2024, 4, 1)]
    assert truncated is False

    boundaries, truncated = period_boundaries(
        start_date=date(2020, 1, 1),
        anchor=date(2020, 2, 1),
        months=1,
        until=date(2026, 1, 1),
        limit=6,
    )
    # Over the cap is reported, and the **newest** are kept — the forward walk's tail.
    assert truncated is True
    assert boundaries == [add_months(date(2025, 8, 1), k) for k in range(6)]


def test_without_until_the_walk_is_unchanged() -> None:
    """The picker and the backlog before this change: back from the anchor, floored."""
    boundaries, truncated = period_boundaries(
        start_date=date(2025, 1, 1),
        anchor=date(2025, 6, 1),
        months=1,
        floor=date(2025, 3, 15),
    )
    assert boundaries == [date(2025, 4, 1), date(2025, 5, 1), date(2025, 6, 1)]
    assert truncated is False


def test_a_derived_cycle_date_never_lands_in_the_past() -> None:
    """An agreement entered with a start date years back is an existing arrangement being
    onboarded, not years of arrears: the derived anchor is the first grid boundary still
    ahead, on the start date's own grid (no clamped-February drift)."""
    today = date(2026, 9, 4)
    assert first_boundary_ahead(date(2026, 9, 4), 1, today) == date(2026, 10, 4)
    assert first_boundary_ahead(date(2024, 6, 1), 1, today) == date(2026, 10, 1)
    assert first_boundary_ahead(date(2023, 3, 31), 1, today) == date(2026, 9, 30)
    assert first_boundary_ahead(date(2025, 8, 1), 3, today) == date(2026, 11, 1)
    # A boundary that falls on today has not passed: it is the cron's tonight.
    assert first_boundary_ahead(date(2026, 8, 4), 1, today) == today


def test_a_renewal_billed_in_advance_covers_the_year_ahead() -> None:
    """The charge raised at a boundary covers the year that *starts* there — the register
    renews on that day for the year ahead — so the ``start_date`` bound bites on the boundary
    itself rather than a year behind it, and 01-10-2026 never reads as "2025 – 2026"."""
    assert period_span(date(2026, 10, 1), 12, advance=True) == (
        date(2026, 10, 1),
        date(2027, 10, 1),
    )
    assert period_span(date(2026, 10, 1), 12, advance=False) == (
        date(2025, 10, 1),
        date(2026, 10, 1),
    )
    # A domain registered 2024-10-01 with its renewal at 2026-10-01: in arrears the walk would
    # reach back to a "2024 – 2025" period; in advance the 2024 boundary itself is the first
    # year served, and the 2023 boundary would begin before the registration did.
    boundaries, truncated = period_boundaries(
        start_date=date(2024, 10, 1),
        anchor=date(2026, 10, 1),
        months=12,
        advance=True,
    )
    assert boundaries == [date(2024, 10, 1), date(2025, 10, 1), date(2026, 10, 1)]
    assert truncated is False


def test_billed_until_settles_every_period_it_covers_including_the_anchor() -> None:
    """The operator's own statement that everything up to a date was invoiced already — by
    the previous system, on paper — takes a period out of the backlog whether the floor
    reached it or not, the anchor included; a period ending *after* the date stays owed."""
    boundaries, truncated = period_boundaries(
        start_date=date(2024, 10, 1),
        anchor=date(2026, 10, 1),
        months=12,
        advance=True,
        billed_until=date(2026, 10, 1),
    )
    # 2024 – 2025 and 2025 – 2026 end on or before the date; 2026 – 2027 does not.
    assert boundaries == [date(2026, 10, 1)]
    assert truncated is False

    boundaries, _ = period_boundaries(
        start_date=date(2024, 10, 1),
        anchor=date(2026, 10, 1),
        months=12,
        advance=True,
        billed_until=date(2027, 10, 1),
    )
    # The anchor's own year is settled too — the cron rolls past it without a draft.
    assert boundaries == []

    # In arrears the period *ending* at the boundary is what the date is compared with.
    boundaries, _ = period_boundaries(
        start_date=date(2025, 1, 1),
        anchor=date(2025, 6, 1),
        months=1,
        floor=date(2025, 1, 1),
        billed_until=date(2025, 4, 1),
    )
    assert boundaries == [date(2025, 5, 1), date(2025, 6, 1)]

    # Settled periods do not count against the cap: a year of history invoiced elsewhere
    # never costs an open period its place in the list.
    boundaries, truncated = period_boundaries(
        start_date=date(2020, 1, 1),
        anchor=date(2026, 1, 1),
        months=1,
        floor=date(2020, 1, 1),
        billed_until=date(2025, 10, 1),
        limit=6,
    )
    assert boundaries == [add_months(date(2025, 11, 1), k) for k in range(3)]
    assert truncated is False


def test_the_start_date_bounds_the_walk_but_never_the_anchor() -> None:
    """A portfolio onboarded in one afternoon carries that afternoon as every ``start_date``,
    and the cycle date set from the register sits wherever the registration renews. The
    anchor's period then *begins* before the start date on every yearly agreement, and a
    guard that refused it hid the whole register from the backlog while the cron billed each
    renewal that night — the two halves disagreeing, again. The anchor is the cycle's own
    statement; only the history behind it is bounded."""
    boundaries, truncated = period_boundaries(
        start_date=date(2026, 8, 6),
        anchor=date(2026, 9, 1),
        months=12,
    )
    assert boundaries == [date(2026, 9, 1)]
    assert truncated is False
    # The same in arrears, one month over: the month ending 1 September began before the
    # 6 August start date, and is still what the cron bills next.
    boundaries, _ = period_boundaries(
        start_date=date(2026, 8, 6),
        anchor=date(2026, 9, 1),
        months=1,
        until=date(2026, 10, 15),
    )
    assert boundaries == [date(2026, 9, 1), date(2026, 10, 1)]
