"""When an automatic Timeon sync is due. Business-licensed — see LICENSE.

Pure arithmetic over four stored fields and one zone, deliberately separated from
:mod:`app.integrations.timeon.jobs` so the interesting half can be asserted without a worker, a
database or a fake Timeon — and so the **screen** can answer "when does it run next?" with the
same function the worker decides with. Two copies of a schedule rule is how a page comes to
promise a run the worker then does not make (#373's `effective_source` lesson, one integration
over).

Three properties are worth stating.

**The ARQ cron is the tick, not the schedule.** It fires every quarter of an hour and each
account decides whether its own moment has come — the only shape that can express "hourly for
this connection, nightly for that one" without a cron per account, and the same shape
``reporting_tick`` already uses for a per-org hour. The consequence is honest and worth saying
out loud: a time of day is honoured to within one tick, never to the minute.

**The clock is the org's** (§8). ``auto_time`` is a local wall clock, so 04:20 is 04:20 on both
sides of a DST change — where the hardcoded ``cron(hour=4, minute=20)`` this replaces was 04:20
*UTC*, and therefore moved by an hour twice a year on the only clock the tenant has.

**Never having run is due now.** A schedule whose first run is up to 24 hours away is a control
whose effect nobody can see, which is the half of #387 that hid the other half: five nights of a
nightly not running looked exactly like five nights of nothing having changed in Timeon. So
switching auto-sync on produces a run within the quarter-hour, and the account settles onto its
stated cadence from there.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.integrations.timeon.models import SyncFrequency

#: How far back an automatic run on a *sub-daily* cadence reads, in days.
#:
#: The account's ``window_days`` (45 by default) is the right horizon for a run that happens once
#: a day and the wrong one for a run that happens twenty-four times: Timeon's hour rows carry no
#: modified timestamp, so the window *is* the sync, and re-reading the same six weeks every hour
#: spends somebody's rate limit on an answer that has not changed. So an hourly connection reads
#: a short window on most ticks and its **full** window on the first run of the org's local day —
#: a deep reconcile daily, cheap catch-up in between.
CATCH_UP_WINDOW_DAYS = 3

#: The interval kinds, where "next" is measured from the last run rather than from a wall clock.
_INTERVAL = frozenset({SyncFrequency.HOURLY.value, SyncFrequency.EVERY_N_HOURS.value})


def step_hours(frequency: str, interval_hours: int) -> int:
    """The gap between two runs for an interval cadence, in hours."""
    if frequency == SyncFrequency.HOURLY.value:
        return 1
    return max(1, min(24, int(interval_hours or 1)))


def next_auto_run(
    *,
    frequency: str,
    interval_hours: int,
    at: time,
    last_run: datetime | None,
    zone: ZoneInfo,
    now: datetime,
) -> datetime:
    """When this connection's next automatic run falls, as a UTC instant.

    ``now`` is returned when a run is due (including the never-run case), which is what
    :func:`is_due` compares and what lets a screen say *"bij de eerstvolgende ronde"* rather than
    printing a moment in the past.
    """
    if last_run is None:
        return now
    if last_run.tzinfo is None:  # a naive column read is UTC here, never local
        last_run = last_run.replace(tzinfo=UTC)

    if frequency in _INTERVAL:
        return last_run + timedelta(hours=step_hours(frequency, interval_hours))

    local = last_run.astimezone(zone)
    candidate = datetime.combine(local.date(), at, tzinfo=zone)
    if candidate <= local:
        candidate += timedelta(days=1)
    if frequency == SyncFrequency.WEEKDAYS.value:
        # Saturday is 5. An agency that does not work weekends does not need Saturday's read of
        # somebody else's rate limit — and Monday's run covers the window either way.
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
    return candidate.astimezone(UTC)


def is_due(
    *,
    frequency: str,
    interval_hours: int,
    at: time,
    last_run: datetime | None,
    zone: ZoneInfo,
    now: datetime,
) -> bool:
    """Whether this connection's moment has come at ``now``."""
    return (
        next_auto_run(
            frequency=frequency,
            interval_hours=interval_hours,
            at=at,
            last_run=last_run,
            zone=zone,
            now=now,
        )
        <= now
    )


def catch_up_days(
    *, frequency: str, last_run: datetime | None, zone: ZoneInfo, now: datetime
) -> int | None:
    """How many days back this particular automatic run should read, or ``None`` for the account's
    own ``window_days``.

    ``None`` for every daily cadence and for the first run of the org's local day; the short
    window otherwise. See :data:`CATCH_UP_WINDOW_DAYS` for why a cadence changes a horizon at all.
    """
    if frequency not in _INTERVAL or last_run is None:
        return None
    if last_run.tzinfo is None:
        last_run = last_run.replace(tzinfo=UTC)
    if last_run.astimezone(zone).date() < now.astimezone(zone).date():
        return None
    return CATCH_UP_WINDOW_DAYS
