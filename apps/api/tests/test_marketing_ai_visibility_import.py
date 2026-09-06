"""Search Console's Generative AI report, brought in by hand (docs/GOOGLE_SEARCH_CONSOLE.md §6a).

Google draws impressions in AI Overviews and AI Mode in the console, offers an export button,
and returns the figure through no API. So the tile on the dashboard and the section in the
report come from the uploaded export — and everything below is about the three ways that can go
quietly wrong: a file that is not this report read as zeros, a nightly sync wiping what was
uploaded, and a client who never had an upload printing "0".
"""

from __future__ import annotations

import io
import uuid
import zipfile
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db import async_session_maker, set_current_org
from app.errors import AppError
from app.modules.companies.models import Company
from app.modules.marketing.aiv_import import parse_export, parse_rows
from app.modules.marketing.models import MarketingLink, MarketingMetricDaily
from app.modules.marketing.report_sections import (
    _CACHE_ATTR,
    GatheredMarketing,
    Part,
    _ai_overviews,
    _search_console,
)
from app.modules.marketing.service import _upsert_daily, aggregate
from app.modules.marketing.sources.base import DailyMetrics
from app.registry import ReportWindow
from tests.conftest import auth_cookie, make_tenant
from tests.test_marketing_api import _add_member, _mark_synced, _seed_metrics


# --- reading the file ------------------------------------------------------------------------ #
def test_the_dates_table_is_read_and_googles_zeros_are_zeros() -> None:
    parsed = parse_export(
        b'Date,Impressions\n2026-08-01,"1,234"\n2026-08-02,~\n2026-08-03,-\n\nTotal,1234\n'
    )
    assert parsed.rows == {date(2026, 8, 1): 1234, date(2026, 8, 2): 0, date(2026, 8, 3): 0}
    assert parsed.date_from == date(2026, 8, 1)
    assert parsed.date_to == date(2026, 8, 3)
    # The total line is not a day: skipped and counted, never read as 12 January 34.
    assert parsed.skipped == 1


def test_a_dutch_console_exports_dutch_headers_and_semicolons() -> None:
    parsed = parse_export(b"Datum;Vertoningen\n2026-08-01;1.234\n2026-08-02;56\n")
    assert parsed.rows == {date(2026, 8, 1): 1234, date(2026, 8, 2): 56}


def test_the_zip_from_the_export_button_is_searched_for_the_dates_table() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Pages.csv", "Top pages,Impressions\nhttps://klant.nl/,900\n")
        archive.writestr("Dates.csv", "Date,Impressions\n2026-08-01,12\n2026-08-02,30\n")
        archive.writestr("Devices.csv", "Device,Impressions\nMOBILE,40\n")
    parsed = parse_export(buffer.getvalue())
    assert parsed.member == "Dates.csv"
    assert parsed.rows == {date(2026, 8, 1): 12, date(2026, 8, 2): 30}


def test_a_zip_without_a_dates_table_is_refused_naming_what_it_held() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Pages.csv", "Top pages,Impressions\nhttps://klant.nl/,900\n")
    with pytest.raises(AppError) as refused:
        parse_export(buffer.getvalue())
    assert refused.value.message_key == "errors.marketing_ai_export_no_dates"
    assert refused.value.details == {"members": ["Pages.csv"]}


def test_a_weekly_export_is_refused_not_stored_as_seven_times_too_small() -> None:
    with pytest.raises(AppError) as refused:
        parse_export(b"Week,Impressions\n2026-08-03,840\n")
    assert refused.value.message_key == "errors.marketing_ai_export_grouped"


def test_a_table_that_is_not_this_report_is_refused_with_its_columns() -> None:
    with pytest.raises(AppError) as refused:
        parse_export(b"Query,Clicks,Position\nfietsen,12,3.4\n")
    assert refused.value.message_key == "errors.marketing_ai_export_unrecognised"
    assert refused.value.details == {"columns": ["Query", "Clicks", "Position"]}


def test_the_json_twin_reads_by_the_same_rules() -> None:
    parsed = parse_rows([("2026-08-01", 12), ("2026-08-02", "1.500")])
    assert parsed.rows == {date(2026, 8, 1): 12, date(2026, 8, 2): 1500}
    with pytest.raises(AppError) as refused:
        parse_rows([("01-08-2026", 12)])
    assert refused.value.message_key == "errors.marketing_ai_export_bad_date"


