"""What a client-facing login is told about a marketing source (#446, #447, #448).

The numbers are the client's; the machinery behind them is the agency's. So a portal login
reads a source under the tenant's own name (or a vendor-free default), with no link into the
supplier's console and no colleague's name beside it — while staff, on the same endpoint, keep
all three. Decided in the service, so the widget, the tab and an MCP client agree.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.core.auth.models import User
from app.db import async_session_maker, set_current_org
from app.modules.marketing.models import MarketingLink, MarketingSource
from app.modules.marketing.service import portal_source_label
from tests.conftest import auth_cookie, make_tenant


class _FakeRedis:
    """The drill-down's cache, in memory: the process-wide client is bound to whichever event
    loop first opened it, which in a long run is not this test's."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:  # noqa: ARG002
        self.store[key] = value


async def _seed_link(org_id, company_id: str, source: str) -> uuid.UUID:
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        link = MarketingLink(
            org_id=org_id,
            company_id=uuid.UUID(company_id),
            source=source,
            external_id=f"{source}-1",
            display_name=f"{source} property",
            config={},
        )
        session.add(link)
        await session.commit()
        return link.id


async def test_portal_reads_a_source_without_its_vendor(client_for, monkeypatch) -> None:
    monkeypatch.setattr("app.modules.marketing.service.get_redis", lambda: _FakeRedis())
    t = await make_tenant("mktg-portal-label")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Klant BV"}, headers=headers)
        ).json()
        contact = (
            await c.post(
                "/api/v1/contacts",
                json={
                    "first_name": "Piet",
                    "last_name": "Klant",
                    "email": "piet-mktg-portal@example.com",
                    "company_ids": [company["id"]],
                },
                headers=headers,
            )
        ).json()
        await _seed_link(t.org.id, company["id"], MarketingSource.SERANKING.value)
        await c.post(f"/api/v1/portal/logins/contact/{contact['id']}", headers=headers)
        async with async_session_maker() as session:
            portal_user = await session.scalar(
                select(User).where(User.email == contact["email"])
            )
        portal_headers = await auth_cookie(portal_user)
        url = f"/api/v1/marketing/companies/{company['id']}/metrics"

        # Staff: the product name (the web prints it), the console link, no override.
        staff = (await c.get(url, headers=headers)).json()["sources"]
        assert len(staff) == 1
        assert staff[0]["label"] is None
        assert "seranking.com" in staff[0]["deep_link"]

        # A client: a vendor-free name, no console link, nobody's Google account named.
        client = (await c.get(url, headers=portal_headers)).json()["sources"]
        assert len(client) == 1
        assert client[0]["deep_link"] == ""
        assert client[0]["connection_owner"] is None
        assert client[0]["label"]
        assert "SE Ranking" not in client[0]["label"]

        # The tenant names it — their product, their word (§2: never a brand in code).
        saved = await c.put(
            "/api/v1/marketing/settings",
            json={"portal_source_labels": {"seranking": "  Bureau Analytics  "}},
            headers=headers,
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["portal_source_labels"] == {"seranking": "Bureau Analytics"}
        client = (await c.get(url, headers=portal_headers)).json()["sources"]
        assert client[0]["label"] == "Bureau Analytics"
        # The tenant's own word is everyone's word: the marketing page prints it as well, so an
        # agency selling "Bureau Analytics" reads the same name on both sides of the portal.
        staff = (await c.get(url, headers=headers)).json()["sources"]
        assert staff[0]["label"] == "Bureau Analytics"

        # The drill-down is the same redaction one level down (#447): no console link, and the
        # reason a table could not be read never names the supplier or tells the client to ask
        # an administrator — staff on the same call keep both.
        link_id = staff[0]["link_id"]
        drill = f"/api/v1/marketing/companies/{company['id']}/drilldown"
        params = {"link_id": link_id, "kind": "keywords", "range_days": 30}
        staff_drill = (await c.get(drill, params=params, headers=headers)).json()
        assert staff_drill["available"] is False
        assert "seranking.com" in staff_drill["deep_link"]
        assert staff_drill["unavailable_reason"] != "marketing.portal_unavailable"
        client_drill = await c.get(drill, params=params, headers=portal_headers)
        assert client_drill.status_code == 200, client_drill.text
        assert client_drill.json()["deep_link"] == ""
        assert client_drill.json()["unavailable_reason"] == "marketing.portal_unavailable"

        # An empty label clears that source back to the default; unknown sources are refused.
        cleared = await c.put(
            "/api/v1/marketing/settings",
            json={"portal_source_labels": {"seranking": ""}},
            headers=headers,
        )
        assert cleared.json()["portal_source_labels"] == {}
        client = (await c.get(url, headers=portal_headers)).json()["sources"]
        assert "SE Ranking" not in client[0]["label"]
        refused = await c.put(
            "/api/v1/marketing/settings",
            json={"portal_source_labels": {"bing": "x"}},
            headers=headers,
        )
        assert refused.status_code == 422


def test_portal_default_label_never_names_a_keyed_vendor() -> None:
    """The resolver's own contract, without a database: every keyed source has a neutral
    default in both shipped locales, and a Google source keeps its product name."""
    for locale in ("nl", "en"):
        labels = {"__locale": locale}
        for source in (MarketingSource.SERANKING.value, MarketingSource.RANKMATH.value):
            label = portal_source_label(source, labels)
            assert label and "SE Ranking" not in label and "Rank Math" not in label
        assert portal_source_label(MarketingSource.GA4.value, labels) == "Google Analytics"
    assert portal_source_label("seranking", {"seranking": "Mine"}) == "Mine"
