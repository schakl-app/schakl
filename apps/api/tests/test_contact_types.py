"""Tenant-configurable contact types + typed links + type filter (issue #91)."""

from __future__ import annotations

from tests.conftest import auth_cookie, make_tenant


async def test_contact_type_crud_and_isolation(client_for) -> None:
    a = await make_tenant("ctype-a")
    b = await make_tenant("ctype-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)
    async with client_for(a.host) as ca:
        created = await ca.post(
            "/api/v1/contacts/types",
            json={"key": "technical", "label_i18n": {"nl": "Technisch", "en": "Technical"}},
            headers=a_headers,
        )
        assert created.status_code == 201, created.text
        assert created.json()["label_i18n"]["nl"] == "Technisch"

        # Duplicate key → 409.
        dup = await ca.post(
            "/api/v1/contacts/types", json={"key": "technical", "label_i18n": {}}, headers=a_headers
        )
        assert dup.status_code == 409

    async with client_for(b.host) as cb:
        assert (await cb.get("/api/v1/contacts/types", headers=b_headers)).json() == []


async def test_contact_type_on_link_and_filter(client_for) -> None:
    t = await make_tenant("ctype-link")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Acme"}, headers=headers)
        ).json()["id"]
        type_id = (
            await c.post(
                "/api/v1/contacts/types",
                json={"key": "billing", "label_i18n": {"nl": "Facturatie", "en": "Billing"}},
                headers=headers,
            )
        ).json()["id"]

        # Create two contacts; type only the first for this company.
        typed = (
            await c.post(
                "/api/v1/contacts",
                json={"first_name": "Ada"},
                headers=headers,
            )
        ).json()["id"]
        await c.post("/api/v1/contacts", json={"first_name": "Bo"}, headers=headers)

        link = await c.post(
            f"/api/v1/contacts/{typed}/links",
            json={"company_id": company, "contact_type_id": type_id},
            headers=headers,
        )
        assert link.status_code == 201
        assert link.json()["companies"][0]["contact_type_id"] == type_id

        # Filtering the contacts list by type returns only the typed contact.
        filtered = await c.get(f"/api/v1/contacts?contact_type_id={type_id}", headers=headers)
        names = [c_["first_name"] for c_ in filtered.json()["items"]]
        assert names == ["Ada"]


async def test_contact_type_manage_requires_permission(client_for) -> None:
    t = await make_tenant("ctype-perm", role="member")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        # A member may read types (to type a link) but not manage the catalog.
        assert (await c.get("/api/v1/contacts/types", headers=headers)).status_code == 200
        blocked = await c.post(
            "/api/v1/contacts/types", json={"key": "x", "label_i18n": {}}, headers=headers
        )
        assert blocked.status_code == 403
