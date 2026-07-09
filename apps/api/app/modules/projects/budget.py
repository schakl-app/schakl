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

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

# The same timezone the task recurrence engine already runs on (tasks/recurrence.py).
_TZ = ZoneInfo("Europe/Amsterdam")

# Stands in for "no lower bound" where a query needs a concrete timestamp. No time entry can
# predate it, so a `total` budget counts everything.
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

BUDGET_PERIODS: tuple[str, ...] = ("total", "monthly", "weekly", "daily")


def period_start(budget_period: str, *, now: datetime | None = None) -> datetime | None:
    """The UTC instant the current budget period began. ``None`` for ``total`` — it never resets.

    ``now`` is injectable so tests can pin a date instead of racing the clock.
    """
    local = (now or datetime.now(UTC)).astimezone(_TZ)
    today = local.date()
    if budget_period == "monthly":
        day = today.replace(day=1)
    elif budget_period == "weekly":
        day = today - timedelta(days=today.weekday())  # Monday
    elif budget_period == "daily":
        day = today
    else:
        return None
    return datetime.combine(day, time.min, tzinfo=_TZ).astimezone(UTC)


def period_bound(budget_period: str, *, now: datetime | None = None) -> datetime:
    """``period_start`` with ``total`` collapsed to ``EPOCH``, for queries that need a timestamp."""
    return period_start(budget_period, now=now) or EPOCH
