"""When a project's hour budget last reset (#25).

``budget_period`` says whether ``budget_hours`` covers the whole project (``total``) or refills
every month/week/day. "Available hours" therefore means *this period's* remaining, and every
period boundary needs a concrete instant to count from.

Boundaries are **local calendar days in the tenant's own zone** (CLAUDE.md §8), not UTC. The rest
of the time module works in UTC, so a monthly budget used to roll over at 01:00 or 02:00 local —
an hour of work landing in the wrong month twice a year. The instant returned here is still UTC;
only the *day* it names is local.

*Which* zone is the caller's to supply, and every caller has one: `org_zoneinfo(session, org_id)`
resolves the org's `org_settings.timezone` and falls back to the instance default. These functions
stay pure so a test can pin both the clock and the zone, but the default is the configured
instance zone — never a hardcoded city. An agency in Lisbon closes its month at Lisbon midnight.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.core.timezone import resolve_zoneinfo

# Stands in for "no lower bound" where a query needs a concrete timestamp. No time entry can
# predate it, so a `total` budget counts everything.
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

BUDGET_PERIODS: tuple[str, ...] = ("total", "monthly", "weekly", "daily")


def _zone(tz: ZoneInfo | None) -> ZoneInfo:
    """The caller's zone, or the **configured** instance default — resolved per call, not at
    import, so a test (or a differently configured instance) is never stuck with a stale one."""
    return tz if tz is not None else resolve_zoneinfo(None)


def effective_budget(
    budget_hours: float | None, budget_period: str, covering: list
) -> tuple[float | None, str]:
    """The budget a project actually burns against, and the period it resets on.

    A project covered by an active subscription with included hours (#225) burns against the
    sum of those subscriptions' monthly-equivalent hours and its period is forced to monthly —
    the stored ``budget_hours`` is derived and read-only then. One copy of that rule, taken by
    the screen's burn bar (``ProjectService._attach_hours``) and the nightly budget watch
    alike, so the alert and the bar can never disagree about how many hours a project has.
    ``covering`` is duck-typed (objects carrying ``monthly_hours``) so this file never imports
    the subscriptions module (CLAUDE.md §6).
    """
    if covering:
        return round(sum(s.monthly_hours for s in covering), 2), "monthly"
    return (float(budget_hours) if budget_hours is not None else None), budget_period


def period_start_date(
    budget_period: str, *, now: datetime | None = None, tz: ZoneInfo | None = None
) -> date | None:
    """The **local calendar day** the current budget period began. ``None`` for ``total``.

    This is the day a human names ("since 1 July"), and the one a client sends back as a
    ``date_from`` filter. It is emphatically *not* ``period_start(...).date()``: in summer the
    UTC instant for Amsterdam-local midnight is 22:00 the day **before**, so that expression
    reports 30 June for a July budget and drags the previous month's last evening into the
    period it is supposed to exclude.

    ``now`` and ``tz`` are injectable so tests can pin a date and a zone instead of racing the
    clock or inheriting the box's configuration.
    """
    today = (now or datetime.now(UTC)).astimezone(_zone(tz)).date()
    if budget_period == "monthly":
        return today.replace(day=1)
    if budget_period == "weekly":
        return today - timedelta(days=today.weekday())  # Monday
    if budget_period == "daily":
        return today
    return None


def period_start(
    budget_period: str, *, now: datetime | None = None, tz: ZoneInfo | None = None
) -> datetime | None:
    """The UTC instant the current budget period began. ``None`` for ``total`` — it never resets.

    ``now`` and ``tz`` are injectable so tests can pin a date and a zone instead of racing the
    clock or inheriting the box's configuration.
    """
    day = period_start_date(budget_period, now=now, tz=tz)
    if day is None:
        return None
    return datetime.combine(day, time.min, tzinfo=_zone(tz)).astimezone(UTC)


def period_bound(
    budget_period: str, *, now: datetime | None = None, tz: ZoneInfo | None = None
) -> datetime:
    """``period_start`` with ``total`` collapsed to ``EPOCH``, for queries that need a timestamp."""
    return period_start(budget_period, now=now, tz=tz) or EPOCH
