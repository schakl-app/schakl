"""Marketing module (epic #134): link round-trip, tenant isolation, stored-metric aggregation.

The Google-facing paths (pickers, drill-downs, the nightly sync) can't be exercised without a
live Google, so this covers what does not need one: the link CRUD, RLS isolation on both tables,
and — the load-bearing logic — the daily-aggregate reads that power the panel/tab/overview,
seeded directly into ``marketing_metrics_daily``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.activity.models import ActivityLog
from app.core.crypto import decrypt
from app.db import async_session_maker, set_current_org
from app.modules.marketing.models import (
    MarketingLink,
    MarketingMetricDaily,
    MarketingSettings,
)
from app.modules.marketing.service import resolve_ads_developer_token
from app.modules.marketing.sources import gads
from tests.conftest import auth_cookie, make_tenant


async def _seed_metrics(org_id, link_id: uuid.UUID, rows: dict[date, dict]) -> None:
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
    """Flip a link to 'synced' so its panel health reads ``ok`` rather than ``pending``."""
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        link = await session.get(MarketingLink, link_id)
        link.backfill_done = True
        link.last_synced_at = datetime.now(UTC)
        await session.commit()


async def test_link_roundtrip_and_isolation(client_for) -> None:
    a = await make_tenant("mktg-a")
    b = await make_tenant("mktg-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)

    async with client_for(a.host) as ca:
        company = (
            await ca.post("/api/v1/companies", json={"name": "Acme BV"}, headers=a_headers)
        ).json()

        created = await ca.post(
            "/api/v1/marketing/links",
            json={
                "company_id": company["id"],
                "source": "ga4",
                "external_id": "properties/123456789",
                "display_name": "Acme — GA4",
            },
            headers=a_headers,
        )
        assert created.status_code == 201, created.text
        link = created.json()
        assert link["source"] == "ga4"
        assert link["active"] is True

        listed = (
            await ca.get(
                "/api/v1/marketing/links",
                params={"company_id": company["id"]},
                headers=a_headers,
            )
        ).json()
        assert [row["id"] for row in listed] == [link["id"]]

        # Unlink deactivates (history stays attributable), it does not delete.
        assert (
            await ca.delete(f"/api/v1/marketing/links/{link['id']}", headers=a_headers)
        ).status_code == 204
        after = (
            await ca.get(
                "/api/v1/marketing/links",
                params={"company_id": company["id"]},
                headers=a_headers,
            )
        ).json()
        assert after[0]["active"] is False

        # Relinking the same property reactivates the same row.
        relinked = await ca.post(
            "/api/v1/marketing/links",
            json={
                "company_id": company["id"],
                "source": "ga4",
                "external_id": "properties/123456789",
                "display_name": "Acme — GA4",
            },
            headers=a_headers,
        )
        assert relinked.status_code == 201
        assert relinked.json()["id"] == link["id"]
        assert relinked.json()["active"] is True

    # Tenant isolation: org B cannot see org A's company or its links.
    async with client_for(b.host) as cb:
        leaked = await cb.get(
            "/api/v1/marketing/links",
            params={"company_id": company["id"]},
            headers=b_headers,
        )
        assert leaked.status_code == 404


async def test_metrics_aggregation_and_deltas(client_for) -> None:
    t = await make_tenant("mktg-metrics")
    headers = await auth_cookie(t.user)
    today = date.today()

    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Trend BV"}, headers=headers)
        ).json()
        ga4 = (
            await c.post(
                "/api/v1/marketing/links",
                json={
                    "company_id": company["id"],
                    "source": "ga4",
                    "external_id": "properties/1",
                    "display_name": "Trend — GA4",
                },
                headers=headers,
            )
        ).json()
        gsc = (
            await c.post(
                "/api/v1/marketing/links",
                json={
                    "company_id": company["id"],
                    "source": "gsc",
                    "external_id": "sc-domain:trend.nl",
                    "display_name": "trend.nl",
                },
                headers=headers,
            )
        ).json()

        link_id = uuid.UUID(ga4["id"])
        # Current window (safely inside [today-30, today-1]) vs previous ([today-60, today-31]).
        await _seed_metrics(
            t.org.id,
            link_id,
            {
                today - timedelta(days=2): {"sessions": 120, "conversions": 10},
                today - timedelta(days=40): {"sessions": 80, "conversions": 5},
            },
        )
        # GSC: two current days — period position is impression-weighted, not the mean of 5 & 8.
        await _seed_metrics(
            t.org.id,
            uuid.UUID(gsc["id"]),
            {
                today - timedelta(days=2): {
                    "clicks": 50, "impressions": 1000, "position": 5.0, "ctr": 0.05
                },
                today - timedelta(days=3): {
                    "clicks": 30, "impressions": 500, "position": 8.0, "ctr": 0.06
                },
            },
        )
        await _mark_synced(t.org.id, link_id)
        await _mark_synced(t.org.id, uuid.UUID(gsc["id"]))

        body = (
            await c.get(
                f"/api/v1/marketing/companies/{company['id']}/metrics",
                params={"range_days": 30},
                headers=headers,
            )
        ).json()
        by_source = {s["source"]: s for s in body["sources"]}

        ga4_kpis = by_source["ga4"]["kpis"]
        assert ga4_kpis["sessions"]["current"] == 120
        assert ga4_kpis["sessions"]["previous"] == 80
        assert ga4_kpis["sessions"]["delta_pct"] == 50.0
        assert ga4_kpis["conversions"]["current"] == 10
        # A gap-free daily series across the 30-day window feeds the sparkline.
        assert len(by_source["ga4"]["series"]["dates"]) == 30
        assert by_source["ga4"]["primary_metric"] == "sessions"

        gsc_kpis = by_source["gsc"]["kpis"]
        assert gsc_kpis["clicks"]["current"] == 80  # 50 + 30
        # (5.0*1000 + 8.0*500) / 1500 = 6.0 — impression-weighted, and down is good.
        assert gsc_kpis["position"]["current"] == 6.0
        assert gsc_kpis["position"]["lower_is_better"] is True


async def test_overview_grid_from_stored_data(client_for) -> None:
    t = await make_tenant("mktg-overview")
    headers = await auth_cookie(t.user)
    today = date.today()

    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Grid BV"}, headers=headers)
        ).json()
        ga4 = (
            await c.post(
                "/api/v1/marketing/links",
                json={
                    "company_id": company["id"],
                    "source": "ga4",
                    "external_id": "properties/9",
                    "display_name": "Grid — GA4",
                },
                headers=headers,
            )
        ).json()
        await _seed_metrics(
            t.org.id,
            uuid.UUID(ga4["id"]),
            {today - timedelta(days=2): {"sessions": 200, "conversions": 7}},
        )

        overview = (
            await c.get(
                "/api/v1/marketing/overview", params={"range_days": 30}, headers=headers
            )
        ).json()
        assert overview["total"] == 1
        row = overview["rows"][0]
        assert row["company_name"] == "Grid BV"
        assert row["metrics"]["sessions"]["current"] == 200
        assert "ga4" in row["sources_present"]


async def test_metrics_needs_connection_when_no_google(client_for) -> None:
    t = await make_tenant("mktg-noconn")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Bare BV"}, headers=headers)
        ).json()
        body = (
            await c.get(
                f"/api/v1/marketing/companies/{company['id']}/metrics", headers=headers
            )
        ).json()
        assert body["needs_connection"] is True
        assert body["sources"] == []


async def test_backfill_rebinds_rls_across_chunk_commits(client_for, monkeypatch) -> None:
    """Regression: the 13-month backfill commits per chunk, but the RLS GUC is transaction-local
    (``set_config(..., is_local=true)``) so each commit clears it. Without re-binding, the second
    chunk's RLS-scoped UPDATE on ``marketing_links`` matches zero rows and SQLAlchemy raises
    StaleDataError, crashing the job. A no-op sync (no Google needed) that dirties the link every
    chunk drives the whole multi-commit loop and must complete cleanly.
    """
    from app.modules.marketing.jobs import marketing_backfill_link

    t = await make_tenant("mktg-backfill")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Backfill BV"}, headers=headers)
        ).json()
        link = (
            await c.post(
                "/api/v1/marketing/links",
                json={
                    "company_id": company["id"],
                    "source": "ga4",
                    "external_id": "properties/7",
                    "display_name": "Backfill — GA4",
                },
                headers=headers,
            )
        ).json()

    async def fake_sync(session, org, lk, start, end):  # noqa: ANN001, ARG001
        # Succeeds without Google, and marks the link dirty so every chunk's commit issues a
        # real RLS-scoped UPDATE — the exact statement that crashed before the GUC re-bind.
        lk.last_synced_at = datetime.now(UTC)
        lk.last_error = None

    monkeypatch.setattr("app.modules.marketing.jobs.sync_link_range", fake_sync)

    # Must not raise (StaleDataError before the fix) and must run to completion.
    await marketing_backfill_link({}, str(t.org.id), link["id"])

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        row = await session.get(MarketingLink, uuid.UUID(link["id"]))
        assert row.backfill_done is True
        assert row.last_error is None


async def test_nightly_resumes_incomplete_backfill(client_for, monkeypatch) -> None:
    """A link whose backfill never completed (backfill_done False) is re-enqueued by the nightly
    sync, so a backfill interrupted at v0.9.0 self-heals without a manual relink."""
    from app.modules.marketing import jobs as mjobs

    t = await make_tenant("mktg-resume")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Resume BV"}, headers=headers)
        ).json()
        link = (
            await c.post(
                "/api/v1/marketing/links",
                json={
                    "company_id": company["id"],
                    "source": "ga4",
                    "external_id": "properties/3",
                    "display_name": "Resume — GA4",
                },
                headers=headers,
            )
        ).json()

    calls: list[tuple] = []

    async def fake_enqueue(fn, *args, **kwargs):  # noqa: ANN001, ANN202
        calls.append((fn, args))

    monkeypatch.setattr("app.modules.marketing.jobs.enqueue", fake_enqueue)
    await mjobs.marketing_sync_all({})

    assert any(
        fn == "marketing_backfill_link" and link["id"] in args for fn, args in calls
    ), "nightly sync should re-enqueue the incomplete backfill"


async def test_key_events_visibility_toggle(client_for) -> None:
    """The per-client toggle hides GA4 key events / conversions from the panel, tab and overview
    server-side while other metrics stay, records the flip on the client's trail, and round-trips
    (#134). Default is on, so an untouched client behaves exactly as before."""
    t = await make_tenant("mktg-keyevents")
    headers = await auth_cookie(t.user)
    today = date.today()

    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "KeyEvents BV"}, headers=headers)
        ).json()
        ga4 = (
            await c.post(
                "/api/v1/marketing/links",
                json={
                    "company_id": company["id"],
                    "source": "ga4",
                    "external_id": "properties/42",
                    "display_name": "KeyEvents — GA4",
                },
                headers=headers,
            )
        ).json()
        await _seed_metrics(
            t.org.id,
            uuid.UUID(ga4["id"]),
            {today - timedelta(days=2): {"sessions": 120, "keyEvents": 10, "conversions": 10}},
        )
        await _mark_synced(t.org.id, uuid.UUID(ga4["id"]))

        metrics_url = f"/api/v1/marketing/companies/{company['id']}/metrics"
        overview_url = "/api/v1/marketing/overview"

        # Default on: key events + conversions are visible on the client and in the grid.
        body = (await c.get(metrics_url, params={"range_days": 30}, headers=headers)).json()
        assert body["show_key_events"] is True
        ga4_src = next(s for s in body["sources"] if s["source"] == "ga4")
        assert ga4_src["kpis"]["keyEvents"]["current"] == 10
        assert ga4_src["kpis"]["conversions"]["current"] == 10
        assert "keyEvents" in ga4_src["series"]["metrics"]

        ov = (await c.get(overview_url, params={"range_days": 30}, headers=headers)).json()
        row = ov["rows"][0]
        assert row["show_key_events"] is True
        assert row["metrics"]["conversions"]["current"] == 10

        # Turn it off for this client.
        put = await c.put(
            f"/api/v1/marketing/companies/{company['id']}/settings",
            json={"show_key_events": False},
            headers=headers,
        )
        assert put.status_code == 200, put.text
        assert put.json()["company_id"] == company["id"]
        assert put.json()["show_key_events"] is False

        # The panel/tab payload now omits GA4 key events + conversions, but keeps sessions.
        body = (await c.get(metrics_url, params={"range_days": 30}, headers=headers)).json()
        assert body["show_key_events"] is False
        ga4_src = next(s for s in body["sources"] if s["source"] == "ga4")
        assert "keyEvents" not in ga4_src["kpis"]
        assert "conversions" not in ga4_src["kpis"]
        assert "keyEvents" not in ga4_src["series"]["metrics"]
        assert ga4_src["kpis"]["sessions"]["current"] == 120

        # The overview drops the conversions cell for this client; sessions stays.
        ov = (await c.get(overview_url, params={"range_days": 30}, headers=headers)).json()
        row = ov["rows"][0]
        assert row["show_key_events"] is False
        assert "conversions" not in row["metrics"]
        assert row["metrics"]["sessions"]["current"] == 120

        # The flip is on the client's activity trail.
        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            actions = (
                (
                    await session.execute(
                        select(ActivityLog.action).where(
                            ActivityLog.entity_type == "company",
                            ActivityLog.entity_id == uuid.UUID(company["id"]),
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert "marketing.key_events_disabled" in actions

        # Toggling back on restores the metric everywhere.
        assert (
            await c.put(
                f"/api/v1/marketing/companies/{company['id']}/settings",
                json={"show_key_events": True},
                headers=headers,
            )
        ).status_code == 200
        body = (await c.get(metrics_url, params={"range_days": 30}, headers=headers)).json()
        ga4_src = next(s for s in body["sources"] if s["source"] == "ga4")
        assert ga4_src["kpis"]["conversions"]["current"] == 10


async def test_key_events_toggle_tenant_isolation(client_for) -> None:
    """Org B cannot flip the key-events toggle on org A's company — it 404s, never leaking that
    the company exists (#134, Golden Rule 1)."""
    a = await make_tenant("mktg-ke-a")
    b = await make_tenant("mktg-ke-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)

    async with client_for(a.host) as ca:
        company = (
            await ca.post("/api/v1/companies", json={"name": "Iso BV"}, headers=a_headers)
        ).json()

    async with client_for(b.host) as cb:
        leaked = await cb.put(
            f"/api/v1/marketing/companies/{company['id']}/settings",
            json={"show_key_events": False},
            headers=b_headers,
        )
        assert leaked.status_code == 404


async def test_ads_developer_token_stored_encrypted_not_env(client_for) -> None:
    """The Google Ads developer token lives in per-org settings, encrypted, write-only — never an
    env var and never played back — and the Ads adapter reads it via the token scope (#134)."""
    t = await make_tenant("mktg-adstoken")
    headers = await auth_cookie(t.user)
    token = "dev-token-abc123"

    async with client_for(t.host) as c:
        # Not configured until set (no env token in the test process).
        before = (await c.get("/api/v1/marketing/settings", headers=headers)).json()
        assert before["ads_developer_token_configured"] is False
        assert before["env_ads_token_configured"] is False

        put = await c.put(
            "/api/v1/marketing/settings", json={"ads_developer_token": token}, headers=headers
        )
        assert put.status_code == 200, put.text
        body = put.json()
        assert body["ads_developer_token_configured"] is True
        # The secret is write-only — the response never carries the value back.
        assert "ads_developer_token" not in body

        # An omitted token keeps the stored one (the Google-client-secret rule).
        kept = (
            await c.put("/api/v1/marketing/settings", json={}, headers=headers)
        ).json()
        assert kept["ads_developer_token_configured"] is True

    # Stored encrypted at rest, and the shared resolver decrypts it per org.
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        row = await session.scalar(
            select(MarketingSettings).where(MarketingSettings.org_id == t.org.id)
        )
        assert row.ads_developer_token_encrypted not in (None, token)  # not plaintext
        assert decrypt(row.ads_developer_token_encrypted) == token
        assert await resolve_ads_developer_token(session, t.org.id) == token

    # The stateless Ads adapter reads the bound per-org token inside the scope, and falls back to
    # "not configured" (no env token here) outside it.
    with gads.developer_token_scope(token):
        assert gads._developer_token() == token
    with pytest.raises(gads.AdsNotConfigured):
        gads._developer_token()


async def test_ads_developer_token_tenant_isolation(client_for) -> None:
    """One org's Ads token is invisible to another — settings are org-scoped like every table."""
    a = await make_tenant("mktg-token-a")
    b = await make_tenant("mktg-token-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)

    async with client_for(a.host) as ca:
        await ca.put(
            "/api/v1/marketing/settings",
            json={"ads_developer_token": "a-only-token"},
            headers=a_headers,
        )

    async with client_for(b.host) as cb:
        b_settings = (await cb.get("/api/v1/marketing/settings", headers=b_headers)).json()
        assert b_settings["ads_developer_token_configured"] is False

    async with async_session_maker() as session:
        await set_current_org(session, b.org.id)
        assert await resolve_ads_developer_token(session, b.org.id) is None
