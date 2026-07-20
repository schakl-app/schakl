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
from pwdlib import PasswordHash
from sqlalchemy import select

from app.core.activity.models import ActivityLog
from app.core.auth.models import User
from app.core.crypto import decrypt
from app.db import async_session_maker, set_current_org
from app.modules.marketing.layout import SourceLayout, resolve_event_label
from app.modules.marketing.models import (
    MarketingLink,
    MarketingMetricDaily,
    MarketingSettings,
)
from app.modules.marketing.service import resolve_ads_developer_token
from app.modules.marketing.sources import gads
from app.modules.marketing.sources.ga4 import GA4Adapter
from tests.conftest import add_membership, auth_cookie, make_tenant

_ph = PasswordHash.recommended()


async def _add_member(org_id, email: str, role: str = "member") -> User:
    """A second employee on the same org (a member: reads marketing but cannot manage links)."""
    async with async_session_maker() as session:
        user = User(
            id=uuid.uuid4(),
            email=email,
            hashed_password=_ph.hash("secret1234"),
            is_active=True,
            is_verified=True,
        )
        session.add(user)
        await session.flush()
        await set_current_org(session, org_id)
        await add_membership(session, org_id, user.id, role)
        await session.commit()
        return User(id=user.id, email=user.email, hashed_password="", is_active=True)


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


class _FakeGA4Response:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeGA4Client:
    """Stands in for the OAuth httpx client; records the runReport body it was sent."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.last_body: dict | None = None

    async def post(self, url: str, json: dict | None = None):  # noqa: ANN201, ARG002
        self.last_body = json
        return _FakeGA4Response(self._payload)


async def test_ga4_key_events_drilldown_lists_events_and_drops_zero_rows() -> None:
    """``key_events`` asks the Data API for eventName × keyEvents. Every event comes back and
    non-key events read 0, so the adapter keeps only the real key events — the by-event breakdown
    (contact form, purchase, …) that the tiles' total alone cannot show."""
    payload = {
        "rows": [
            {"dimensionValues": [{"value": "generate_lead"}], "metricValues": [{"value": "12"}]},
            {"dimensionValues": [{"value": "purchase"}], "metricValues": [{"value": "3"}]},
            {"dimensionValues": [{"value": "page_view"}], "metricValues": [{"value": "0"}]},
        ]
    }
    client = _FakeGA4Client(payload)
    table = await GA4Adapter().drilldown(
        client, "properties/42", "key_events", date(2026, 6, 1), date(2026, 6, 30), {}
    )
    assert table.columns == ["keyEvents"]
    assert [(row.label, row.metrics["keyEvents"]) for row in table.rows] == [
        ("generate_lead", 12.0),
        ("purchase", 3.0),
    ]
    assert client.last_body is not None
    assert client.last_body["dimensions"] == [{"name": "eventName"}]
    assert client.last_body["metrics"] == [{"name": "keyEvents"}]


async def test_key_events_drilldown_respects_visibility_gate(client_for) -> None:
    """The by-event drill-down is a valid GA4 kind and obeys the per-client key-events gate:
    with key events hidden the endpoint refuses the kind outright, same as an unknown one."""
    t = await make_tenant("mktg-drillgate")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Drill BV"}, headers=headers)
        ).json()
        ga4 = (
            await c.post(
                "/api/v1/marketing/links",
                json={
                    "company_id": company["id"],
                    "source": "ga4",
                    "external_id": "properties/42",
                    "display_name": "Drill — GA4",
                },
                headers=headers,
            )
        ).json()
        drill_url = f"/api/v1/marketing/companies/{company['id']}/drilldown"
        params = {"link_id": ga4["id"], "kind": "key_events", "range_days": 30}

        # Gate on (the default): the kind passes validation — with no Google connection the
        # response is the labelled unavailable state, never a 422.
        res = await c.get(drill_url, params=params, headers=headers)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["available"] is False
        assert body["unavailable_reason"] == "marketing.disconnected"

        # Gate off: the drill-down no longer exists for this client.
        await c.put(
            f"/api/v1/marketing/companies/{company['id']}/settings",
            json={"show_key_events": False},
            headers=headers,
        )
        res = await c.get(drill_url, params=params, headers=headers)
        assert res.status_code == 422


