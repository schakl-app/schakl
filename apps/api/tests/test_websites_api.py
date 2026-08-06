"""Websites module: 0/1 per domain, hosting link, party, tenant isolation (issue #94)."""

from __future__ import annotations

from tests.conftest import auth_cookie, make_tenant


async def _domain(client, headers, name: str = "example.nl") -> tuple[str, str]:
    company = (
        await client.post("/api/v1/companies", json={"name": "Acme"}, headers=headers)
    ).json()["id"]
    domain = (
        await client.post(
            "/api/v1/domains", json={"name": name, "company_id": company}, headers=headers
        )
    ).json()["id"]
    return company, domain


async def test_website_crud_and_one_per_domain(client_for) -> None:
    t = await make_tenant("web-crud")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, domain = await _domain(c, headers)
        hosting = (
            await c.post("/api/v1/hosting", json={"name": "cluster"}, headers=headers)
        ).json()["id"]

        created = await c.post(
            "/api/v1/websites",
            json={
                "domain_id": domain,
                "root": True,
                "hosting_id": hosting,
                "technical_owner": {"type": "agency"},
                "uptime_enabled": True,
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        website = created.json()
        assert website["domain_name"] == "example.nl"
        assert website["hosting_name"] == "cluster"
        assert website["technical_owner"]["type"] == "agency"

        # At most one website per domain.
        dup = await c.post("/api/v1/websites", json={"domain_id": domain}, headers=headers)
        assert dup.status_code == 409

        # Reachable via the domain filter (renders under its domain).
        listing = await c.get(f"/api/v1/websites?domain_id={domain}", headers=headers)
        assert listing.json()["total"] == 1


async def test_website_tenant_isolation(client_for) -> None:
    a = await make_tenant("web-iso-a")
    b = await make_tenant("web-iso-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)
    async with client_for(a.host) as ca:
        _, domain = await _domain(ca, a_headers, "iso.nl")
        created = await ca.post("/api/v1/websites", json={"domain_id": domain}, headers=a_headers)
        wid = created.json()["id"]
    async with client_for(b.host) as cb:
        assert (await cb.get("/api/v1/websites", headers=b_headers)).json()["total"] == 0
        assert (await cb.get(f"/api/v1/websites/{wid}", headers=b_headers)).status_code == 404
        # B cannot create a website on A's domain either.
        blocked = await cb.post(
            "/api/v1/websites", json={"domain_id": domain}, headers=b_headers
        )
        assert blocked.status_code == 404


async def test_website_company_filter_and_panel(client_for) -> None:
    """Owner request: websites read per client — the ?company_id filter (the websites page's
    deep link) and the company panel that replaced hosting's on the client page."""
    t = await make_tenant("web-company")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company, domain = await _domain(c, headers, "klant.nl")
        created = await c.post("/api/v1/websites", json={"domain_id": domain}, headers=headers)
        assert created.status_code == 201, created.text
        assert created.json()["company_id"] == company
        assert created.json()["company_name"] == "Acme"

        listing = await c.get(f"/api/v1/websites?company_id={company}", headers=headers)
        assert listing.json()["total"] == 1
        other = (
            await c.post("/api/v1/companies", json={"name": "Leeg BV"}, headers=headers)
        ).json()["id"]
        assert (
            await c.get(f"/api/v1/websites?company_id={other}", headers=headers)
        ).json()["total"] == 0

        panels = await c.get(f"/api/v1/companies/{company}/panels", headers=headers)
        websites_panel = next(p for p in panels.json() if p["key"] == "websites.company")
        assert websites_panel["data"]["websites"][0]["name"] == "klant.nl"
        assert websites_panel["data"]["total"] == 1


async def test_website_search_matches_the_domain_it_renders_under(client_for) -> None:
    """A website has no name of its own — the list prints its parent domain's and stores none
    (``natural_keys=("domain",)``), so that is what the search box searches.

    Both halves matter. The match walks the bare-table bridge to ``domains`` rather than any
    column on ``websites``, and it is an ``EXISTS``, so a site is never listed twice; and the
    ``total`` narrows with it, because a count that ignores the filter shows "3" above a list
    of one (docs/PERFORMANCE.md, #285's second failure mode).
    """
    t = await make_tenant("web-search")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        for name in ("winkel.nl", "winkelwagen.be", "heelietsanders.nl"):
            _, domain = await _domain(c, headers, name)
            created = await c.post(
                "/api/v1/websites", json={"domain_id": domain}, headers=headers
            )
            assert created.status_code == 201, created.text

        hit = await c.get("/api/v1/websites", params={"q": "winkel"}, headers=headers)
        assert hit.status_code == 200, hit.text
        assert hit.json()["total"] == 2
        assert sorted(w["domain_name"] for w in hit.json()["items"]) == [
            "winkel.nl",
            "winkelwagen.be",
        ]

        # Case-insensitive, and a miss is an empty page rather than the unfiltered list.
        assert (
            await c.get("/api/v1/websites", params={"q": "WINKELWAGEN"}, headers=headers)
        ).json()["total"] == 1
        assert (
            await c.get("/api/v1/websites", params={"q": "nietbestaand"}, headers=headers)
        ).json()["total"] == 0
        # Blank is not a filter: an empty box shows everything.
        assert (await c.get("/api/v1/websites", params={"q": "  "}, headers=headers)).json()[
            "total"
        ] == 3


async def test_website_list_filters(client_for) -> None:
    """The websites list's own filter bar.

    Two of the three narrow on the *parent domain* — a website has no name of its own and its
    client is the domain's — so they cross the same bare-table bridge the sort does, correlated
    rather than by pulling every one of the client's domain ids into Python first.
    """
    t = await make_tenant("web-filters")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        acme, acme_one = await _domain(c, headers, "acme-shop.nl")
        acme_two = (
            await c.post(
                "/api/v1/domains",
                json={"name": "acme-blog.nl", "company_id": acme},
                headers=headers,
            )
        ).json()["id"]
        bravo = (
            await c.post("/api/v1/companies", json={"name": "Bravo"}, headers=headers)
        ).json()["id"]
        bravo_one = (
            await c.post(
                "/api/v1/domains", json={"name": "bravo.nl", "company_id": bravo}, headers=headers
            )
        ).json()["id"]

        cluster = (
            await c.post("/api/v1/hosting", json={"name": "cluster-a"}, headers=headers)
        ).json()["id"]

        for domain, hosting, uptime in [
            (acme_one, cluster, True),
            (acme_two, None, False),
            (bravo_one, cluster, False),
        ]:
            created = await c.post(
                "/api/v1/websites",
                json={"domain_id": domain, "hosting_id": hosting, "uptime_enabled": uptime},
                headers=headers,
            )
            assert created.status_code == 201, created.text

        async def names(query: str) -> list[str]:
            res = await c.get(f"/api/v1/websites?{query}", headers=headers)
            assert res.status_code == 200, res.text
            body = res.json()
            assert body["total"] == len(body["items"])
            return sorted(w["domain_name"] for w in body["items"])

        assert await names(f"company_id={acme}") == ["acme-blog.nl", "acme-shop.nl"]
        assert await names("q=acme") == ["acme-blog.nl", "acme-shop.nl"]
        assert await names("q=shop") == ["acme-shop.nl"]
        assert await names(f"hosting_id={cluster}") == ["acme-shop.nl", "bravo.nl"]
        assert await names("uptime_enabled=true") == ["acme-shop.nl"]
        # `false` is a filter in its own right, not "no filter": "what is *not* monitored".
        assert await names("uptime_enabled=false") == ["acme-blog.nl", "bravo.nl"]
        assert await names(f"company_id={acme}&hosting_id={cluster}") == ["acme-shop.nl"]


async def test_company_panel_shows_five_websites_and_the_whole_count(client_for) -> None:
    """The domains panel's rule, one entity over: five rows and an honest total."""
    t = await make_tenant("web-panel-cap")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company, _ = await _domain(c, headers, "site-00.nl")
        for i in range(1, 7):
            domain = (
                await c.post(
                    "/api/v1/domains",
                    json={"name": f"site-{i:02d}.nl", "company_id": company},
                    headers=headers,
                )
            ).json()["id"]
            created = await c.post(
                "/api/v1/websites", json={"domain_id": domain}, headers=headers
            )
            assert created.status_code == 201, created.text
        first = await c.post(
            "/api/v1/websites",
            json={"domain_id": (await c.get("/api/v1/domains?q=site-00", headers=headers)).json()[
                "items"
            ][0]["id"]},
            headers=headers,
        )
        assert first.status_code == 201, first.text

        res = await c.get(f"/api/v1/companies/{company}/panels", headers=headers)
        panel = next(p for p in res.json() if p["key"] == "websites.company")["data"]
        assert panel["total"] == 7
        assert len(panel["websites"]) == 5
