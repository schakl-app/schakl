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

from app.core.billing import add_months, first_boundary_ahead, period_boundaries


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