# --- absent is not zero --------------------------------------------------------------------- #
def test_a_period_with_no_upload_carries_no_ai_figure_at_all() -> None:
    """``aggregate`` leaves the imported key out rather than summing it to 0, which is what
    keeps a tile off the dashboard and a section off the report for a client whose agency
    never uploaded the export — a "0" there would be a claim about their AI visibility."""
    synced_only = [{"clicks": 5.0, "impressions": 100.0, "ctr": 0.05, "position": 4.0}]
    assert "ai_impressions" not in aggregate("gsc", synced_only)
    with_upload = synced_only + [{"impressions": 10.0, "ai_impressions": 7.0}]
    assert aggregate("gsc", with_upload)["ai_impressions"] == 7.0


async def test_the_nightly_sync_keeps_what_was_uploaded(client_for) -> None:
    """The trailing-window re-pull replaces a day's metrics wholesale; the imported figure on
    that day must survive it, or every upload lasts one night."""
    del client_for  # binds the engine to this test's loop; nothing here goes over HTTP
    t = await make_tenant("mktg-ai-sync")
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        company = Company(org_id=t.org.id, name="Keep BV")
        session.add(company)
        await session.flush()
        link = MarketingLink(
            org_id=t.org.id,
            company_id=company.id,
            source="gsc",
            external_id="sc-domain:keep.nl",
            display_name="keep.nl",
            active=True,
        )
        session.add(link)
        await session.flush()
        day = date(2026, 8, 5)
        session.add(
            MarketingMetricDaily(
                org_id=t.org.id,
                link_id=link.id,
                date=day,
                metrics={"clicks": 1.0, "ai_impressions": 42.0},
                synced_at=datetime.now(UTC),
            )
        )
        await session.flush()
        await _upsert_daily(
            session,
            link,
            [DailyMetrics(day=day, metrics={"clicks": 3.0, "impressions": 80.0})],
        )
        row = await session.scalar(
            select(MarketingMetricDaily).where(MarketingMetricDaily.link_id == link.id)
        )
        assert row is not None
        assert row.metrics == {"clicks": 3.0, "impressions": 80.0, "ai_impressions": 42.0}
        await session.rollback()


# --- the routes ------------------------------------------------------------------------------ #
async def _client_with_gsc(client, headers, name: str, site: str) -> tuple[dict, dict]:
    company = (await client.post("/api/v1/companies", json={"name": name}, headers=headers)).json()
    link = (
        await client.post(
            "/api/v1/marketing/links",
            json={
                "company_id": company["id"],
                "source": "gsc",
                "external_id": site,
                "display_name": site.removeprefix("sc-domain:"),
            },
            headers=headers,
        )
    ).json()
    return company, link


