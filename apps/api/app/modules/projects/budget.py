"""When a project's hour budget last reset (#25).

``budget_period`` says whether ``budget_hours`` covers the whole project (``total``) or refills
every month/week/day. "Available hours" therefore means *this period's* remaining, and every
period boundary needs a concrete instant to count from.

Boundaries are **Europe/Amsterdam**, the platform's default timezone (CLAUDE.md §8), not UTC.
The rest of the time module works in UTC, so a monthly budget used to roll over at 01:00 or 02:00
local — an hour of work landing in the wrong month twice a year. The instant returned here is
still UTC; only the *day* it names is local.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

# The same timezone the task recurrence engine already runs on (tasks/recurrence.py).
_TZ = ZoneInfo("Europe/Amsterdam")

# Stands in for "no lower bound" where a query needs a concrete timestamp. No time entry can
# predate it, so a `total` budget counts everything.
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

BUDGET_PERIODS: tuple[str, ...] = ("total", "monthly", "weekly", "daily")


def period_start_date(budget_period: str, *, now: datetime | None = None) -> date | None:
    """The **local calendar day** the current budget period began. ``None`` for ``total``.

    This is the day a human names ("since 1 July"), and the one a client sends back as a
    ``date_from`` filter. It is emphatically *not* ``period_start(...).date()``: in summer the
    UTC instant for Amsterdam-local midnight is 22:00 the day **before**, so that expression
    reports 30 June for a July budget and drags the previous month's last evening into the
    period it is supposed to exclude.

    ``now`` is injectable so tests can pin a date instead of racing the clock.
    """
    today = (now or datetime.now(UTC)).astimezone(_TZ).date()
    if budget_period == "monthly":
        return today.replace(day=1)
    if budget_period == "weekly":
        return today - timedelta(days=today.weekday())  # Monday
    if budget_period == "daily":
        return today
    return None


def period_start(budget_period: str, *, now: datetime | None = None) -> datetime | None:
    """The UTC instant the current budget period began. ``None`` for ``total`` — it never resets.

    ``now`` is injectable so tests can pin a date instead of racing the clock.
    """
    day = period_start_date(budget_period, now=now)
    if day is None:
        return None
    return datetime.combine(day, time.min, tzinfo=_TZ).astimezone(UTC)


def period_bound(budget_period: str, *, now: datetime | None = None) -> datetime:
    """``period_start`` with ``total`` collapsed to ``EPOCH``, for queries that need a timestamp."""
    return period_start(budget_period, now=now) or EPOCH
