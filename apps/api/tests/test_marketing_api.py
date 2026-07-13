"""Marketing module (epic #134): link round-trip, tenant isolation, stored-metric aggregation.

The Google-facing paths (pickers, drill-downs, the nightly sync) can't be exercised without a
live Google, so this covers what does not need one: the link CRUD, RLS isolation on both tables,
and — the load-bearing logic — the daily-aggregate reads that power the panel/tab/overview,
seeded directly into ``marketing_metrics_daily``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from app.db import async_session_maker, set_current_org
from app.modules.marketing.models import MarketingLink, MarketingMetricDaily
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
