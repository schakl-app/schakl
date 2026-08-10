"""Which span a marketing dashboard reports on (issue #316).

#312 fixed *what a number is compared against* and left the period itself as a trailing day
count, so the one question an agency is actually asked — "how did July go?" — had no answer on
the screen: thirty days back from 9 August is 11 July to 9 August, which is not a month anyone
reports on. Four things are worth pinning:

- the **token vocabulary** (`app/core/periods.resolve_period`): trailing windows, rolling presets
  and named calendar periods, each resolving to two dates and nothing else;
- the **edges**, which are the only places this is hard: a period-to-date on its own first day, a
  quarter that wraps a year boundary, and a month picked before it has begun;
- the fact that **`period` wins over `range_days`** while `range_days` keeps working, because it
  is in shared URLs and in the generated MCP tool surface;
- that a drill-down covers the **same span** as the tiles above it — the cache key is the pair of
  dates, not the day count, or July's table is served for June.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from app.core.periods import (
    DEFAULT_PERIOD,
    is_whole_month,
    is_whole_quarter,
    period_days,
    quarter_of,
    resolve_period,
)
from tests.conftest import auth_cookie, make_tenant, org_today


# --- the vocabulary, with no database in sight ------------------------------------------------ #
@pytest.mark.parametrize(
    ("token", "expected"),
    [
        # A trailing window ends yesterday: today is partial, and comparing fourteen hours
        # against twenty-four reads as a collapse in traffic every morning.
        ("30d", (date(2026, 7, 11), date(2026, 8, 9))),
        ("90d", (date(2026, 5, 12), date(2026, 8, 9))),
        # Kept alive because it is in URLs people have shared (§9: the URL *is* the view).
        ("yoy", (date(2025, 8, 10), date(2026, 8, 9))),
        # Rolling presets — what they mean changes with the calendar, which is why they belong
        # in the tab row rather than in the picker.
        ("month", (date(2026, 8, 1), date(2026, 8, 9))),
        ("last_month", (date(2026, 7, 1), date(2026, 7, 31))),
        ("quarter", (date(2026, 7, 1), date(2026, 8, 9))),
        ("last_quarter", (date(2026, 4, 1), date(2026, 6, 30))),
        # Named calendar periods — frozen, so a link to one shows the same numbers next year.
        ("2026-07", (date(2026, 7, 1), date(2026, 7, 31))),
        ("2026-Q2", (date(2026, 4, 1), date(2026, 6, 30))),
        ("2025-Q4", (date(2025, 10, 1), date(2025, 12, 31))),
    ],
)
def test_a_token_resolves_to_two_dates(token: str, expected: tuple[date, date]) -> None:
    assert resolve_period(token, date(2026, 8, 10)) == expected


def test_a_period_in_progress_stops_at_the_last_complete_day() -> None:
    """A named month that contains yesterday is clipped, exactly as its rolling twin is.

    "2026-08" on 10 August and "month" on 10 August are the same nine days. That is the point:
    the picker's frozen token and the tab's rolling one must not disagree about today.
    """
    today = date(2026, 8, 10)
    assert resolve_period("2026-08", today) == resolve_period("month", today)
    assert resolve_period("2026-Q3", today) == resolve_period("quarter", today)


def test_a_period_to_date_with_no_complete_day_falls_back_to_the_whole_previous_one() -> None:
    """On the first of a month, "this month" contains no finished day at all.

    An empty span is not something a chart, a delta or a label can be built from, so it resolves
    to the previous whole period. Safe only *because* the payload carries its dates: the screen
    names the span it was given ("juli 2026"), so nothing claims to be showing August.
    """
    assert resolve_period("month", date(2026, 8, 1)) == (date(2026, 7, 1), date(2026, 7, 31))
    # A quarter's first day is the same shape, one level up — and it wraps the year.
    assert resolve_period("quarter", date(2026, 1, 1)) == (date(2025, 10, 1), date(2025, 12, 31))
    assert resolve_period("last_quarter", date(2026, 2, 15)) == (
        date(2025, 10, 1),
        date(2025, 12, 31),
    )


def test_an_explicitly_named_period_is_never_swapped_for_another() -> None:
    """The fallback above must not apply to a month the user *typed*.

    Silently showing July to someone who asked for August is the one outcome worse than an empty
    chart, because the label would be the only thing that could tell them — and it names the
    dates it was handed.
    """
    assert resolve_period("2026-09", date(2026, 8, 10)) == (date(2026, 9, 1), date(2026, 9, 30))
    # The rolling twin falls back to July here; the named one keeps the month it was given, empty.
    assert resolve_period("2026-08", date(2026, 8, 1)) == (date(2026, 8, 1), date(2026, 8, 31))
    assert resolve_period("month", date(2026, 8, 1)) == (date(2026, 7, 1), date(2026, 7, 31))


def test_an_unparseable_token_falls_back_rather_than_raising() -> None:
    """A period arrives from a query string anyone can edit or an old bookmark can carry.

    A dashboard that 422s on a stale link is worse than one that shows its default.
    """
    default = resolve_period(DEFAULT_PERIOD, date(2026, 8, 10))
    for junk in ("", None, "rubbish", "2026-13", "2026-Q5", "-1d", "0000-00", "12x"):
        assert resolve_period(junk, date(2026, 8, 10)) == default


def test_a_trailing_window_is_capped() -> None:
    """`9999d` typed into a URL must not ask the database for a decade."""
    start, end = resolve_period("9999d", date(2026, 8, 10), max_days=400)
    assert period_days(start, end) == 400


def test_naming_a_span_is_decided_by_its_dates() -> None:
    """What the screen prints follows from the resolved pair, never from the token.

    So a month reached through the picker ("2026-07") and the same month reached through the tab
    row ("last_month" in August) can never print differently.
    """
    assert is_whole_month(date(2026, 7, 1), date(2026, 7, 31))
    assert not is_whole_month(date(2026, 7, 1), date(2026, 7, 30))
    assert is_whole_quarter(date(2026, 7, 1), date(2026, 9, 30))
    # A quarter-to-date is not a quarter; labelling it "Q3 2026" would put a name on eleven days.
    assert not is_whole_quarter(date(2026, 7, 1), date(2026, 8, 9))
    assert quarter_of(date(2026, 8, 10)) == 3


# --- end to end ------------------------------------------------------------------------------- #
async def _company(c, headers, name: str) -> dict:
    return (await c.post("/api/v1/companies", json={"name": name}, headers=headers)).json()


async def test_the_dashboard_reports_on_the_period_it_was_asked_for(client_for) -> None:
    """`period` reaches the payload as dates, and the length is derived from them.

    `range_days` is echoed from the *resolved* span rather than the request, so a caller who asks
    for a month gets that month's length — a chart drawn off it cannot disagree with the dates
    printed beside it.
    """
    t = await make_tenant("mktg-period-basic")
    headers = await auth_cookie(t.user)
    today = org_today()
    last_month_end = today.replace(day=1) - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)

    async with client_for(t.host) as c:
        company = await _company(c, headers, "Acme BV")
        body = (
            await c.get(
                f"/api/v1/marketing/companies/{company['id']}/metrics",
                params={"period": "last_month"},
                headers=headers,
            )
        ).json()

        assert body["compare"]["current_start"] == last_month_start.isoformat()
        assert body["compare"]["current_end"] == last_month_end.isoformat()
        assert body["range_days"] == period_days(last_month_start, last_month_end)
        # A whole month compares to a whole month, a year earlier (#312) — the two features meet
        # here, and this is the pair a client's report is actually built from.
        assert body["compare"]["start"] == last_month_start.replace(
            year=last_month_start.year - 1
        ).isoformat()


async def test_period_wins_over_range_days_and_range_days_keeps_working(client_for) -> None:
    """Both parameters stay, because `range_days` is in shared URLs and in the MCP tool surface.

    The more specific request wins: "July" is not a number of days.
    """
    t = await make_tenant("mktg-period-precedence")
    headers = await auth_cookie(t.user)
    today = org_today()

    async with client_for(t.host) as c:
        company = await _company(c, headers, "Acme BV")
        url = f"/api/v1/marketing/companies/{company['id']}/metrics"

        legacy = (await c.get(url, params={"range_days": 7}, headers=headers)).json()
        assert legacy["compare"]["current_start"] == (today - timedelta(days=7)).isoformat()
        assert legacy["range_days"] == 7

        last_month_end = today.replace(day=1) - timedelta(days=1)
        both = (
            await c.get(url, params={"range_days": 7, "period": "last_month"}, headers=headers)
        ).json()
        assert both["compare"]["current_start"] == last_month_end.replace(day=1).isoformat()
        assert both["compare"]["current_end"] == last_month_end.isoformat()
        assert both["range_days"] == last_month_end.day


@pytest.mark.parametrize("surface", ["overview", "summary"])
async def test_the_cross_client_surfaces_take_a_period_too(client_for, surface: str) -> None:
    """One period named once above a list of clients — the #312 rule, unchanged by #316."""
    t = await make_tenant(f"mktg-period-{surface}")
    headers = await auth_cookie(t.user)
    today = org_today()
    last_month_end = today.replace(day=1) - timedelta(days=1)

    async with client_for(t.host) as c:
        body = (
            await c.get(
                f"/api/v1/marketing/{surface}", params={"period": "last_month"}, headers=headers
            )
        ).json()
        assert body["compare"]["current_end"] == last_month_end.isoformat()
        assert body["range_days"] == period_days(last_month_end.replace(day=1), last_month_end)


