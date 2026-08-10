"""Marketing module (epic #134): link round-trip, tenant isolation, stored-metric aggregation.

The Google-facing paths (pickers, drill-downs, the nightly sync) can't be exercised without a
live Google, so this covers what does not need one: the link CRUD, RLS isolation on both tables,
and — the load-bearing logic — the daily-aggregate reads that power the panel/tab/overview,
seeded directly into ``marketing_metrics_daily``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from pwdlib import PasswordHash
from sqlalchemy import select

from app.core.activity.models import ActivityLog
from app.core.auth.models import User
from app.core.crypto import decrypt, encrypt
from app.db import async_session_maker, set_current_org
from app.modules.google.client import describe_api_error
from app.modules.google.models import GoogleConnection, GoogleSettings
from app.modules.google.oauth import SCOPE_ANALYTICS
from app.modules.marketing.layout import SourceLayout, resolve_event_label
from app.modules.marketing.models import (
    MarketingLink,
    MarketingMetricDaily,
    MarketingSettings,
)
from app.modules.marketing.service import _failure_key, resolve_ads_developer_token
from app.modules.marketing.sources import gads
from app.modules.marketing.sources.ga4 import GA4Adapter
from tests.conftest import add_membership, auth_cookie, make_tenant

_ph = PasswordHash.recommended()


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:  # noqa: ARG002
        self.store[key] = value


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
        # Current window (safely inside [today-30, today-1]) against the same days a year
        # earlier — the default comparison since #312. The comparison the dashboard *used* to
        # make lives in tests/test_marketing_compare.py, where it is a per-client setting.
        await _seed_metrics(
            t.org.id,
            link_id,
            {
                today - timedelta(days=2): {"sessions": 120, "conversions": 10},
                today - timedelta(days=367): {"sessions": 80, "conversions": 5},
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


async def _link(c, headers, company_id: str, source: str, external_id: str) -> dict:
    res = await c.post(
        "/api/v1/marketing/links",
        json={
            "company_id": company_id,
            "source": source,
            "external_id": external_id,
            "display_name": external_id,
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()


async def test_summary_top_clients_headline_fallback_and_cap(client_for) -> None:
    """The My Day digest (#254): one headline KPI per linked client — GA4 sessions, else GSC
    clicks — sorted on the current value, capped but honest about the cap, and per-client
    curation (#192) withholds a hidden tile here exactly like the panel/tab."""
    t = await make_tenant("mktg-summary")
    headers = await auth_cookie(t.user)
    today = date.today()

    async with client_for(t.host) as c:
        ga4_co = (
            await c.post("/api/v1/companies", json={"name": "Sessies BV"}, headers=headers)
        ).json()
        gsc_co = (
            await c.post("/api/v1/companies", json={"name": "Klikken BV"}, headers=headers)
        ).json()
        ads_co = (
            await c.post("/api/v1/companies", json={"name": "AdsOnly BV"}, headers=headers)
        ).json()

        ga4 = await _link(c, headers, ga4_co["id"], "ga4", "properties/11")
        gsc = await _link(c, headers, gsc_co["id"], "gsc", "sc-domain:klikken.nl")
        await _link(c, headers, ads_co["id"], "gads", "customers/123")

        await _seed_metrics(
            t.org.id,
            uuid.UUID(ga4["id"]),
            {
                today - timedelta(days=2): {"sessions": 120},
                # A year back: the widget's delta follows the org default comparison (#312).
                today - timedelta(days=367): {"sessions": 80},
            },
        )
        await _seed_metrics(
            t.org.id,
            uuid.UUID(gsc["id"]),
            {today - timedelta(days=2): {"clicks": 300, "impressions": 1000}},
        )

        body = (
            await c.get("/api/v1/marketing/summary", params={"range_days": 30}, headers=headers)
        ).json()
        # The Ads-only client feeds neither headline: counted, never listed.
        assert body["linked_total"] == 3
        assert [r["company_name"] for r in body["rows"]] == ["Klikken BV", "Sessies BV"]
        clicks_row, sessions_row = body["rows"]
        assert clicks_row["metric"] == "clicks"
        assert clicks_row["kpi"]["current"] == 300
        assert sessions_row["metric"] == "sessions"
        assert sessions_row["kpi"]["current"] == 120
        assert sessions_row["kpi"]["delta_pct"] == 50.0

        # The cap truncates rows but never the count the widget's "top n of this" prints.
        capped = (
            await c.get("/api/v1/marketing/summary", params={"limit": 1}, headers=headers)
        ).json()
        assert [r["company_name"] for r in capped["rows"]] == ["Klikken BV"]
        assert capped["linked_total"] == 3

        # Curating sessions away (#192) drops that client's headline from the digest too.
        hidden = await c.put(
            f"/api/v1/marketing/companies/{ga4_co['id']}/settings",
            json={"layout": {"sources": {"ga4": {"tiles": ["conversions"]}}}},
            headers=headers,
        )
        assert hidden.status_code == 200, hidden.text
        curated = (await c.get("/api/v1/marketing/summary", headers=headers)).json()
        assert [r["company_name"] for r in curated["rows"]] == ["Klikken BV"]
        assert curated["linked_total"] == 3


async def test_summary_is_horizon_scoped_for_portal_logins(client_for) -> None:
    """The summary rides ``marketing.metrics.read``, which the portal ``client`` role holds
    (#193) — so it must honour the company horizon (#191): a contact-linked login gets only
    its own companies' rows, while staff read the whole book."""
    from app.core.auth.models import User as AuthUser

    t = await make_tenant("mktg-sum-portal")
    headers = await auth_cookie(t.user)
    today = date.today()

    async with client_for(t.host) as c:
        mine = (
            await c.post("/api/v1/companies", json={"name": "Mijn BV"}, headers=headers)
        ).json()
        other = (
            await c.post("/api/v1/companies", json={"name": "Andermans BV"}, headers=headers)
        ).json()
        mine_link = await _link(c, headers, mine["id"], "ga4", "properties/21")
        other_link = await _link(c, headers, other["id"], "ga4", "properties/22")
        for link in (mine_link, other_link):
            await _seed_metrics(
                t.org.id,
                uuid.UUID(link["id"]),
                {today - timedelta(days=2): {"sessions": 50}},
            )

        contact = (
            await c.post(
                "/api/v1/contacts",
                json={
                    "first_name": "Piet",
                    "last_name": "Klant",
                    "email": "piet-mktg-sum@example.com",
                    "company_ids": [mine["id"]],
                },
                headers=headers,
            )
        ).json()
        assert (
            await c.post(f"/api/v1/portal/logins/contact/{contact['id']}", headers=headers)
        ).status_code == 200
        async with async_session_maker() as session:
            portal_user = await session.scalar(
                select(AuthUser).where(AuthUser.email == contact["email"])
            )
        portal_headers = await auth_cookie(portal_user)

        staff = (await c.get("/api/v1/marketing/summary", headers=headers)).json()
        assert staff["linked_total"] == 2
        assert {r["company_name"] for r in staff["rows"]} == {"Mijn BV", "Andermans BV"}

        portal = (await c.get("/api/v1/marketing/summary", headers=portal_headers)).json()
        assert portal["linked_total"] == 1
        assert [r["company_name"] for r in portal["rows"]] == ["Mijn BV"]

    # Tenant isolation: a fresh org's digest is empty, whatever its neighbours link.
    o = await make_tenant("mktg-sum-other")
    o_headers = await auth_cookie(o.user)
    async with client_for(o.host) as co:
        empty = (await co.get("/api/v1/marketing/summary", headers=o_headers)).json()
        assert empty["linked_total"] == 0
        assert empty["rows"] == []


# --- what Google actually said (a 403 is not one failure) ------------------------------------ #
def _http_403(body: dict) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://analyticsadmin.googleapis.com/v1beta/accountSummaries")
    response = httpx.Response(403, json=body, request=request)
    return httpx.HTTPStatusError("Client error '403 Forbidden'", request=request, response=response)


_SERVICE_DISABLED = {
    "error": {
        "code": 403,
        "status": "PERMISSION_DENIED",
        "message": (
            "Google Analytics Admin API has not been used in project 123456789012 before or "
            "it is disabled."
        ),
        "details": [
            {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "SERVICE_DISABLED"}
        ],
    }
}


def test_describe_api_error_reads_googles_own_reason() -> None:
    """``str(exc)`` is the status line and the URL; the diagnosis is in the body."""
    disabled = describe_api_error(_http_403(_SERVICE_DISABLED))
    assert disabled is not None
    assert disabled.api_disabled and not disabled.scope_insufficient
    assert disabled.status == "PERMISSION_DENIED"
    assert "123456789012" in str(disabled)  # the Cloud project, the whole point of logging it

    scoped = describe_api_error(
        _http_403(
            {
                "error": {
                    "code": 403,
                    "message": "Request had insufficient authentication scopes.",
                    "details": [{"reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}],
                }
            }
        )
    )
    assert scoped is not None and scoped.scope_insufficient and not scoped.api_disabled

    # The older Google JSON shape, and a plain 403 that explains nothing.
    legacy = describe_api_error(
        _http_403({"error": {"code": 403, "errors": [{"reason": "accessNotConfigured"}]}})
    )
    assert legacy is not None and legacy.api_disabled
    bare = describe_api_error(_http_403({"error": {"code": 403, "message": "Forbidden"}}))
    assert bare is not None and not bare.api_disabled and not bare.scope_insufficient
    assert describe_api_error(RuntimeError("not http at all")) is None


def test_failure_key_routes_each_403_to_its_own_cure() -> None:
    """The three failure paths share one classifier, so the nightly sync and the picker can
    never disagree about what a given 403 means."""
    disabled = describe_api_error(_http_403(_SERVICE_DISABLED))
    scoped = describe_api_error(
        _http_403(
            {
                "error": {
                    "code": 403,
                    "message": "Request had insufficient authentication scopes.",
                    "details": [{"reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}],
                }
            }
        )
    )
    assert _failure_key(disabled, "fallback") == "marketing.api_not_enabled"
    assert _failure_key(scoped, "fallback") == "marketing.scope_insufficient"
    # A cause Google did not name keeps the caller's fallback — for the sync that is Google's
    # own sentence, which beats the status line it used to store.
    bare = describe_api_error(_http_403({"error": {"code": 403, "message": "Forbidden"}}))
    assert _failure_key(bare, "403 : Forbidden") == "403 : Forbidden"
    assert _failure_key(None, "marketing.accounts_error") == "marketing.accounts_error"


async def _seed_google(tenant, user_id: uuid.UUID) -> None:
    """An org OAuth client plus an active connection carrying the GA4 scope."""
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        session.add(
            GoogleSettings(
                org_id=tenant.org.id,
                client_id="123456789012-abc.apps.googleusercontent.com",
                client_secret_encrypted=encrypt("client-secret"),
            )
        )
        session.add(
            GoogleConnection(
                org_id=tenant.org.id,
                user_id=user_id,
                google_sub="sub-mktg",
                email="marketeer@agency.nl",
                scopes=["openid", "email", SCOPE_ANALYTICS],
                refresh_token_encrypted=encrypt("refresh-token-plain"),
            )
        )
        await session.commit()


_SCOPE_INSUFFICIENT = {
    "error": {
        "code": 403,
        "message": "Request had insufficient authentication scopes.",
        "details": [{"reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}],
    }
}


@pytest.mark.parametrize(
    ("body", "expected", "has_scope"),
    [
        (_SERVICE_DISABLED, "marketing.api_not_enabled", True),
        # Google saying the token lacks the scope is exactly what ``has_scope=False`` means.
        (_SCOPE_INSUFFICIENT, "marketing.scope_insufficient", False),
        ({"error": {"code": 403, "message": "Forbidden"}}, "marketing.accounts_error", True),
    ],
)
async def test_accounts_picker_separates_a_disabled_api_from_a_bad_token(
    client_for,
    monkeypatch: pytest.MonkeyPatch,
    body: dict,
    expected: str,
    has_scope: bool,
) -> None:
    """A disabled Cloud API and a dead grant both 403; only one is cured by reconnecting, so
    the picker must not answer "try reconnecting" to both (the GA4 symptom this came from)."""
    t = await make_tenant("mktg-accounts-403")
    headers = await auth_cookie(t.user)
    await _seed_google(t, t.user.id)

    fake_redis = _FakeRedis()
    monkeypatch.setattr("app.modules.marketing.service.get_redis", lambda: fake_redis)

    async def _boom(self, client):  # noqa: ANN001, ARG001
        raise _http_403(body)

    monkeypatch.setattr(GA4Adapter, "list_accounts", _boom)

    async with client_for(t.host) as c:
        res = await c.get("/api/v1/marketing/accounts?source=ga4", headers=headers)
    assert res.status_code == 200
    payload = res.json()
    assert payload["connected"] is True and payload["has_scope"] is has_scope
    assert payload["error"] == expected
    assert payload["accounts"] == []
    assert fake_redis.store == {}  # a failure is never cached as "this account list is empty"


async def test_link_names_whose_google_connection_syncs_it(client_for) -> None:
    """Every marketing surface says *through whom* a client's numbers arrive.

    A link rides one colleague's grant. Without the owner on the payload a second employee sees
    a working link with no hint that it is not theirs — so they connect Google again for data
    that is already flowing, and nobody learns that the link dies when that person leaves.
    """
    t = await make_tenant("mktg-owner")
    headers = await auth_cookie(t.user)
    await _seed_google(t, t.user.id)
    colleague = await _add_member(t.org.id, "collega@agency.nl", role="admin")
    colleague_headers = await auth_cookie(colleague)

    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Acme BV"}, headers=headers)
        ).json()
        created = await c.post(
            "/api/v1/marketing/links",
            json={
                "company_id": company["id"],
                "source": "ga4",
                "external_id": "properties/1",
                "display_name": "Acme GA4",
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        # The creator sees their own grant named as theirs, straight off the create.
        mine = created.json()["connection_owner"]
        assert mine["email"] == "marketeer@agency.nl" and mine["is_me"] is True

        # The colleague sees the same link attributed to the person who connected it.
        listed = await c.get(
            f"/api/v1/marketing/links?company_id={company['id']}", headers=colleague_headers
        )
        owner = listed.json()[0]["connection_owner"]
        assert owner["is_me"] is False
        assert owner["email"] == "marketeer@agency.nl"  # the Google account
        assert owner["name"] == t.user.email  # the colleague, named (no full_name set)
        assert owner["user_id"] == str(t.user.id)

        # …and the panel/tab payload carries it too, so it shows without a second call.
        metrics = await c.get(
            f"/api/v1/marketing/companies/{company['id']}/metrics",
            headers=colleague_headers,
        )
        assert metrics.json()["sources"][0]["connection_owner"]["is_me"] is False


async def test_picker_tells_a_colleague_who_already_connected(client_for) -> None:
    """The picker only ever saw the *caller's* grant, so the second person in the agency was
    told "not connected" about accounts their colleague had linked minutes earlier."""
    t = await make_tenant("mktg-connected-via")
    await _seed_google(t, t.user.id)
    colleague = await _add_member(t.org.id, "tweede@agency.nl", role="admin")
    colleague_headers = await auth_cookie(colleague)

    async with client_for(t.host) as c:
        res = await c.get("/api/v1/marketing/accounts?source=ga4", headers=colleague_headers)
    assert res.status_code == 200
    payload = res.json()
    assert payload["connected"] is False  # they still need their own to pick accounts
    assert [o["email"] for o in payload["connected_via"]] == ["marketeer@agency.nl"]
    assert payload["connected_via"][0]["is_me"] is False

    # Search Console is not on that connection, so nobody is offered for it.
    async with client_for(t.host) as c:
        gsc = await c.get("/api/v1/marketing/accounts?source=gsc", headers=colleague_headers)
    assert gsc.json()["connected_via"] == []


async def test_own_connection_is_never_listed_as_a_colleague(client_for) -> None:
    t = await make_tenant("mktg-connected-self")
    headers = await auth_cookie(t.user)
    await _seed_google(t, t.user.id)
    async with client_for(t.host) as c:
        # The GA4 scope is on this connection but Search Console is not: the caller's own grant
        # must never come back as "someone else already connected".
        res = await c.get("/api/v1/marketing/accounts?source=gsc", headers=headers)
    assert res.json()["connected_via"] == []


def test_ads_api_version_is_not_a_sunset_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """Google sunsets an Ads API version about a year after release and then 404s every path
    under it — which no picker state describes, so the module just looks broken. The version is
    a setting for exactly that reason; this pins that it is honoured, and that we never ship
    one already known dead."""
    from app.config import settings as app_settings

    assert gads.api_base() == f"{gads.API_HOST}/{gads.DEFAULT_API_VERSION}"
    # v18 sunset on 2025-08-20; anything at or below it is a guaranteed 404.
    assert int(gads.DEFAULT_API_VERSION.lstrip("v")) > 18

    monkeypatch.setattr(app_settings, "google_ads_api_version", "v26")
    assert gads.api_base().endswith("/v26")
    monkeypatch.setattr(app_settings, "google_ads_api_version", "")
    assert gads.api_base().endswith(f"/{gads.DEFAULT_API_VERSION}")


async def test_link_owner_lookup_does_not_scale_with_the_links(client_for, count_queries) -> None:
    """Naming the connection's owner stays one joined read, whatever a client has linked.

    The obvious implementation — resolve the user per link — is invisible in the JSON: three
    links and thirty return the same payload, and only the statement count tells them apart
    (docs/PERFORMANCE.md). So the count is what this pins, not the names.
    """
    t = await make_tenant("mktg-owner-budget")
    headers = await auth_cookie(t.user)
    await _seed_google(t, t.user.id)

    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Acme BV"}, headers=headers)
        ).json()
        for source, external in (("ga4", "properties/1"), ("gsc", "sc-domain:a.nl")):
            created = await c.post(
                "/api/v1/marketing/links",
                json={
                    "company_id": company["id"],
                    "source": source,
                    "external_id": external,
                    "display_name": external,
                },
                headers=headers,
            )
            assert created.status_code == 201, created.text

        with count_queries() as counter:
            listed = await c.get(
                f"/api/v1/marketing/links?company_id={company['id']}", headers=headers
            )
        assert listed.status_code == 200, listed.text
        assert len(listed.json()) == 2
        assert all(row["connection_owner"]["is_me"] for row in listed.json())
        # One read of the connections + their owners, not one per link — and `users` is only
        # ever touched by that join.
        assert len(counter.matching("from google_connections")) == 1
        assert len(counter.matching(" join users")) == 1


def test_a_sunset_ads_api_version_is_named_not_shrugged_at() -> None:
    """A dead Ads API version 404s every call with a perfectly good token — the exact failure
    that had to be diagnosed from a container log. Ads gets its own message; the other sources
    keep theirs, because a 404 there means the property is gone, not the API."""
    from app.modules.marketing.models import MarketingSource

    request = httpx.Request(
        "GET", "https://googleads.googleapis.com/v18/customers:listAccessibleCustomers"
    )
    response = httpx.Response(
        404, json={"error": {"code": 404, "status": "NOT_FOUND", "message": "Not found."}},
        request=request,
    )
    gone = describe_api_error(
        httpx.HTTPStatusError("Client error '404 Not Found'", request=request, response=response)
    )
    assert (
        _failure_key(gone, "marketing.accounts_error", source=MarketingSource.GADS.value)
        == "marketing.ads_api_version"
    )
    assert (
        _failure_key(gone, "marketing.accounts_error", source=MarketingSource.GA4.value)
        == "marketing.accounts_error"
    )
    # And a 403 keeps its own classification whichever source it came from.
    disabled = describe_api_error(_http_403(_SERVICE_DISABLED))
    assert (
        _failure_key(disabled, "fallback", source=MarketingSource.GADS.value)
        == "marketing.api_not_enabled"
    )


class _FakeAdsClient:
    """A stand-in for the Ads REST client that records what was asked, and with which headers.

    The headers are the point: ``login-customer-id`` is the whole of MCC support, and it is
    invisible in every response body.
    """

    def __init__(self, responses: dict[str, dict]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict, dict]] = []

    def _answer(self, url: str, headers: dict, body: dict | None = None):
        self.calls.append((url, dict(headers), dict(body or {})))
        for needle, payload in self._responses.items():
            if needle in url and (
                not isinstance(payload, dict)
                or "_query" not in payload
                or payload["_query"] in str((body or {}).get("query", ""))
            ):
                return httpx.Response(
                    200,
                    json={k: v for k, v in payload.items() if k != "_query"},
                    request=httpx.Request("GET", url),
                )
        return httpx.Response(200, json={}, request=httpx.Request("GET", url))

    async def get(self, url: str, headers: dict | None = None):
        return self._answer(url, headers or {})

    async def post(self, url: str, headers: dict | None = None, json: dict | None = None):
        return self._answer(url, headers or {}, json)


async def test_manager_accounts_expand_into_their_clients() -> None:
    """An agency's Google user is granted the **manager**, not the clients under it.

    ``listAccessibleCustomers`` answers direct grants only, so the raw list is one MCC id and a
    picker built on it offers nothing an agency actually runs. Each child must come back tagged
    with the manager to reach it through, or every later call is made by a user with no grant
    on that account.
    """
    client = _FakeAdsClient(
        {
            "customers:listAccessibleCustomers": {"resourceNames": ["customers/1112223333"]},
            "customers/1112223333/googleAds:search": {
                "_query": "FROM customer\b",
            },
        }
    )
    # Two answers off the same URL: "is this a manager?" and "list its children".
    manager_meta = {
        "results": [
            {
                "customer": {
                    "descriptiveName": "Bureau MCC",
                    "currencyCode": "EUR",
                    "manager": True,
                }
            }
        ]
    }
    children = {
        "results": [
            {
                "customerClient": {
                    "id": "4445556666",
                    "descriptiveName": "Acme BV",
                    "currencyCode": "EUR",
                    "level": "1",
                }
            },
            {
                "customerClient": {
                    "id": "7778889999",
                    "descriptiveName": "Bakkerij Jansen",
                    "currencyCode": "EUR",
                    "level": "2",
                }
            },
        ]
    }

    calls: list[dict] = []

    async def _post(url: str, headers: dict | None = None, json: dict | None = None):  # noqa: A002
        query = str((json or {}).get("query", ""))
        calls.append({"url": url, "headers": dict(headers or {}), "query": query})
        body = children if "customer_client" in query else manager_meta
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))

    client.post = _post  # type: ignore[method-assign]

    with gads.developer_token_scope("dev-token"):
        options = await gads.GAdsAdapter().list_accounts(client)  # type: ignore[arg-type]

    assert [o.external_id for o in options] == ["4445556666", "7778889999"]
    assert [o.display_name for o in options] == ["Acme BV", "Bakkerij Jansen"]
    # The manager itself is never offered — Google refuses metric queries against one, so a link
    # to it would error forever rather than roll its clients up.
    assert all(o.external_id != "1112223333" for o in options)
    # Every child carries the manager it must be reached through, and the hint names it.
    assert all(o.config["manager_id"] == "1112223333" for o in options)
    assert options[0].account_hint == "4445556666 · Bureau MCC"
    # The hierarchy query is one call for the whole tree — not one per level, not one per child.
    hierarchy = [c for c in calls if "customer_client" in c["query"]]
    assert len(hierarchy) == 1
    assert hierarchy[0]["headers"]["login-customer-id"] == "1112223333"


async def test_a_plain_advertiser_account_is_still_offered_untagged() -> None:
    """Not every install has an MCC; a directly-granted account must not grow a manager id."""
    client = _FakeAdsClient(
        {"customers:listAccessibleCustomers": {"resourceNames": ["customers/5550001111"]}}
    )

    async def _post(url: str, headers: dict | None = None, json: dict | None = None):  # noqa: A002, ARG001
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "customer": {
                            "descriptiveName": "Directe klant",
                            "currencyCode": "EUR",
                            "manager": False,
                        }
                    }
                ]
            },
            request=httpx.Request("POST", url),
        )

    client.post = _post  # type: ignore[method-assign]
    with gads.developer_token_scope("dev-token"):
        options = await gads.GAdsAdapter().list_accounts(client)  # type: ignore[arg-type]

    assert [o.external_id for o in options] == ["5550001111"]
    assert "manager_id" not in options[0].config


def test_a_linked_child_sends_the_manager_on_every_call() -> None:
    """The tag is only worth storing if the header is actually built from it."""
    with gads.developer_token_scope("dev-token"):
        headers = gads._headers({"manager_id": "111-222-3333", "currency": "EUR"})
    # Google wants the id without dashes.
    assert headers["login-customer-id"] == "1112223333"
    assert headers["developer-token"] == "dev-token"

    with gads.developer_token_scope("dev-token"):
        plain = gads._headers({"currency": "EUR"})
    assert "login-customer-id" not in plain