async def test_layout_roundtrip_orders_hides_and_relabels(client_for) -> None:
    """The per-client layout (#192): tiles reorder and hide server-side, label overrides ride
    the payload, the default charted metric follows the layout, and the drill-down list obeys
    it — panel, tab and overview alike."""
    t = await make_tenant("mktg-layout")
    headers = await auth_cookie(t.user)
    today = date.today()

    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Layout BV"}, headers=headers)
        ).json()
        ga4 = (
            await c.post(
                "/api/v1/marketing/links",
                json={
                    "company_id": company["id"],
                    "source": "ga4",
                    "external_id": "properties/7",
                    "display_name": "Layout — GA4",
                },
                headers=headers,
            )
        ).json()
        await _seed_metrics(
            t.org.id,
            uuid.UUID(ga4["id"]),
            {
                today - timedelta(days=2): {
                    "sessions": 100, "totalUsers": 80, "keyEvents": 9, "conversions": 9,
                }
            },
        )
        await _mark_synced(t.org.id, uuid.UUID(ga4["id"]))

        layout = {
            "sources": {
                "ga4": {
                    "tiles": ["keyEvents", "sessions"],
                    "labels": {
                        "keyEvents": {"nl": "Aanvragen via de website", "en": "Enquiries"}
                    },
                    "drilldowns": ["top_pages", "key_events"],
                    "chart_metric": "keyEvents",
                }
            }
        }
        saved = await c.put(
            f"/api/v1/marketing/companies/{company['id']}/settings",
            json={"layout": layout},
            headers=headers,
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["layout"]["sources"]["ga4"]["tiles"] == ["keyEvents", "sessions"]

        body = (
            await c.get(
                f"/api/v1/marketing/companies/{company['id']}/metrics",
                params={"range_days": 30},
                headers=headers,
            )
        ).json()
        src = next(s for s in body["sources"] if s["source"] == "ga4")
        # Order and visibility are the layout's: two tiles, in the curated order; the hidden
        # metrics are absent from the payload entirely — kpis and series both.
        assert src["tiles"] == ["keyEvents", "sessions"]
        assert set(src["kpis"]) == {"keyEvents", "sessions"}
        assert set(src["series"]["metrics"]) == {"keyEvents", "sessions"}
        assert src["tile_labels"]["keyEvents"]["nl"] == "Aanvragen via de website"
        assert src["primary_metric"] == "keyEvents"
        assert src["drilldowns"] == ["top_pages", "key_events"]
        # The stored layout rides the payload for the editor (manager-only).
        assert body["layout"]["sources"]["ga4"]["chart_metric"] == "keyEvents"

        # The overview grid respects the same layout: sessions visible, conversions hidden.
        ov = (
            await c.get("/api/v1/marketing/overview", params={"range_days": 30}, headers=headers)
        ).json()
        row = next(r for r in ov["rows"] if r["company_id"] == company["id"])
        assert "sessions" in row["metrics"]
        assert "conversions" not in row["metrics"]
        # keyEvents stays visible per the layout, so the grid's toggle reads on.
        assert row["show_key_events"] is True

        # The layout change landed on the client's trail (§16).
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
        assert "marketing.layout_changed" in actions

        # Clearing the layout restores the defaults.
        cleared = await c.put(
            f"/api/v1/marketing/companies/{company['id']}/settings",
            json={"layout": {"sources": {}}},
            headers=headers,
        )
        assert cleared.json()["layout"] is None
        body = (
            await c.get(
                f"/api/v1/marketing/companies/{company['id']}/metrics",
                params={"range_days": 30},
                headers=headers,
            )
        ).json()
        src = next(s for s in body["sources"] if s["source"] == "ga4")
        assert "totalUsers" in src["kpis"]


async def test_layout_validation_rejects_unknown_keys(client_for) -> None:
    t = await make_tenant("mktg-layout-val")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Val BV"}, headers=headers)
        ).json()
        for bad in (
            {"sources": {"nope": {"tiles": []}}},
            {"sources": {"ga4": {"tiles": ["notAMetric"]}}},
            {"sources": {"ga4": {"drilldowns": ["notAKind"]}}},
            {"sources": {"ga4": {"chart_metric": "notAMetric"}}},
            {"sources": {"ga4": {"labels": {"sessions": {"fr": "Sessions"}}}}},
        ):
            res = await c.put(
                f"/api/v1/marketing/companies/{company['id']}/settings",
                json={"layout": bad},
                headers=headers,
            )
            assert res.status_code == 422, bad


