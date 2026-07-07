"""contacts module API coverage (CLAUDE.md §6, §9): CRUD, company panel, tenant isolation."""

from __future__ import annotations

from tests.conftest import auth_cookie, make_tenant


async def test_contact_crud_and_company_filter(client_for) -> None:
    t = await make_tenant("con-crud")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Acme"}, headers=headers)
        ).json()

        created = await c.post(
            "/api/v1/contacts",
            json={"first_name": "Ada", "last_name": "Lovelace", "company_id": company["id"]},
            headers=headers,
        )
        assert created.status_code == 201
        contact = created.json()
        assert contact["first_name"] == "Ada"

        # Filter by company.
        listing = await c.get(
            "/api/v1/contacts", params={"company_id": company["id"]}, headers=headers
        )
        assert listing.json()["total"] == 1

        deleted = await c.delete(f"/api/v1/contacts/{contact['id']}", headers=headers)
        assert deleted.status_code == 204


async def test_contacts_panel_on_company(client_for) -> None:
    t = await make_tenant("con-panel")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Panel Co"}, headers=headers)
        ).json()
        await c.post(
            "/api/v1/contacts",
            json={"first_name": "Grace", "company_id": company["id"]},
            headers=headers,
        )
        panels = {
            p["key"]: p
            for p in (
                await c.get(f"/api/v1/companies/{company['id']}/panels", headers=headers)
            ).json()
        }
        assert "contacts.company" in panels
        assert panels["contacts.company"]["data"]["contacts"][0]["first_name"] == "Grace"


async def test_contacts_tenant_isolation(client_for) -> None:
    a = await make_tenant("con-iso-a")
    b = await make_tenant("con-iso-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)

    async with client_for(a.host) as ca:
        created = await ca.post(
            "/api/v1/contacts", json={"first_name": "Secret"}, headers=a_headers
        )
        assert created.status_code == 201
        a_contact_id = created.json()["id"]

    async with client_for(b.host) as cb:
        assert (await cb.get("/api/v1/contacts", headers=b_headers)).json()["total"] == 0
        fetch = await cb.get(f"/api/v1/contacts/{a_contact_id}", headers=b_headers)
        assert fetch.status_code == 404
