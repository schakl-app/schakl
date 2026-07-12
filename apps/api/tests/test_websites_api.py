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
