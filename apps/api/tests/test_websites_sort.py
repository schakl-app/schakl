"""Server-side sorting for the website list (#251).

A website has no name of its own: ``name`` orders by the parent domain's name, ``company``
walks domain → company, ``hosting`` by the hosting account's name — each a correlated
bare-table subquery (§6), so the row sorts by what the cell prints without a join
multiplying it or a cross-module import.
"""

from __future__ import annotations

from tests.conftest import auth_cookie, make_tenant


async def _company(client, headers, name: str) -> str:
    res = await client.post("/api/v1/companies", json={"name": name}, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _domain(client, headers, name: str, company_id: str) -> str:
    res = await client.post(
        "/api/v1/domains", json={"name": name, "company_id": company_id}, headers=headers
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _website(client, headers, domain_id: str, **extra) -> str:
    res = await client.post(
        "/api/v1/websites", json={"domain_id": domain_id, **extra}, headers=headers
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def test_sort_by_name_orders_by_domain_name(client_for) -> None:
    t = await make_tenant("web-sort-name")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers, "Acme")
        # Created in reverse-alphabetical order, so the default (created_at desc) differs.
        for name in ("charlie.nl", "bravo.nl", "alpha.nl"):
            await _website(c, headers, await _domain(c, headers, name, company))

        asc = (await c.get("/api/v1/websites?sort=name", headers=headers)).json()
        assert [i["domain_name"] for i in asc["items"]] == ["alpha.nl", "bravo.nl", "charlie.nl"]

        desc = (await c.get("/api/v1/websites?sort=-name", headers=headers)).json()
        assert [i["domain_name"] for i in desc["items"]] == ["charlie.nl", "bravo.nl", "alpha.nl"]


async def test_sort_by_company_walks_the_domain_bridge(client_for) -> None:
    t = await make_tenant("web-sort-co")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        zebra = await _company(c, headers, "Zebra")
        alpha = await _company(c, headers, "alpha")
        await _website(c, headers, await _domain(c, headers, "one.nl", zebra))
        await _website(c, headers, await _domain(c, headers, "two.nl", alpha))

        asc = (await c.get("/api/v1/websites?sort=company", headers=headers)).json()
        assert [i["company_name"] for i in asc["items"]] == ["alpha", "Zebra"]

        desc = (await c.get("/api/v1/websites?sort=-company", headers=headers)).json()
        assert [i["company_name"] for i in desc["items"]] == ["Zebra", "alpha"]


async def test_sort_by_hosting_puts_unhosted_last_both_ways(client_for) -> None:
    t = await make_tenant("web-sort-host")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers, "Acme")
        zed = (await c.post("/api/v1/hosting", json={"name": "Zed"}, headers=headers)).json()["id"]
        ace = (await c.post("/api/v1/hosting", json={"name": "ace"}, headers=headers)).json()["id"]
        await _website(c, headers, await _domain(c, headers, "one.nl", company), hosting_id=zed)
        await _website(c, headers, await _domain(c, headers, "two.nl", company), hosting_id=ace)
        await _website(c, headers, await _domain(c, headers, "bare.nl", company))

        asc = (await c.get("/api/v1/websites?sort=hosting", headers=headers)).json()
        assert [i["domain_name"] for i in asc["items"]] == ["two.nl", "one.nl", "bare.nl"]

        # NULLS LAST in both directions: an unhosted site never floats to the top.
        desc = (await c.get("/api/v1/websites?sort=-hosting", headers=headers)).json()
        assert [i["domain_name"] for i in desc["items"]] == ["one.nl", "two.nl", "bare.nl"]


async def test_unknown_sort_key_is_refused(client_for) -> None:
    t = await make_tenant("web-sort-bad")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        bad = await c.get("/api/v1/websites?sort=org_id", headers=headers)
        assert bad.status_code == 400
        assert bad.json()["error"]["message"] == "errors.invalid_sort"
