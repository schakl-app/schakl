"""Core activity trail (issue #67): records field edits, and never crosses a tenant.

The trail is a core capability (CLAUDE.md §9), so it gets the same tenant-isolation test every
module's data does — one org must never read another's activity, even by naming its entity id.
"""

from __future__ import annotations

from tests.conftest import auth_cookie, make_tenant


async def _feed(client, headers, entity_type: str, entity_id: str):
    resp = await client.get(
        "/api/v1/activity",
        params={"entity_type": entity_type, "entity_id": entity_id},
        headers=headers,
    )
    assert resp.status_code == 200
    return resp.json()


async def test_create_and_update_are_recorded_with_values(client_for) -> None:
    t = await make_tenant("act-rec")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post(
                "/api/v1/companies",
                json={"name": "Acme", "status": "lead"},
                headers=headers,
            )
        ).json()

        # Creating the record leaves a "created" line.
        feed = await _feed(c, headers, "company", company["id"])
        assert [item["action"] for item in feed] == ["created"]
        assert feed[0]["actor_name"] == t.user.email
        assert feed[0]["actor_deleted"] is False

        # Editing tracked fields leaves an "updated" line carrying the before/after values.
        await c.patch(
            f"/api/v1/companies/{company['id']}",
            json={"name": "Acme B.V.", "status": "active"},
            headers=headers,
        )
        feed = await _feed(c, headers, "company", company["id"])
        assert [item["action"] for item in feed] == ["updated", "created"]
        changes = feed[0]["payload"]["changes"]
        assert changes["name"] == {"from": "Acme", "to": "Acme B.V."}
        assert changes["status"] == {"from": "lead", "to": "active"}


async def test_company_hub_composes_the_activity_panel(client_for) -> None:
    """The trail reaches the company page through the core panel on the API's panel seam (#67)."""
    t = await make_tenant("act-panel")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Acme"}, headers=headers)
        ).json()
        r = await c.get(f"/api/v1/companies/{company['id']}/panels", headers=headers)
        panels = {p["key"]: p for p in r.json()}
        assert "activity.trail" in panels
        assert panels["activity.trail"]["title_key"] == "activity.title"
        assert [i["action"] for i in panels["activity.trail"]["data"]["items"]] == ["created"]


async def test_no_op_update_records_nothing(client_for) -> None:
    t = await make_tenant("act-noop")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Acme"}, headers=headers)
        ).json()
        # Re-sending the same name changes nothing, so the trail must not grow an empty edit.
        await c.patch(
            f"/api/v1/companies/{company['id']}",
            json={"name": "Acme"},
            headers=headers,
        )
        feed = await _feed(c, headers, "company", company["id"])
        assert [item["action"] for item in feed] == ["created"]


async def test_activity_tenant_isolation(client_for) -> None:
    a = await make_tenant("act-iso-a")
    b = await make_tenant("act-iso-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)

    async with client_for(a.host) as ca:
        company = (
            await ca.post("/api/v1/companies", json={"name": "Secret"}, headers=a_headers)
        ).json()
        assert await _feed(ca, a_headers, "company", company["id"])  # A sees its own trail

    # B, naming A's company id under B's hostname, gets nothing — RLS scopes the rows to B's org.
    async with client_for(b.host) as cb:
        assert await _feed(cb, b_headers, "company", company["id"]) == []