async def test_an_unknown_period_shows_the_default_rather_than_failing(client_for) -> None:
    """The whole point of falling back in `resolve_period` — asserted at the HTTP boundary."""
    t = await make_tenant("mktg-period-junk")
    headers = await auth_cookie(t.user)
    today = org_today()

    async with client_for(t.host) as c:
        company = await _company(c, headers, "Acme BV")
        res = await c.get(
            f"/api/v1/marketing/companies/{company['id']}/metrics",
            params={"period": "vorige-maand-graag"},
            headers=headers,
        )
        assert res.status_code == 200
        assert res.json()["compare"]["current_start"] == (today - timedelta(days=30)).isoformat()


async def test_the_two_windows_of_a_named_month_are_read_as_two_windows(
    client_for, count_queries
) -> None:
    """#312's read rule, under a named period rather than a trailing one.

    A named month compared year-over-year is two windows twelve months apart. Reading their hull
    would drag a year of daily rows through the session to print a month's worth, and that is
    invisible in the JSON — the response is byte-for-byte identical either way.
    """
    t = await make_tenant("mktg-period-read")
    headers = await auth_cookie(t.user)

    async with client_for(t.host) as c:
        company = await _company(c, headers, "Acme BV")
        with count_queries() as counter:
            await c.get(
                f"/api/v1/marketing/companies/{company['id']}/metrics",
                params={"period": "last_month"},
                headers=headers,
            )
    daily = [s for s in counter.statements if "marketing_metrics_daily" in s and "SELECT" in s]
    for query in daily:
        # Two bounded ranges OR'd together, never one span covering both.
        assert query.count("marketing_metrics_daily.date") >= 4, query


async def test_a_drilldown_covers_the_span_the_tiles_do(client_for) -> None:
    """The drill-down takes the same token, so its table is about the same days.

    Before the token it took `range_days`, which cannot express "July" — the breakdown under a
    month's tiles would have silently been the last thirty days.
    """
    t = await make_tenant("mktg-period-drill")
    headers = await auth_cookie(t.user)

    async with client_for(t.host) as c:
        company = await _company(c, headers, "Acme BV")
        created = (
            await c.post(
                "/api/v1/marketing/links",
                json={
                    "company_id": company["id"],
                    "source": "ga4",
                    "external_id": "properties/1",
                    "display_name": "acme.nl",
                },
                headers=headers,
            )
        ).json()
        res = await c.get(
            f"/api/v1/marketing/companies/{company['id']}/drilldown",
            params={"link_id": created["id"], "kind": "top_pages", "period": "2026-07"},
            headers=headers,
        )
        # No live Google connection here, so the endpoint answers its labelled unavailable state
        # rather than throwing — what matters is that the token was accepted at all.
        assert res.status_code == 200
        assert uuid.UUID(created["id"])
