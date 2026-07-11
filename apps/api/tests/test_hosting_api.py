"""Hosting module: CRUD, party/provider validation, tenant isolation (issue #93)."""

from __future__ import annotations

from tests.conftest import auth_cookie, make_tenant


async def test_hosting_crud_with_provider_and_contact(client_for) -> None:
    t = await make_tenant("host-crud")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Acme"}, headers=headers)
        ).json()["id"]
        provider = (
            await c.post(
                "/api/v1/providers", json={"kind": "hosting", "name": "Cloudflare"}, headers=headers
            )
        ).json()["id"]

        created = await c.post(
            "/api/v1/hosting",
            json={
                "name": "prod cluster",
                "company_id": company,
                "provider_id": provider,
                "ip_address": "203.0.113.7",
                "contact": {"type": "agency"},
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        hosting = created.json()
        assert hosting["provider_name"] == "Cloudflare"
        assert hosting["company_name"] == "Acme"
        assert hosting["contact"]["type"] == "agency"

        # Appears on the company panel.
        panels = await c.get(f"/api/v1/companies/{company}/panels", headers=headers)
        hosting_panel = next(p for p in panels.json() if p["key"] == "hosting.company")
        assert hosting_panel["data"]["hosting"][0]["name"] == "prod cluster"


async def test_hosting_tenant_isolation(client_for) -> None:
    a = await make_tenant("host-iso-a")
    b = await make_tenant("host-iso-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)
    async with client_for(a.host) as ca:
        created = await ca.post(
            "/api/v1/hosting", json={"name": "shared"}, headers=a_headers
        )
        hid = created.json()["id"]
    async with client_for(b.host) as cb:
        assert (await cb.get("/api/v1/hosting", headers=b_headers)).json()["total"] == 0
        assert (await cb.get(f"/api/v1/hosting/{hid}", headers=b_headers)).status_code == 404