async def test_layout_hides_key_events_drilldown_and_toggle_edits_layout(client_for) -> None:
    """A layout without the keyEvents tile 422s the by-event drill-down; the legacy toggle
    keeps working against a curated layout by editing its tiles (#192 expand rules)."""
    t = await make_tenant("mktg-layout-kd")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "KD BV"}, headers=headers)
        ).json()
        ga4 = (
            await c.post(
                "/api/v1/marketing/links",
                json={
                    "company_id": company["id"],
                    "source": "ga4",
                    "external_id": "properties/9",
                    "display_name": "KD — GA4",
                },
                headers=headers,
            )
        ).json()

        # Hide keyEvents via layout; the drill-down goes with it.
        await c.put(
            f"/api/v1/marketing/companies/{company['id']}/settings",
            json={"layout": {"sources": {"ga4": {"tiles": ["sessions"]}}}},
            headers=headers,
        )
        res = await c.get(
            "/api/v1/marketing/companies/" + company["id"] + "/drilldown",
            params={"link_id": ga4["id"], "kind": "key_events", "range_days": 30},
            headers=headers,
        )
        assert res.status_code == 422
        # The settings echo derives the boolean from the tiles.
        body = (
            await c.get(
                f"/api/v1/marketing/companies/{company['id']}/metrics",
                params={"range_days": 30},
                headers=headers,
            )
        ).json()
        assert body["show_key_events"] is False

        # The legacy toggle edits the curated tiles back on.
        toggled = await c.put(
            f"/api/v1/marketing/companies/{company['id']}/settings",
            json={"show_key_events": True},
            headers=headers,
        )
        assert toggled.json()["show_key_events"] is True
        tiles = toggled.json()["layout"]["sources"]["ga4"]["tiles"]
        assert "keyEvents" in tiles and "sessions" in tiles


async def test_layout_tenant_isolation(client_for) -> None:
    a = await make_tenant("mktg-layout-a")
    b = await make_tenant("mktg-layout-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)
    async with client_for(a.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "A BV"}, headers=a_headers)
        ).json()
        await c.put(
            f"/api/v1/marketing/companies/{company['id']}/settings",
            json={"layout": {"sources": {"ga4": {"tiles": ["sessions"]}}}},
            headers=a_headers,
        )
    async with client_for(b.host) as c:
        # B cannot reach A's company settings by id — reads as absent.
        res = await c.put(
            f"/api/v1/marketing/companies/{company['id']}/settings",
            json={"layout": {"sources": {}}},
            headers=b_headers,
        )
        assert res.status_code == 404