async def test_an_upload_lands_beside_the_synced_metrics_and_becomes_a_tile(client_for) -> None:
    t = await make_tenant("mktg-ai-import")
    headers = await auth_cookie(t.user)
    today = date.today()
    d1, d2, d3 = (today - timedelta(days=n) for n in (4, 3, 2))

    async with client_for(t.host) as c:
        company, link = await _client_with_gsc(c, headers, "Spark BV", "sc-domain:spark.nl")
        _, other = await _client_with_gsc(c, headers, "Quiet BV", "sc-domain:quiet.nl")
        link_id = uuid.UUID(link["id"])
        # Two synced days; the upload names one of them and one more.
        await _seed_metrics(
            t.org.id,
            link_id,
            {
                d1: {"clicks": 5, "impressions": 100, "ctr": 0.05, "position": 4.0},
                d2: {"clicks": 7, "impressions": 140, "ctr": 0.05, "position": 4.0},
            },
        )
        await _mark_synced(t.org.id, link_id)
        await _mark_synced(t.org.id, uuid.UUID(other["id"]))

        csv = f"Date,Impressions\n{d2.isoformat()},30\n{d3.isoformat()},12\n".encode()
        res = await c.post(
            f"/api/v1/marketing/links/{link['id']}/ai-visibility/import",
            files={"file": ("Dates.csv", csv, "text/csv")},
            headers=headers,
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["days"] == 2
        assert body["total"] == 42.0
        assert body["date_from"] == d2.isoformat()
        assert body["date_to"] == d3.isoformat()

        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            rows = {
                row.date: row.metrics
                for row in (
                    await session.execute(
                        select(MarketingMetricDaily).where(MarketingMetricDaily.link_id == link_id)
                    )
                ).scalars()
            }
        # Merged into the synced day, created for the day the sync had not reached.
        assert rows[d2]["clicks"] == 7
        assert rows[d2]["ai_impressions"] == 30.0
        assert rows[d3] == {"ai_impressions": 12.0}
        # The day the file did not name is untouched.
        assert "ai_impressions" not in rows[d1]

        metrics = (
            await c.get(
                f"/api/v1/marketing/companies/{company['id']}/metrics",
                params={"range_days": 30},
                headers=headers,
            )
        ).json()
        gsc = next(s for s in metrics["sources"] if s["source"] == "gsc")
        assert gsc["kpis"]["ai_impressions"]["current"] == 42.0
        assert gsc["tiles"][-1] == "ai_impressions"
        assert gsc["ai_visibility"]["imported"]["days"] == 2
        assert gsc["ai_visibility"]["imported"]["date_to"] == d3.isoformat()
        # The trend carries the imported days and zeros elsewhere.
        series = gsc["series"]["metrics"]["ai_impressions"]
        assert sum(series) == 42.0

        # The activity trail says who put numbers under the client's name.
        trail = (
            await c.get(
                "/api/v1/activity",
                params={"entity_type": "company", "entity_id": company["id"]},
                headers=headers,
            )
        ).json()
        entries = trail if isinstance(trail, list) else trail.get("items", [])
        actions = [entry["action"] for entry in entries]
        assert "marketing.ai_imported" in actions


async def test_a_client_with_no_upload_gets_no_ai_tile(client_for) -> None:
    t = await make_tenant("mktg-ai-none")
    headers = await auth_cookie(t.user)
    today = date.today()
    async with client_for(t.host) as c:
        company, link = await _client_with_gsc(c, headers, "Quiet BV", "sc-domain:quiet.nl")
        link_id = uuid.UUID(link["id"])
        await _seed_metrics(
            t.org.id,
            link_id,
            {today - timedelta(days=2): {"clicks": 5, "impressions": 100}},
        )
        await _mark_synced(t.org.id, link_id)
        metrics = (
            await c.get(
                f"/api/v1/marketing/companies/{company['id']}/metrics",
                params={"range_days": 30},
                headers=headers,
            )
        ).json()
        gsc = next(s for s in metrics["sources"] if s["source"] == "gsc")
        assert "ai_impressions" not in gsc["kpis"]
        assert "ai_impressions" not in gsc["tiles"]
        assert "ai_impressions" not in gsc["series"]["metrics"]
        assert gsc["ai_visibility"]["available"] is False
        assert gsc["ai_visibility"]["imported"] is None


async def test_the_json_twin_writes_the_same_rows(client_for) -> None:
    t = await make_tenant("mktg-ai-json")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, link = await _client_with_gsc(c, headers, "Twin BV", "sc-domain:twin.nl")
        res = await c.post(
            f"/api/v1/marketing/links/{link['id']}/ai-visibility/rows",
            json={"rows": [{"day": "2026-08-01", "impressions": 5}]},
            headers=headers,
        )
        assert res.status_code == 200, res.text
        assert res.json()["total"] == 5.0


async def test_the_upload_is_refused_where_it_cannot_mean_anything(client_for) -> None:
    """Not on a GA4 link, not by a member who may not manage links, not on another tenant's
    link (a 404, never a 403 — the row's existence is not for them to learn), and not for a
    file that is not the report."""
    a = await make_tenant("mktg-ai-refuse-a")
    b = await make_tenant("mktg-ai-refuse-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)
    member = await _add_member(a.org.id, "viewer@mktg-ai.test")
    member_headers = await auth_cookie(member, a.org.id)
    csv = b"Date,Impressions\n2026-08-01,5\n"

    async with client_for(a.host) as c:
        company, gsc = await _client_with_gsc(c, a_headers, "Refuse BV", "sc-domain:refuse.nl")
        ga4 = (
            await c.post(
                "/api/v1/marketing/links",
                json={
                    "company_id": company["id"],
                    "source": "ga4",
                    "external_id": "properties/9",
                    "display_name": "Refuse — GA4",
                },
                headers=a_headers,
            )
        ).json()
        wrong_source = await c.post(
            f"/api/v1/marketing/links/{ga4['id']}/ai-visibility/import",
            files={"file": ("Dates.csv", csv, "text/csv")},
            headers=a_headers,
        )
        assert wrong_source.status_code == 422
        assert wrong_source.json()["error"]["message"] == "errors.marketing_ai_export_source"

        not_the_report = await c.post(
            f"/api/v1/marketing/links/{gsc['id']}/ai-visibility/import",
            files={"file": ("Queries.csv", b"Query,Clicks\nfietsen,3\n", "text/csv")},
            headers=a_headers,
        )
        assert not_the_report.status_code == 422
        assert (
            not_the_report.json()["error"]["message"] == "errors.marketing_ai_export_unrecognised"
        )

        forbidden = await c.post(
            f"/api/v1/marketing/links/{gsc['id']}/ai-visibility/import",
            files={"file": ("Dates.csv", csv, "text/csv")},
            headers=member_headers,
        )
        assert forbidden.status_code == 403

    async with client_for(b.host) as cb:
        leaked = await cb.post(
            f"/api/v1/marketing/links/{gsc['id']}/ai-visibility/import",
            files={"file": ("Dates.csv", csv, "text/csv")},
            headers=b_headers,
        )
        assert leaked.status_code == 404


# --- the report section ---------------------------------------------------------------------- #
class _Ctx:
    """Seeding ``gather``'s own memo is what reaches a provider without a database."""


def _seeded(data: GatheredMarketing) -> tuple[_Ctx, ReportWindow]:
    window = ReportWindow(
        company_id=uuid.uuid4(),
        start=date(2026, 7, 1),
        end=date(2026, 7, 31),
        compare_start=date(2025, 7, 1),
        compare_end=date(2025, 7, 31),
    )
    ctx = _Ctx()
    setattr(
        ctx,
        _CACHE_ATTR,
        {(window.company_id, window.start, window.end, window.compare_start): data},
    )
    return ctx, window


def _gsc_stored(*, imported: bool) -> GatheredMarketing:
    totals = {"clicks": 511.0, "impressions": 20000.0, "ctr": 0.0255, "position": 12.1}
    compare = {"clicks": 480.0, "impressions": 18000.0, "ctr": 0.0266, "position": 13.0}
    stored = {
        "totals": dict(totals),
        "compare": dict(compare),
        "channels": {},
        "compare_channels": {},
        "days": 31,
        "compare_days": 31,
        "currency": None,
        "display_name": "klant.nl",
        "imported": {},
        "compare_imported": {},
    }
    if imported:
        stored["totals"]["ai_impressions"] = 640.0
        stored["compare"]["ai_impressions"] = 200.0
        stored["imported"] = {
            "ai_impressions": {date(2026, 7, d): 20.0 for d in range(1, 32)},
        }
        stored["imported"]["ai_impressions"][date(2026, 7, 31)] = 40.0
        stored["compare_imported"] = {
            "ai_impressions": {date(2025, 7, d): 10.0 for d in range(1, 21)},
        }
    return GatheredMarketing(
        parts={"gsc": [Part(key="gsc", label="", links=())]},
        stored={"gsc": stored},
    )


async def test_the_ai_section_is_the_month_by_week_against_the_comparison() -> None:
    ctx, window = _seeded(_gsc_stored(imported=True))

    payload = await _ai_overviews(ctx, window)  # type: ignore[arg-type]

    assert payload is not None
    assert payload["kind"] == "ai_overviews"
    assert payload["totals"] == {"ai_impressions": 640.0}
    assert payload["compare"] == {"ai_impressions": 200.0}
    chart = payload["chart"]
    assert chart["labels"] == ["1-7", "8-14", "15-21", "22-28", "29-31"]
    assert chart["series"][0]["values"] == [140.0, 140.0, 140.0, 140.0, 80.0]
    # The comparison month was uploaded for twenty days: three full weeks and a partial one,
    # and a fifth bar of zero rather than a series shifted to fit.
    assert chart["series"][1]["values"] == [70.0, 70.0, 60.0, 0.0, 0.0]


async def test_the_search_console_section_leaves_the_ai_figure_to_its_own_section() -> None:
    ctx, window = _seeded(_gsc_stored(imported=True))

    payload = await _search_console(ctx, window)  # type: ignore[arg-type]

    assert payload is not None
    assert "ai_impressions" not in payload["totals"]
    assert "ai_impressions" not in payload["compare"]
    assert payload["columns"] == ["clicks", "impressions", "ctr", "position"]


async def test_a_client_without_an_upload_gets_no_ai_section() -> None:
    ctx, window = _seeded(_gsc_stored(imported=False))

    assert await _ai_overviews(ctx, window) is None  # type: ignore[arg-type]
    console = await _search_console(ctx, window)  # type: ignore[arg-type]
    assert console is not None and console["totals"]["clicks"] == 511.0
