"""Connecting a marketing source, and what a client's hub draws (#399, #411).

Both issues are about the *same* control read from three different screens, so they are tested
together. What is asserted is the half no functional test of the endpoints could have caught:
that the question the picker needs answered — which client, then which website — is answerable
from a screen that is not the client's own page, and that removing three cards from the hub did
not remove the one fact one of them carried.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.db import async_session_maker, set_current_org
from app.integrations.google_tag_manager.models import GtmContainer
from app.registry import registry
from tests.conftest import auth_cookie, make_tenant


async def _company_with_website(c, headers, name: str = "Klant BV") -> tuple[str, str, str]:
    company = (await c.post("/api/v1/companies", json={"name": name}, headers=headers)).json()
    domain = (
        await c.post(
            "/api/v1/domains",
            json={"name": "klant.nl", "company_id": company["id"]},
            headers=headers,
        )
    ).json()
    website = (
        await c.post(
            "/api/v1/websites",
            json={"domain_id": domain["id"], "root": True},
            headers=headers,
        )
    ).json()
    return company["id"], website["id"], domain["name"]


async def _link_container(org_id, company_id: str, *, staged: int = 0) -> uuid.UUID:
    """A Tag Manager container attached to a client, written straight in.

    The link path is `test_gtm_api`'s subject; what matters here is only that a linked container
    exists, so this stays a row rather than a trip through the fake transport.
    """
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        row = GtmContainer(
            org_id=org_id,
            account_id="6000000000",
            container_id="900000001",
            public_id="GTM-ABC1234",
            name="klant.nl — web",
            company_id=uuid.UUID(company_id),
            live_version_id="12",
            tag_count=9,
            workspace_changes=staged,
            observed_at=datetime.now(UTC),
        )
        session.add(row)
        await session.commit()
        return row.id


# --- #399: the connect dialog can ask which website ------------------------------------------ #
async def test_a_client_s_websites_are_readable_by_whoever_may_link(client_for) -> None:
    """The site select's own read.

    Away from a client's page nothing on the screen can answer "which website", which is why the
    Rank Math row read *"deze klant heeft nog geen website"* for a client with two. It is gated
    on ``marketing.link.manage`` rather than on ``websites.website.read`` on purpose: the
    question is part of the link the caller is already allowed to make, and requiring the
    websites module's own key would refuse exactly the person put in charge of connecting
    sources (#310, the same shape one module over).
    """
    t = await make_tenant("mktg-sites")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company_id, website_id, domain = await _company_with_website(c, headers)

        response = await c.get(
            f"/api/v1/marketing/companies/{company_id}/websites", headers=headers
        )
        assert response.status_code == 200, response.text
        assert response.json() == [{"id": website_id, "name": domain}]

        # A client with none is an empty list, never an error: "this client has no website" and
        # "we could not ask" are the picker's two different sentences (`MarketingAccountPicker`).
        bare = (
            await c.post("/api/v1/companies", json={"name": "Zonder site"}, headers=headers)
        ).json()
        empty = await c.get(
            f"/api/v1/marketing/companies/{bare['id']}/websites", headers=headers
        )
        assert empty.status_code == 200
        assert empty.json() == []


async def test_another_tenant_s_client_has_no_websites_to_read(client_for) -> None:
    """404, not an empty list: an empty answer would confirm the company exists."""
    a = await make_tenant("mktg-sites-a")
    b = await make_tenant("mktg-sites-b")
    a_headers, b_headers = await auth_cookie(a.user), await auth_cookie(b.user)
    async with client_for(a.host) as ca:
        company_id, _, _ = await _company_with_website(ca, a_headers)
    async with client_for(b.host) as cb:
        response = await cb.get(
            f"/api/v1/marketing/companies/{company_id}/websites", headers=b_headers
        )
        assert response.status_code == 404, response.text


# --- #411: three cards fewer, and the one fact that had to survive --------------------------- #
def test_the_three_integration_cards_are_gone_from_the_company_hub() -> None:
    """Ads, Tag Manager and Timeon no longer draw their own card (#411).

    Asserted against the registry rather than against a rendered page, because the failure mode
    is a `PanelSpec` quietly coming back with a module that gets re-registered — the same shape
    `test_company_panels_have_a_query_budget` guards for cost.
    """
    keys = {
        p.key
        for p in registry.panels_for("company", [m.name for m in registry.all()])
    }
    assert "marketing.overview" in keys
    assert not keys & {"google_ads.company", "google_tag_manager.company", "timeon.company"}


async def test_the_panel_carries_the_staged_changes_the_gtm_card_used_to(client_for) -> None:
    """``workspace_changes`` on the client's page, without opening anything.

    The Tag Manager card was deleted because it printed largely what the marketing panel prints
    one card up — but it carried one number that nothing else did, and a change staged weeks ago
    and never published is how a client's tracking quietly stops being what they were told it is.
    Removing the card without moving the number would have removed the warning.
    """
    t = await make_tenant("mktg-conn")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company_id, _, _ = await _company_with_website(c, headers)
        container_id = await _link_container(t.org.id, company_id, staged=3)

        panels = await c.get(f"/api/v1/companies/{company_id}/panels", headers=headers)
        assert panels.status_code == 200, panels.text
        panel = next(p for p in panels.json() if p["key"] == "marketing.overview")
        connections = panel["data"]["connections"]
        assert [row["id"] for row in connections] == [str(container_id)]
        assert connections[0]["kind"] == "gtm"
        assert connections[0]["external_id"] == "GTM-ABC1234"
        assert connections[0]["pending_changes"] == 3
        assert connections[0]["live_count"] == 9
        # And the panel is not folded away as empty: a client with a container and no metrics
        # source still has something to say, so `empty_when` must not swallow it (#364).
        assert panel.get("empty") is not True


async def test_the_tab_pays_nothing_for_the_connections_row(client_for) -> None:
    """``with_connections`` is opt-in, and the tab does not opt in.

    The same payload feeds the panel, the client's marketing tab, `/marketing` and the portal
    widget. Three of those draw no connections row, and a field that grows a cost for one of its
    four callers is how a screen gets slow one field at a time (docs/PERFORMANCE.md).
    """
    t = await make_tenant("mktg-conn-tab")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company_id, _, _ = await _company_with_website(c, headers)
        await _link_container(t.org.id, company_id, staged=2)

        metrics = await c.get(
            f"/api/v1/marketing/companies/{company_id}/metrics", headers=headers
        )
        assert metrics.status_code == 200, metrics.text
        assert metrics.json()["connections"] == []


async def test_a_container_of_another_tenant_never_reaches_a_panel(client_for) -> None:
    """The seam reads through the owning module's own repository, so RLS and the horizon hold."""
    a = await make_tenant("mktg-conn-a")
    b = await make_tenant("mktg-conn-b")
    a_headers, b_headers = await auth_cookie(a.user), await auth_cookie(b.user)
    async with client_for(a.host) as ca:
        a_company, _, _ = await _company_with_website(ca, a_headers)
        await _link_container(a.org.id, a_company, staged=1)
    async with client_for(b.host) as cb:
        b_company = (
            await cb.post("/api/v1/companies", json={"name": "Andere"}, headers=b_headers)
        ).json()["id"]
        panels = await cb.get(f"/api/v1/companies/{b_company}/panels", headers=b_headers)
        panel = next(p for p in panels.json() if p["key"] == "marketing.overview")
        assert panel["data"]["connections"] == []

    # And nothing crossed in the table either.
    async with async_session_maker() as session:
        await set_current_org(session, b.org.id)
        assert (await session.execute(select(GtmContainer))).scalars().all() == []


async def test_a_keyed_source_is_not_disconnected_for_want_of_a_google_grant(client_for) -> None:
    """SE Ranking and Rank Math have no Google connection, and never will.

    ``_health`` asked every link whether its ``connection_id`` resolved to an active grant, which
    is a question about a credential two of the five sources do not use — so an SE Ranking
    project linked on an install with no Google account at all rendered a red *"De
    Google-verbinding van deze koppeling is weg"* over a link that was working. It is #399's own
    thesis one layer deeper: a Google state deciding the verdict on a source that is not Google.
    """
    t = await make_tenant("mktg-health")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company_id, _, _ = await _company_with_website(c, headers)
        created = await c.post(
            "/api/v1/marketing/links",
            json={
                "company_id": company_id,
                "source": "seranking",
                "external_id": "project-1",
                "display_name": "Klant BV",
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text

        metrics = (
            await c.get(f"/api/v1/marketing/companies/{company_id}/metrics", headers=headers)
        ).json()
        health = {row["source"]: row["health"] for row in metrics["sources"]}
        # "pending" — nothing has synced yet, which is true and actionable. Never
        # "disconnected", which names a credential this source does not have.
        assert health == {"seranking": "pending"}