async def test_event_labels_roundtrip_and_validation(client_for) -> None:
    """Per-key-event custom labels (#192): a GA4 layout may relabel events keyed on the GA4
    ``eventName``; the labels round-trip on the manager's layout payload. Non-GA4 sources reject
    them, and oversized maps / bad locales / over-long names or labels are 422s like any stray
    layout key."""
    t = await make_tenant("mktg-eventlabels")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Events BV"}, headers=headers)
        ).json()
        settings_url = f"/api/v1/marketing/companies/{company['id']}/settings"

        # Accept: GA4 event labels, keyed on the eventName, per-locale and optional-per-locale.
        good = {
            "sources": {
                "ga4": {
                    "tiles": ["keyEvents", "sessions"],
                    "event_labels": {
                        "generate_lead": {"nl": "Aanvragen via de website", "en": "Enquiries"},
                        "purchase": {"nl": "Aankopen"},
                    },
                }
            }
        }
        saved = await c.put(settings_url, json={"layout": good}, headers=headers)
        assert saved.status_code == 200, saved.text
        stored = saved.json()["layout"]["sources"]["ga4"]["event_labels"]
        assert stored["generate_lead"]["nl"] == "Aanvragen via de website"
        assert stored["purchase"] == {"nl": "Aankopen"}

        # The manager's metrics payload echoes the layout (editor reads event_labels from it).
        body = (
            await c.get(
                f"/api/v1/marketing/companies/{company['id']}/metrics",
                params={"range_days": 30},
                headers=headers,
            )
        ).json()
        echoed = body["layout"]["sources"]["ga4"]["event_labels"]
        assert echoed["generate_lead"]["en"] == "Enquiries"

        # Reject: event labels on a non-GA4 source, a bad locale, an over-long label, an
        # over-long event name, and an oversized map.
        for bad in (
            {"sources": {"gsc": {"event_labels": {"click": {"nl": "Kliks"}}}}},
            {"sources": {"ga4": {"event_labels": {"generate_lead": {"fr": "Prospects"}}}}},
            {"sources": {"ga4": {"event_labels": {"generate_lead": {"nl": "x" * 81}}}}},
            {"sources": {"ga4": {"event_labels": {"e" * 101: {"nl": "Naam"}}}}},
            {
                "sources": {
                    "ga4": {
                        "event_labels": {
                            f"event_{i}": {"nl": "Naam"} for i in range(51)
                        }
                    }
                }
            },
        ):
            res = await c.put(settings_url, json={"layout": bad}, headers=headers)
            assert res.status_code == 422, bad


def test_resolve_event_label_locale_fallback() -> None:
    """The requested locale wins, then the other locale (an override is optional per language);
    a missing/absent event resolves to ``None`` so the caller keeps the raw event name (#192)."""
    src = SourceLayout(
        event_labels={"generate_lead": {"nl": "Aanvraag"}, "purchase": {"en": "Purchase"}}
    )
    assert resolve_event_label(src, "generate_lead", "nl") == "Aanvraag"
    # nl-only label still shows for an en viewer (fallback to the other locale), never the raw id.
    assert resolve_event_label(src, "generate_lead", "en") == "Aanvraag"
    assert resolve_event_label(src, "purchase", "nl") == "Purchase"
    assert resolve_event_label(src, "unknown_event", "nl") is None
    assert resolve_event_label(None, "generate_lead", "nl") is None


