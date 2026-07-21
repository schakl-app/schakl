"""Server-side sorting for the domain list (#251).

``company`` and ``registrar``/``dns`` order by the *name the cell prints* — resolved via
correlated subqueries, never the FK, and without importing another module's internals
(bare tables for companies, the core Provider model for providers).
"""

from __future__ import annotations

from tests.conftest import auth_cookie, make_tenant


async def _company(client, headers, name: str) -> str:
    res = await client.post("/api/v1/companies", json={"name": name}, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _provider(client, headers, name: str, kind: str = "registrar") -> str:
    res = await client.post(
        "/api/v1/providers", json={"kind": kind, "name": name}, headers=headers
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _domain(client, headers, name: str, company_id: str, **extra) -> str:
    res = await client.post(
        "/api/v1/domains", json={"name": name, "company_id": company_id, **extra}, headers=headers
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def test_sort_by_company_orders_by_client_name(client_for) -> None:
    t = await make_tenant("dom-sort-co")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        zebra = await _company(c, headers, "Zebra")
        alpha = await _company(c, headers, "alpha")
        await _domain(c, headers, "a-domain.nl", zebra)
        await _domain(c, headers, "b-domain.nl", alpha)

        asc = (await c.get("/api/v1/domains?sort=company", headers=headers)).json()
        assert [i["company_name"] for i in asc["items"]] == ["alpha", "Zebra"]

        desc = (await c.get("/api/v1/domains?sort=-company", headers=headers)).json()
        assert [i["company_name"] for i in desc["items"]] == ["Zebra", "alpha"]


async def test_sort_by_registrar_puts_unset_last_both_ways(client_for) -> None:
    t = await make_tenant("dom-sort-reg")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers, "Acme")
        zed = await _provider(c, headers, "Zed Registry")
        ace = await _provider(c, headers, "ace hosting")
        await _domain(c, headers, "one.nl", company, registrar_provider_id=zed)
        await _domain(c, headers, "two.nl", company, registrar_provider_id=ace)
        await _domain(c, headers, "bare.nl", company)

        asc = (await c.get("/api/v1/domains?sort=registrar", headers=headers)).json()
        assert [i["name"] for i in asc["items"]] == ["two.nl", "one.nl", "bare.nl"]

        # NULLS LAST in both directions: a domain with no registrar never floats to the top.
        desc = (await c.get("/api/v1/domains?sort=-registrar", headers=headers)).json()
        assert [i["name"] for i in desc["items"]] == ["one.nl", "two.nl", "bare.nl"]


async def test_unknown_sort_key_is_refused(client_for) -> None:
    t = await make_tenant("dom-sort-bad")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        bad = await c.get("/api/v1/domains?sort=org_id", headers=headers)
        assert bad.status_code == 400
        assert bad.json()["error"]["message"] == "errors.invalid_sort"
