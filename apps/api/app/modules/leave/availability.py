"""Resolving a freelancer's actual availability: the base week, bent by dated exceptions.

Two rules live here and nowhere else, so the day view, the roster and any later capacity read
can never hold different opinions about the same Thursday.

**When a row applies.** A one-off applies to its own date. A repeat applies to its own date and
every ``repeat_weeks``-th week after it on the same weekday, up to ``repeat_until`` (or forever).
There is no generated occurrence anywhere — availability is not a balance being spent, so
there is nothing to place and nothing to keep in step with the rule that produced it (contrast
#107, whose free days *are* leave leaving a pot and therefore have to exist as rows).

**How the day resolves.** Minute intervals, like every other hour in this module (#46): the base
week's worked stretches, unioned with what ``extra`` rows add, then minus what ``unavailable``
rows take away. The order is deliberate — **a "no" outranks a "yes"** — because booking someone
who said they were away is a worse failure than missing a day they could have worked.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, time
from decimal import Decimal

from app.modules.leave import schedule as sched
from app.modules.leave.models import AvailabilityKind, EmploymentAvailability

#: A half-open ``[start, end)`` in minutes since midnight.
Interval = tuple[int, int]


def occurs_on(row: EmploymentAvailability, day: date) -> bool:
    """Does this row apply to ``day``?"""
    if day < row.date:
        return False
    if row.repeat_weeks is None:
        return day == row.date
    if row.repeat_until is not None and day > row.repeat_until:
        return False
    delta = (day - row.date).days
    return delta % (7 * row.repeat_weeks) == 0


def overlaps_window(row: EmploymentAvailability, date_from: date, date_to: date) -> bool:
    """Does any occurrence of this row land inside ``[date_from, date_to]``?

    A cheap span test first (a non-repeating row is just its own date), then — only for a
    repeat — the weekday walk, which is bounded by the cadence and never by the window's length.
    """
    if row.repeat_weeks is None:
        return date_from <= row.date <= date_to
    if row.date > date_to:
        return False
    if row.repeat_until is not None and row.repeat_until < date_from:
        return False
    # The first occurrence on or after ``date_from``, found by arithmetic rather than by
    # iterating the window: a year's roster must not walk 365 days per row.
    step = 7 * row.repeat_weeks
    behind = max(0, (date_from - row.date).days)
    ahead = row.date.toordinal() + ((behind + step - 1) // step) * step
    first = date.fromordinal(ahead)
    return first <= date_to and (row.repeat_until is None or first <= row.repeat_until)


def _day_intervals(day: sched.WorkDay | None) -> list[Interval]:
    """The stretches actually worked on a scheduled day — the block minus its breaks."""
    if day is None:
        return []
    start, end = sched.to_minutes(day.start), sched.to_minutes(day.end)
    intervals: list[Interval] = []
    cursor = start
    for window in day.breaks:  # sorted and validated inside the block by WorkDay
        break_start, break_end = sched.to_minutes(window.start), sched.to_minutes(window.end)
        if break_start > cursor:
            intervals.append((cursor, break_start))
        cursor = max(cursor, break_end)
    if cursor < end:
        intervals.append((cursor, end))
    return intervals


def _normalise(intervals: Iterable[Interval]) -> list[Interval]:
    """Sorted, merged, non-touching."""
    merged: list[Interval] = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _subtract(intervals: Sequence[Interval], cut: Interval) -> list[Interval]:
    out: list[Interval] = []
    for start, end in intervals:
        if cut[1] <= start or cut[0] >= end:
            out.append((start, end))
            continue
        if start < cut[0]:
            out.append((start, cut[0]))
        if cut[1] < end:
            out.append((cut[1], end))
    return out


def _row_window(
    row: EmploymentAvailability, fallback: Sequence[Interval]
) -> Interval | None:
    """The row's own window in minutes, or ``None`` when it covers the whole day.

    A one-sided window is closed against the day it lands on — "from 13:00" means "until the day
    ends" (#48) — and against midnight when there is no day to borrow a bound from, which is the
    honest reading of "available from 13:00" on a day the base week does not work.
    """
    if row.start_time is None and row.end_time is None:
        return None
    low = min((start for start, _ in fallback), default=0)
    high = max((end for _, end in fallback), default=sched.MINUTES_PER_DAY)
    start = sched.to_minutes(row.start_time) if row.start_time is not None else low
    end = sched.to_minutes(row.end_time) if row.end_time is not None else high
    return (start, end) if end > start else None


def typical_day(*weeks: sched.WorkSchedule | None) -> sched.WorkDay | None:
    """The first working day of the first week that has one — "a day like the ones you work".

    What a whole-day ``extra`` means on a day the base week does not contain. It cannot be *that
    weekday* of the org default, because the answer wanted is almost always Saturday and no
    default week works Saturdays: the row would add a day of zero length and read on screen as
    "yes, for no hours". The person's own week is preferred over the org's, so a part-timer's
    extra day is one of *their* days.
    """
    for week in weeks:
        if week is None:
            continue
        for index in range(7):
            day = week.day(index)
            if day is not None:
                return day
    return None


def resolve_day(
    base: sched.WorkDay | None,
    typical: sched.WorkDay | None,
    rows: Sequence[EmploymentAvailability],
) -> list[Interval]:
    """The stretches available on one day: ``base``, plus every ``extra``, minus every
    ``unavailable``.

    ``typical`` is what a whole-day ``extra`` means on a day the base week does not work — the
    common case, since an extra day is usually a day off. See :func:`typical_day`.
    """
    intervals = _day_intervals(base)
    extras = [r for r in rows if r.kind == AvailabilityKind.EXTRA]
    blocks = [r for r in rows if r.kind == AvailabilityKind.UNAVAILABLE]

    for row in extras:
        window = _row_window(row, intervals)
        if window is None:
            intervals = _normalise([*intervals, *_day_intervals(base or typical)])
        else:
            intervals = _normalise([*intervals, window])
    # An extra window that stretches a day the base week already works must not swallow its
    # lunch: a break is a window, not a duration (#46), and "available 08:00–18:00" does not
    # mean nine and a half hours became ten.
    if base is not None:
        for window in base.breaks:
            intervals = _subtract(
                intervals, (sched.to_minutes(window.start), sched.to_minutes(window.end))
            )

    for row in blocks:
        window = _row_window(row, intervals)
        if window is None:
            return []
        intervals = _subtract(intervals, window)
    return _normalise(intervals)


def hours(intervals: Sequence[Interval]) -> Decimal:
    return sched.to_hours(sum(end - start for start, end in intervals))


def as_clock(minutes: int) -> time:
    """Minutes since midnight → a wall clock. 24:00 has no ``time``, so it closes at 23:59."""
    minutes = min(minutes, sched.MINUTES_PER_DAY - 1)
    return time(hour=minutes // 60, minute=minutes % 60)