async def test_hidden_source_omitted_for_client_kept_for_manager(client_for) -> None:
    """A source marked ``hidden`` (#192) is dropped from the payload for a viewer who cannot
    manage links (the portal/client) but kept — flagged ``hidden`` — for a manager, so edit mode
    can list every linked source and re-enable it."""
    t = await make_tenant("mktg-hidesrc")
    headers = await auth_cookie(t.user)
    member = await _add_member(t.org.id, "member@mktg-hidesrc.test")
    member_headers = await auth_cookie(member)
    today = date.today()

    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Hide BV"}, headers=headers)
        ).json()
        ga4 = (
            await c.post(
                "/api/v1/marketing/links",
                json={
                    "company_id": company["id"],
                    "source": "ga4",
                    "external_id": "properties/55",
                    "display_name": "Hide — GA4",
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
                    "external_id": "sc-domain:hide.nl",
                    "display_name": "hide.nl",
                },
                headers=headers,
            )
        ).json()
        await _seed_metrics(
            t.org.id, uuid.UUID(ga4["id"]), {today - timedelta(days=2): {"sessions": 10}}
        )
        await _seed_metrics(
            t.org.id, uuid.UUID(gsc["id"]), {today - timedelta(days=2): {"clicks": 5}}
        )
        await _mark_synced(t.org.id, uuid.UUID(ga4["id"]))
        await _mark_synced(t.org.id, uuid.UUID(gsc["id"]))

        metrics_url = f"/api/v1/marketing/companies/{company['id']}/metrics"

        # Hide the GSC section entirely.
        put = await c.put(
            f"/api/v1/marketing/companies/{company['id']}/settings",
            json={"layout": {"sources": {"gsc": {"hidden": True}}}},
            headers=headers,
        )
        assert put.status_code == 200, put.text

        # Manager: GSC still present, flagged hidden; GA4 present and visible.
        mgr = (await c.get(metrics_url, params={"range_days": 30}, headers=headers)).json()
        by_source = {s["source"]: s for s in mgr["sources"]}
        assert set(by_source) == {"ga4", "gsc"}
        assert by_source["gsc"]["hidden"] is True
        assert by_source["ga4"]["hidden"] is False

        # Member (marketing.metrics.read but not link.manage): the hidden source is gone.
        cli = (
            await c.get(metrics_url, params={"range_days": 30}, headers=member_headers)
        ).json()
        assert [s["source"] for s in cli["sources"]] == ["ga4"]
        # A member never receives the manager-only layout either.
        assert cli["layout"] is None


async def test_link_attaches_to_client_website(client_for) -> None:
    """A link may attach to one of *this* client's websites; the metrics payload carries the
    website (id + domain name) so panel/tab group per site, and another company's website 404s."""
    a = await make_tenant("mktg-web")
    headers = await auth_cookie(a.user)

    async with client_for(a.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Twee Sites BV"}, headers=headers)
        ).json()
        other = (
            await c.post("/api/v1/companies", json={"name": "Ander BV"}, headers=headers)
        ).json()
        domain = (
            await c.post(
                "/api/v1/domains",
                json={"name": "tweesites.nl", "company_id": company["id"]},
                headers=headers,
            )
        ).json()
        website = (
            await c.post("/api/v1/websites", json={"domain_id": domain["id"]}, headers=headers)
        ).json()
        other_domain = (
            await c.post(
                "/api/v1/domains",
                json={"name": "ander.nl", "company_id": other["id"]},
                headers=headers,
            )
        ).json()
        other_website = (
            await c.post(
                "/api/v1/websites", json={"domain_id": other_domain["id"]}, headers=headers
            )
        ).json()

        created = await c.post(
            "/api/v1/marketing/links",
            json={
                "company_id": company["id"],
                "website_id": website["id"],
                "source": "ga4",
                "external_id": "properties/111",
                "display_name": "Twee Sites — GA4",
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        assert created.json()["website_id"] == website["id"]
        assert created.json()["website_name"] == "tweesites.nl"

        # Another company's website is not a valid attachment point — a non-leaking 404.
        rejected = await c.post(
            "/api/v1/marketing/links",
            json={
                "company_id": company["id"],
                "website_id": other_website["id"],
                "source": "gsc",
                "external_id": "sc-domain:tweesites.nl",
                "display_name": "Twee Sites — GSC",
            },
            headers=headers,
        )
        assert rejected.status_code == 404, rejected.text

        # The metrics payload groups per website: the source carries the website, and the
        # client's website list rides along for the pickers.
        metrics = (
            await c.get(
                f"/api/v1/marketing/companies/{company['id']}/metrics", headers=headers
            )
        ).json()
        assert [w["name"] for w in metrics["websites"]] == ["tweesites.nl"]
        assert metrics["sources"][0]["website_id"] == website["id"]
        assert metrics["sources"][0]["website_name"] == "tweesites.nl"
