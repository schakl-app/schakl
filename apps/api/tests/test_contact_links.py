"""company↔contact link coverage (CLAUDE.md §6): many-to-many, primary, tenant isolation."""

from __future__ import annotations

from tests.conftest import auth_cookie, make_tenant


def _panel_contacts(panels: list[dict]) -> list[dict]:
    for p in panels:
        if p["key"] == "contacts.company":
            return p["data"]["contacts"]
    return []


async def test_contact_links_across_two_companies(client_for) -> None:
    """A person can be a contact at several clients at once — the core M2M guarantee."""
    t = await make_tenant("lnk-m2m")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        co1 = (await c.post("/api/v1/companies", json={"name": "One"}, headers=headers)).json()
        co2 = (await c.post("/api/v1/companies", json={"name": "Two"}, headers=headers)).json()
        contact = (
            await c.post(
                "/api/v1/contacts",
                json={"first_name": "Sam", "company_ids": [co1["id"]]},
                headers=headers,
            )
        ).json()

        # Attach the same person to a second client — without detaching the first.
        linked = await c.post(
            f"/api/v1/contacts/{contact['id']}/links",
            json={"company_id": co2["id"]},
            headers=headers,
        )
        assert linked.status_code == 201
        company_ids = {c["company_id"] for c in linked.json()["companies"]}
        assert company_ids == {co1["id"], co2["id"]}

        # They now show on both clients' panels.
        for co in (co1, co2):
            panels = (
                await c.get(f"/api/v1/companies/{co['id']}/panels", headers=headers)
            ).json()
            assert [c["first_name"] for c in _panel_contacts(panels)] == ["Sam"]


async def test_primary_is_unique_per_company(client_for) -> None:
    t = await make_tenant("lnk-primary")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        co = (await c.post("/api/v1/companies", json={"name": "Prim"}, headers=headers)).json()
        first = (
            await c.post(
                "/api/v1/contacts",
                json={"first_name": "First", "company_ids": [co["id"]]},
                headers=headers,
            )
        ).json()
        second = (
            await c.post(
                "/api/v1/contacts",
                json={"first_name": "Second", "company_ids": [co["id"]]},
                headers=headers,
            )
        ).json()

        # The first attached contact is primary; a later one is not (auto).
        panels = (await c.get(f"/api/v1/companies/{co['id']}/panels", headers=headers)).json()
        by_name = {c["first_name"]: c for c in _panel_contacts(panels)}
        assert by_name["First"]["is_primary"] is True
        assert by_name["Second"]["is_primary"] is False
        # Primary-first ordering.
        assert _panel_contacts(panels)[0]["first_name"] == "First"

        # Promote the second — the first must lose primary (one primary per company).
        promoted = await c.patch(
            f"/api/v1/contacts/{second['id']}/links/{co['id']}",
            json={"is_primary": True},
            headers=headers,
        )
        assert promoted.status_code == 200
        panels = (await c.get(f"/api/v1/companies/{co['id']}/panels", headers=headers)).json()
        by_name = {c["first_name"]: c for c in _panel_contacts(panels)}
        assert by_name["Second"]["is_primary"] is True
        assert by_name["First"]["is_primary"] is False

        # Detach the first — it only unlinks, the contact still exists.
        unlinked = await c.delete(
            f"/api/v1/contacts/{first['id']}/links/{co['id']}", headers=headers
        )
        assert unlinked.status_code == 204
        assert (
            await c.get(f"/api/v1/contacts/{first['id']}", headers=headers)
        ).status_code == 200
        panels = (await c.get(f"/api/v1/companies/{co['id']}/panels", headers=headers)).json()
        assert [c["first_name"] for c in _panel_contacts(panels)] == ["Second"]


async def test_link_tenant_isolation(client_for) -> None:
    """A tenant can never link its contact to another tenant's company, nor vice-versa."""
    a = await make_tenant("lnk-iso-a")
    b = await make_tenant("lnk-iso-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)

    async with client_for(a.host) as ca:
        a_company = (
            await ca.post("/api/v1/companies", json={"name": "A Co"}, headers=a_headers)
        ).json()
        a_contact = (
            await ca.post("/api/v1/contacts", json={"first_name": "A"}, headers=a_headers)
        ).json()

    async with client_for(b.host) as cb:
        b_contact = (
            await cb.post("/api/v1/contacts", json={"first_name": "B"}, headers=b_headers)
        ).json()

        # B cannot see A's contact to link it.
        cross_contact = await cb.post(
            f"/api/v1/contacts/{a_contact['id']}/links",
            json={"company_id": a_company["id"]},
            headers=b_headers,
        )
        assert cross_contact.status_code == 404

        # B's own contact cannot be linked to A's company (unknown company for B).
        cross_company = await cb.post(
            f"/api/v1/contacts/{b_contact['id']}/links",
            json={"company_id": a_company["id"]},
            headers=b_headers,
        )
        assert cross_company.status_code == 404


async def test_create_path_attaches_contacts_with_chosen_primary(client_for) -> None:
    """The client-create flow: create the company, attach its contacts, then set the primary.

    The web create form picks contact persons before the company exists, so the action replays
    exactly this sequence. Attaching auto-promotes the *first* contact, which is why the chosen
    primary is set explicitly at the end — assert that choice wins.
    """
    t = await make_tenant("create-contacts")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        co = (
            await c.post("/api/v1/companies", json={"name": "Fresh"}, headers=headers)
        ).json()
        first = (
            await c.post("/api/v1/contacts", json={"first_name": "First"}, headers=headers)
        ).json()
        chosen = (
            await c.post("/api/v1/contacts", json={"first_name": "Chosen"}, headers=headers)
        ).json()

        for contact in (first, chosen):
            linked = await c.post(
                f"/api/v1/contacts/{contact['id']}/links",
                json={"company_id": co["id"], "is_primary": False},
                headers=headers,
            )
            assert linked.status_code == 201
        promoted = await c.patch(
            f"/api/v1/contacts/{chosen['id']}/links/{co['id']}",
            json={"is_primary": True},
            headers=headers,
        )
        assert promoted.status_code == 200

        contacts = _panel_contacts(
            (await c.get(f"/api/v1/companies/{co['id']}/panels", headers=headers)).json()
        )
        assert [(x["first_name"], x["is_primary"]) for x in contacts] == [
            ("Chosen", True),
            ("First", False),
        ]


async def test_create_path_cannot_attach_another_tenants_contact(client_for) -> None:
    """Tenant isolation on the create path: a foreign contact id never lands on a new company."""
    a = await make_tenant("create-iso-a")
    b = await make_tenant("create-iso-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)

    async with client_for(b.host) as cb:
        b_contact = (
            await cb.post("/api/v1/contacts", json={"first_name": "Foreign"}, headers=b_headers)
        ).json()

    async with client_for(a.host) as ca:
        a_company = (
            await ca.post("/api/v1/companies", json={"name": "A Co"}, headers=a_headers)
        ).json()

        rejected = await ca.post(
            f"/api/v1/contacts/{b_contact['id']}/links",
            json={"company_id": a_company["id"], "is_primary": False},
            headers=a_headers,
        )
        assert rejected.status_code == 404

        # The new company is left with no contacts at all — nothing leaked across the tenant line.
        panels = (
            await ca.get(f"/api/v1/companies/{a_company['id']}/panels", headers=a_headers)
        ).json()
        assert _panel_contacts(panels) == []
