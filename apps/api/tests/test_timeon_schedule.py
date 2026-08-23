"""When an automatic Timeon sync is due — the arithmetic, with no worker and no database.

Split out of ``test_timeon_sync.py`` because the interesting half of #388 is a pure function over
four stored fields and one zone, and a test that has to stand up a fake organisation to assert
that Friday is followed by Monday is a test nobody will extend. The end-to-end half (that the
tick honours what this computes) stays in the sync file, where the fake lives.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.integrations.timeon.schedule import (
    CATCH_UP_WINDOW_DAYS,
    catch_up_days,
    is_due,
    next_auto_run,
)

AMS = ZoneInfo("Europe/Amsterdam")
AT = time(4, 20)


def _next(frequency: str, last: datetime | None, *, now: datetime, interval: int = 4) -> datetime:
    return next_auto_run(
        frequency=frequency,
        interval_hours=interval,
        at=AT,
        last_run=last,
        zone=AMS,
        now=now,
    )


def test_a_connection_that_has_never_run_is_due_now() -> None:
    """A schedule whose first run is up to a day away is a control nobody can watch working —
    which is the half of #387 that hid the other half. Switching auto-sync on produces a run
    within the quarter-hour, and the connection settles onto its cadence from there."""
    now = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
    assert _next("daily", None, now=now) == now
    assert is_due(
        frequency="daily", interval_hours=4, at=AT, last_run=None, zone=AMS, now=now
    )


def test_a_daily_run_keeps_its_local_wall_clock_across_a_dst_change() -> None:
    """The whole point of moving the schedule off ``cron(hour=4, minute=20)``: that one was
    04:20 **UTC**, so the "nightly" arrived at 06:20 in Amsterdam in summer and 05:20 in winter
    and moved by an hour twice a year on the only clock the tenant has (§8)."""
    summer = _next("daily", datetime(2026, 8, 23, 2, 20, tzinfo=UTC), now=datetime.now(UTC))
    winter = _next("daily", datetime(2026, 1, 10, 3, 20, tzinfo=UTC), now=datetime.now(UTC))
    assert summer.astimezone(AMS).timetz().replace(tzinfo=None) == AT
    assert winter.astimezone(AMS).timetz().replace(tzinfo=None) == AT
    # …and they really are two different UTC offsets, or the assertion above proves nothing.
    assert summer.astimezone(AMS).utcoffset() != winter.astimezone(AMS).utcoffset()


def test_weekdays_only_skips_the_weekend() -> None:
    """An agency that does not work weekends does not need Saturday's read of somebody else's
    rate limit; Monday's run covers the window either way."""
    friday = datetime(2026, 8, 21, 2, 20, tzinfo=UTC)  # 04:20 local, a Friday
    assert _next("weekdays", friday, now=datetime.now(UTC)).astimezone(AMS).date() == (
        friday.astimezone(AMS).date() + timedelta(days=3)
    )
    # The daily cadence over the same Friday goes to Saturday — otherwise the test above is
    # asserting the calendar rather than the setting.
    assert _next("daily", friday, now=datetime.now(UTC)).astimezone(AMS).date() == (
        friday.astimezone(AMS).date() + timedelta(days=1)
    )


def test_an_interval_cadence_counts_from_the_last_run() -> None:
    last = datetime(2026, 8, 23, 9, 0, tzinfo=UTC)
    now = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
    assert _next("hourly", last, now=now) == last + timedelta(hours=1)
    assert _next("every_n_hours", last, now=now, interval=4) == last + timedelta(hours=4)
    assert is_due(
        frequency="every_n_hours", interval_hours=4, at=AT, last_run=last, zone=AMS, now=now
    ) is False
    assert is_due(
        frequency="every_n_hours", interval_hours=2, at=AT, last_run=last, zone=AMS, now=now
    )


def test_a_sub_daily_cadence_shortens_its_window_after_the_first_run_of_the_day() -> None:
    """Timeon's hour rows carry no modified timestamp, so the window *is* the sync — and
    re-reading the account's whole 45 days twenty-four times a day spends somebody's rate limit
    on an answer that has not changed. A deep reconcile once a day, cheap catch-up in between."""
    now = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
    same_day = datetime(2026, 8, 23, 9, 0, tzinfo=UTC)
    yesterday = datetime(2026, 8, 22, 23, 0, tzinfo=UTC)  # 01:00 local on the 23rd… see below
    assert (
        catch_up_days(frequency="hourly", last_run=same_day, zone=AMS, now=now)
        == CATCH_UP_WINDOW_DAYS
    )
    # 23:00 UTC on the 22nd is 01:00 *local* on the 23rd, so it is the same local day and still
    # a catch-up: the comparison is the org's calendar, never UTC's (§8).
    assert (
        catch_up_days(frequency="hourly", last_run=yesterday, zone=AMS, now=now)
        == CATCH_UP_WINDOW_DAYS
    )
    earlier_day = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
    assert catch_up_days(frequency="hourly", last_run=earlier_day, zone=AMS, now=now) is None
    # A daily cadence never shortens: its whole window is the point.
    assert catch_up_days(frequency="daily", last_run=same_day, zone=AMS, now=now) is None
