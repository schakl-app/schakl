"""Which TLD price row prices a renewal on a given day — one rule, read by the backlog seam
(`DomainService.open_renewals`) and the renewal cron (`jobs._advance_one`) alike, so a screen
and the cron can never disagree about what a renewal costs.

The rule: the newest row whose ``valid_from`` is on or before the day. A day *before the
list begins* takes the list's first row — an agency that enters ".nl €14,79" today with 21
domains on it means "this is what .nl costs", not "this is what .nl costs from today and
nothing before", and the alternative left every overdue renewal unpriced until somebody
backdated the row (and then deleted the one they had entered first, because the newer row
outranks it). A **scheduled** row never prices anything before its day: it is a change that
has not happened yet, and reading it backwards would price last month's renewal at next
month's increase. History is still never repriced — an invoice snapshots at draft time.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol


class PriceRow(Protocol):
    valid_from: date


def price_row_at[T: PriceRow](rows: list[T], day: date, today: date) -> T | None:
    """``rows`` is one TLD's history sorted by ``valid_from`` ascending; ``today`` is the
    org-local today that decides which rows are scheduled rather than in force."""
    current: T | None = None
    for row in rows:
        if row.valid_from <= day:
            current = row
        else:
            break
    if current is not None:
        return current
    first = rows[0] if rows else None
    if first is not None and first.valid_from <= today:
        return first
    return None
