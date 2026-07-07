"""companies module API coverage (CLAUDE.md §6, §9): auth, CRUD, custom fields, panels."""

from __future__ import annotations

from tests.conftest import auth_cookie, make_tenant


async def test_requires_authentication(client_for) -> None:
    t = await make_tenant("noauth")
    async with client_for(t.host) as c:
        r = await c.get("/api/v1/companies")
        assert r.status_code == 401
        assert r.json()["error"]["message"] == "errors.unauthorized"


async def test_local_login_then_crud_with_custom_fields(client_for) -> None:
    t = await make_tenant("crud", email="crud@example.com", password="secret1234")
    async with client_for(t.host) as c:
        # Real local login (proves password auth works out of the box).
        login = await c.post(
            "/api/v1/auth/login",
            data={"username": "crud@example.com", "password": "secret1234"},
        )
        assert login.status_code in (200, 204)

        # Create with a per-tenant custom value; it round-trips.
        created = await c.post(
            "/api/v1/companies",
            json={
                "name": "Acme",
                "website": "https://acme.test",
                "custom": {"vat": "NL0001"},
            },
        )
        assert created.status_code == 201
        company = created.json()
        assert company["custom"] == {"vat": "NL0001"}
        company_id = company["id"]

        listing = await c.get("/api/v1/companies")
        assert listing.json()["total"] == 1

        updated = await c.patch(
            f"/api/v1/companies/{company_id}", json={"name": "Acme B.V."}
        )
        assert updated.status_code == 200
        assert updated.json()["name"] == "Acme B.V."

        deleted = await c.delete(f"/api/v1/companies/{company_id}")
        assert deleted.status_code == 204
        assert (await c.get("/api/v1/companies")).json()["total"] == 0


async def test_register_endpoint_creates_user(client_for) -> None:
    t = await make_tenant("reg")
    async with client_for(t.host) as c:
        r = await c.post(
            "/api/v1/auth/register",
            json={"email": "newbie@example.com", "password": "secret1234"},
        )
        assert r.status_code == 201
        assert r.json()["email"] == "newbie@example.com"


async def test_validation_error_envelope(client_for) -> None:
    t = await make_tenant("val")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        r = await c.post("/api/v1/companies", json={}, headers=headers)
        assert r.status_code == 422
        error = r.json()["error"]
        assert error["code"] == "validation"
        assert error["message"] == "errors.validation"
        assert error["fields"]["name"] == "errors.required"


async def test_client_role_cannot_write(client_for) -> None:
    t = await make_tenant("ro", role="client")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        r = await c.post("/api/v1/companies", json={"name": "Nope"}, headers=headers)
        assert r.status_code == 403
        assert r.json()["error"]["message"] == "errors.forbidden"


async def test_company_panels_compose(client_for) -> None:
    t = await make_tenant("pan")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Panel Co"}, headers=headers)
        ).json()
        r = await c.get(f"/api/v1/companies/{company['id']}/panels", headers=headers)
        assert r.status_code == 200
        panels = {p["key"]: p for p in r.json()}
        assert "companies.details" in panels
        assert panels["companies.details"]["title_key"] == "companies.panel.details"
        assert panels["companies.details"]["data"]["name"] == "Panel Co"


async def test_meta_modules(client_for) -> None:
    t = await make_tenant("meta")
    async with client_for(t.host) as c:
        r = await c.get("/api/v1/meta/modules")
        assert r.status_code == 200
        data = r.json()
        assert "companies" in data["enabled_modules"]
        assert "company" in data["customizable_entity_types"]
        assert data["default_locale"] == "nl"
        assert data["local_login_enabled"] is True
