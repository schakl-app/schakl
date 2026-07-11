"""Provider catalog: CRUD, permission gating and tenant isolation (issue #89)."""

from __future__ import annotations

from tests.conftest import auth_cookie, make_tenant


async def test_provider_crud_and_kind_filter(client_for) -> None:
    t = await make_tenant("prov-crud")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        created = await c.post(
            "/api/v1/providers",
            json={"kind": "registrar", "name": "OXXA"},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        provider = created.json()
        assert provider["kind"] == "registrar"
        assert provider["active"] is True

        await c.post(
            "/api/v1/providers",
            json={"kind": "email", "name": "Microsoft Exchange"},
            headers=headers,
        )

        # Kind filter returns only that kind.
        registrars = await c.get("/api/v1/providers?kind=registrar", headers=headers)
        assert [p["name"] for p in registrars.json()] == ["OXXA"]

        # Deactivate → excluded from the default list, present with include_inactive.
        await c.patch(
            f"/api/v1/providers/{provider['id']}", json={"active": False}, headers=headers
        )
        active = await c.get("/api/v1/providers?kind=registrar", headers=headers)
        assert active.json() == []
        all_rows = await c.get(
            "/api/v1/providers?kind=registrar&include_inactive=true", headers=headers
        )
        assert len(all_rows.json()) == 1


async def test_provider_manage_requires_permission(client_for) -> None:
    t = await make_tenant("prov-perm", role="member")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        # A member may read the catalog (to fill pickers) but not manage it.
        assert (await c.get("/api/v1/providers", headers=headers)).status_code == 200
        blocked = await c.post(
            "/api/v1/providers", json={"kind": "dns", "name": "Cloudflare"}, headers=headers
        )
        assert blocked.status_code == 403


async def test_provider_tenant_isolation(client_for) -> None:
    a = await make_tenant("prov-iso-a")
    b = await make_tenant("prov-iso-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)
    async with client_for(a.host) as ca:
        created = await ca.post(
            "/api/v1/providers", json={"kind": "hosting", "name": "Hetzner"}, headers=a_headers
        )
        pid = created.json()["id"]
    async with client_for(b.host) as cb:
        assert (await cb.get("/api/v1/providers", headers=b_headers)).json() == []
        assert (await cb.get(f"/api/v1/providers/{pid}", headers=b_headers)).status_code == 404
