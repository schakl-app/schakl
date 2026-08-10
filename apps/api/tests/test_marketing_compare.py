"""What a marketing dashboard measures against (issue #312).

Three things are worth pinning here, and only the first is arithmetic:

- the **window math** (`app/core/periods.py`), including the two rules that are easy to get
  wrong — a whole month steps to a whole month, and only 29 February moves when stepping a year;
- the **resolution chain** — the client's own setting, then the agency's default, then ``year`` —
  and the fact that a cross-client grid deliberately ignores the per-client half of it;
- the **read shape**: two bounded windows, never their hull, or a year-over-year dashboard drags
  eleven unused months through the session on every render (docs/PERFORMANCE.md).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from app.core.periods import ComparePeriod, compare_window, resolve_compare
from app.db import async_session_maker, set_current_org
from app.modules.marketing.models import MarketingLink, MarketingMetricDaily
from tests.conftest import auth_cookie, make_tenant, org_today


# --- the math, with no database in sight ---------------------------------------------------- #
def test_year_over_year_is_the_same_span_a_year_earlier() -> None:
    assert compare_window(date(2026, 7, 1), date(2026, 7, 31), "year") == (
        date(2025, 7, 1),
        date(2025, 7, 31),
    )
    assert compare_window(date(2026, 7, 11), date(2026, 8, 9), ComparePeriod.YEAR) == (
        date(2025, 7, 11),
        date(2025, 8, 9),
    )


def test_a_whole_month_compares_to_a_whole_month() -> None:
    """Subtracting 31 days from 1 July lands on 31 May, straddling two months and being neither."""
    assert compare_window(date(2026, 7, 1), date(2026, 7, 31), "previous") == (
        date(2026, 6, 1),
        date(2026, 6, 30),
    )
    # A trailing span is not a calendar month, so it steps back by its own length.
    assert compare_window(date(2026, 7, 12), date(2026, 8, 10), "previous") == (
        date(2026, 6, 12),
        date(2026, 7, 11),
    )


def test_only_the_leap_day_moves() -> None:
    """A leap-day *start* must not drag the end of the span back with it.

    The shape this was lifted from stepped both endpoints inside one ``try``, so 29 Feb – 31 Mar
    came back as 28 Feb – **28** Mar: three days of the comparison quietly gone, with nothing on
    any screen to show for it.
    """
    assert compare_window(date(2024, 2, 29), date(2024, 3, 31), "year") == (
        date(2023, 2, 28),
        date(2023, 3, 31),
    )


def test_resolution_is_a_fallback_chain() -> None:
    assert resolve_compare("previous", "year") is ComparePeriod.PREVIOUS
    assert resolve_compare(None, "previous") is ComparePeriod.PREVIOUS
    assert resolve_compare(None, None) is ComparePeriod.YEAR
    # A stored value a later release dropped falls through rather than 500-ing a dashboard.
    assert resolve_compare("fortnight", None) is ComparePeriod.YEAR


# --- helpers ---------------------------------------------------------------------------------- #
async def _seed(org_id, link_id: uuid.UUID, rows: dict[date, dict]) -> None:
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        for day, metrics in rows.items():
            session.add(
                MarketingMetricDaily(
                    org_id=org_id,
                    link_id=link_id,
                    date=day,
                    metrics=metrics,
                    synced_at=datetime.now(UTC),
                )
            )
        await session.commit()


async def _mark_synced(org_id, link_id: uuid.UUID) -> None:
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        link = await session.get(MarketingLink, link_id)
        link.backfill_done = True
        link.last_synced_at = datetime.now(UTC)
        await session.commit()


async def _link_ga4(c, headers, company_id: str) -> uuid.UUID:
    created = (
        await c.post(
            "/api/v1/marketing/links",
            json={
                "company_id": company_id,
                "source": "ga4",
                "external_id": "properties/1",
                "display_name": "acme.nl",
            },
            headers=headers,
        )
    ).json()
    return uuid.UUID(created["id"])


async def _company(c, headers, name: str) -> dict:
    return (await c.post("/api/v1/companies", json={"name": name}, headers=headers)).json()


# --- the resolution chain, end to end --------------------------------------------------------- #
async def test_dashboard_compares_to_last_year_by_default(client_for) -> None:
    """The default is the comparison a client asks about, and the payload names the span.

    Three days are seeded: one in the current window, one in the span immediately before it, and
    one a year back. Reading the *year* one is what distinguishes this from the old behaviour;
    reading it while `compare.start`/`compare.end` say something else would be the bug the issue
    is really about, so both are asserted together.
    """
    t = await make_tenant("mktg-cmp-default")
    headers = await auth_cookie(t.user)
    today = org_today()

    async with client_for(t.host) as c:
        company = await _company(c, headers, "Acme BV")
        link_id = await _link_ga4(c, headers, company["id"])
        await _seed(
            t.org.id,
            link_id,
            {
                today - timedelta(days=2): {"sessions": 120},
                today - timedelta(days=40): {"sessions": 999},  # the old "previous period"
                today - timedelta(days=367): {"sessions": 80},  # the same days last year
            },
        )
        await _mark_synced(t.org.id, link_id)

        body = (
            await c.get(
                f"/api/v1/marketing/companies/{company['id']}/metrics",
                params={"range_days": 30},
                headers=headers,
            )
        ).json()

        assert body["compare"]["mode"] == "year"
        assert body["compare"]["current_end"] == (today - timedelta(days=1)).isoformat()
        assert body["compare"]["start"] == (
            (today - timedelta(days=30)).replace(year=today.year - 1).isoformat()
        )
        kpi = body["sources"][0]["kpis"]["sessions"]
        assert kpi["current"] == 120
        assert kpi["previous"] == 80  # last year's, not the 999 sitting 40 days back
        assert kpi["delta_pct"] == 50.0
        # Nothing stored for this client yet — the select must show "follow the default".
        assert body["compare_setting"] is None
        assert body["compare_default"] == "year"


async def test_a_client_can_be_pinned_to_the_previous_period(client_for) -> None:
    """The per-client override, and the explicit ``null`` that clears it back to inherited."""
    t = await make_tenant("mktg-cmp-client")
    headers = await auth_cookie(t.user)
    today = org_today()

    async with client_for(t.host) as c:
        company = await _company(c, headers, "Seasonless BV")
        link_id = await _link_ga4(c, headers, company["id"])
        await _seed(
            t.org.id,
            link_id,
            {
                today - timedelta(days=2): {"sessions": 120},
                today - timedelta(days=40): {"sessions": 60},
                today - timedelta(days=367): {"sessions": 80},
            },
        )
        await _mark_synced(t.org.id, link_id)

        saved = await c.put(
            f"/api/v1/marketing/companies/{company['id']}/settings",
            json={"compare": "previous"},
            headers=headers,
        )
        assert saved.status_code == 200
        assert saved.json()["compare"] == "previous"
        assert saved.json()["compare_resolved"] == "previous"

        body = (
            await c.get(
                f"/api/v1/marketing/companies/{company['id']}/metrics",
                params={"range_days": 30},
                headers=headers,
            )
        ).json()
        assert body["compare"]["mode"] == "previous"
        assert body["compare"]["end"] == (today - timedelta(days=31)).isoformat()
        assert body["sources"][0]["kpis"]["sessions"]["previous"] == 60
        assert body["compare_setting"] == "previous"

        # An explicit null clears the override; omitting the field would have left it alone,
        # which is why "volg de standaard" has to be expressible at all (§18).
        cleared = await c.put(
            f"/api/v1/marketing/companies/{company['id']}/settings",
            json={"compare": None},
            headers=headers,
        )
        assert cleared.json()["compare"] is None
        assert cleared.json()["compare_resolved"] == "year"

        # And a write that says nothing about the comparison leaves it where it was.
        await c.put(
            f"/api/v1/marketing/companies/{company['id']}/settings",
            json={"compare": "previous"},
            headers=headers,
        )
        untouched = await c.put(
            f"/api/v1/marketing/companies/{company['id']}/settings",
            json={"show_key_events": True},
            headers=headers,
        )
        assert untouched.json()["compare"] == "previous"


async def test_the_org_default_is_what_an_unset_client_follows(client_for) -> None:
    t = await make_tenant("mktg-cmp-org")
    headers = await auth_cookie(t.user)
    today = org_today()

    async with client_for(t.host) as c:
        assert (
            await c.get("/api/v1/marketing/settings", headers=headers)
        ).json()["default_compare"] == "year"

        saved = await c.put(
            "/api/v1/marketing/settings",
            json={"default_compare": "previous"},
            headers=headers,
        )
        assert saved.json()["default_compare"] == "previous"

        company = await _company(c, headers, "Inheritor BV")
        link_id = await _link_ga4(c, headers, company["id"])
        await _seed(
            t.org.id,
            link_id,
            {
                today - timedelta(days=2): {"sessions": 120},
                today - timedelta(days=40): {"sessions": 60},
                today - timedelta(days=367): {"sessions": 80},
            },
        )
        await _mark_synced(t.org.id, link_id)

        body = (
            await c.get(
                f"/api/v1/marketing/companies/{company['id']}/metrics",
                params={"range_days": 30},
                headers=headers,
            )
        ).json()
        assert body["compare"]["mode"] == "previous"
        assert body["compare_default"] == "previous"
        assert body["compare_setting"] is None
        assert body["sources"][0]["kpis"]["sessions"]["previous"] == 60

        # Saving an unrelated field must not silently reset the default to `year`: this screen
        # writes two write-only secrets in the same request, and "omitted keeps the stored one"
        # has to mean the same thing for all three.
        await c.put("/api/v1/marketing/settings", json={}, headers=headers)
        assert (
            await c.get("/api/v1/marketing/settings", headers=headers)
        ).json()["default_compare"] == "previous"


async def test_the_cross_client_grid_uses_one_comparison_for_every_row(client_for) -> None:
    """A client's own override governs their dashboard, never the board that ranks them.

    Two clients, opposite settings, one grid: both rows must be measured the same way, because a
    column sorted on percentages whose denominators differ per row ranks nothing at all.
    """
    t = await make_tenant("mktg-cmp-grid")
    headers = await auth_cookie(t.user)
    today = org_today()

    async with client_for(t.host) as c:
        rows = {
            today - timedelta(days=2): {"sessions": 120},
            today - timedelta(days=40): {"sessions": 60},
            today - timedelta(days=367): {"sessions": 80},
        }
        for name, mode in (("Pinned BV", "previous"), ("Default BV", None)):
            company = await _company(c, headers, name)
            link_id = await _link_ga4(c, headers, company["id"])
            await _seed(t.org.id, link_id, rows)
            await _mark_synced(t.org.id, link_id)
            if mode is not None:
                await c.put(
                    f"/api/v1/marketing/companies/{company['id']}/settings",
                    json={"compare": mode},
                    headers=headers,
                )

        grid = (
            await c.get(
                "/api/v1/marketing/overview", params={"range_days": 30}, headers=headers
            )
        ).json()
        assert grid["compare"]["mode"] == "year"
        assert {row["metrics"]["sessions"]["previous"] for row in grid["rows"]} == {80}

        # The My Day digest is the same list under a different name, so it answers the same way.
        digest = (
            await c.get(
                "/api/v1/marketing/summary", params={"range_days": 30}, headers=headers
            )
        ).json()
        assert digest["compare"]["mode"] == "year"
        assert {row["kpi"]["previous"] for row in digest["rows"]} == {80}


# --- the read shape ---------------------------------------------------------------------------- #
async def test_the_two_windows_are_read_as_two_windows(client_for, count_queries) -> None:
    """Never the hull of the two.

    Invisible in the JSON and expensive in the database: a year-over-year 12-month dashboard whose
    daily read spans ``[start_of_last_year's_window, yesterday]`` pulls three years of rows to
    print two. So the day sitting *between* the windows must not come back, and the statement
    must carry two bounded ranges rather than one.
    """
    t = await make_tenant("mktg-cmp-spans")
    headers = await auth_cookie(t.user)
    today = org_today()

    async with client_for(t.host) as c:
        company = await _company(c, headers, "Span BV")
        link_id = await _link_ga4(c, headers, company["id"])
        await _seed(
            t.org.id,
            link_id,
            {
                today - timedelta(days=2): {"sessions": 120},
                today - timedelta(days=180): {"sessions": 5000},  # between the two windows
                today - timedelta(days=367): {"sessions": 80},
            },
        )
        await _mark_synced(t.org.id, link_id)

        with count_queries() as counter:
            body = (
                await c.get(
                    f"/api/v1/marketing/companies/{company['id']}/metrics",
                    params={"range_days": 30},
                    headers=headers,
                )
            ).json()

        kpi = body["sources"][0]["kpis"]["sessions"]
        assert (kpi["current"], kpi["previous"]) == (120, 80)  # the 5000 belongs to neither

        daily = [s for s in counter.statements if "marketing_metrics_daily" in s and "SELECT" in s]
        assert len(daily) == 1, "the panel's daily read is one query, per link count"
        # Resolving the client's setting and the agency's default is *one* statement, not two:
        # the company hub composes a provider per module in sequence, so a per-panel "+1" is how
        # that page gets slow (docs/PERFORMANCE.md, the #290 budget).
        settings_reads = [
            s
            for s in counter.statements
            if "marketing_company_settings" in s or "marketing_settings" in s
        ]
        assert len(settings_reads) == 1, "\n".join(settings_reads)
        # Two lower bounds = two windows. One would mean the hull, and the Python filter above
        # would still produce the right numbers — which is exactly why this is asserted here.
        assert daily[0].count("marketing_metrics_daily.date >=") == 2


async def test_changing_the_comparison_lands_on_the_activity_trail(client_for) -> None:
    """It re-bases every percentage on the client's dashboard without touching any data, so
    "these numbers changed and nobody edited anything" needs an answer (§16)."""
    from sqlalchemy import select

    from app.core.activity.models import ActivityLog

    t = await make_tenant("mktg-cmp-trail")
    headers = await auth_cookie(t.user)

    async with client_for(t.host) as c:
        company = await _company(c, headers, "Trail BV")
        await c.put(
            f"/api/v1/marketing/companies/{company['id']}/settings",
            json={"compare": "previous"},
            headers=headers,
        )
        # A no-op re-save must not add a second line.
        await c.put(
            f"/api/v1/marketing/companies/{company['id']}/settings",
            json={"compare": "previous"},
            headers=headers,
        )

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        entries = (
            await session.execute(
                select(ActivityLog).where(ActivityLog.action == "marketing.compare_changed")
            )
        ).scalars().all()
    assert len(entries) == 1
    assert entries[0].payload["changes"]["compare"] == {"from": None, "to": "previous"}


@pytest.mark.parametrize("mode", ["fortnight", "", "YEAR"])
async def test_an_unknown_comparison_is_refused_at_the_door(client_for, mode: str) -> None:
    """The stored vocabulary is a closed set; a stray value would resolve to the default forever
    and read as a screen ignoring its own setting."""
    t = await make_tenant(f"mktg-cmp-bad-{abs(hash(mode)) % 10000}")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers, "Strict BV")
        response = await c.put(
            f"/api/v1/marketing/companies/{company['id']}/settings",
            json={"compare": mode},
            headers=headers,
        )
        assert response.status_code == 422
