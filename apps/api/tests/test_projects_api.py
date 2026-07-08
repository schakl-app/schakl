"""projects module API coverage (CLAUDE.md §6, §9): CRUD, budgets, custom fields,
task linking, company panel, and tenant isolation."""

from __future__ import annotations

from tests.conftest import auth_cookie, make_tenant


async def test_requires_authentication(client_for) -> None:
    t = await make_tenant("proj-noauth")
    async with client_for(t.host) as c:
        r = await c.get("/api/v1/projects")
        assert r.status_code == 401


async def test_project_crud_with_budget_and_custom_fields(client_for) -> None:
    t = await make_tenant("proj-crud")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)

        # A per-tenant custom field on projects (proves the mixin registered "project").
        definition = await c.post(
            "/api/v1/custom-fields/definitions",
            json={
                "entity_type": "project",
                "key": "po_number",
                "label_i18n": {"nl": "PO-nummer", "en": "PO number"},
                "data_type": "text",
            },
            headers=headers,
        )
        assert definition.status_code == 201

        company = await c.post("/api/v1/companies", json={"name": "Acme"}, headers=headers)
        company_id = company.json()["id"]

        created = await c.post(
            "/api/v1/projects",
            json={
                "name": "Website revamp",
                "company_id": company_id,
                "budget_hours": 40,
                "budget_amount": 4000,
                "hourly_rate": 100,
                "billable_default": True,
                "custom": {"po_number": "PO-42"},
            },
            headers=headers,
        )
        assert created.status_code == 201
        project = created.json()
        assert project["budget_hours"] == 40.0
        assert project["hourly_rate"] == 100.0
        assert project["custom"] == {"po_number": "PO-42"}
        assert project["status"] == "active"
        project_id = project["id"]

        # Filter projects by company.
        by_company = await c.get(
            "/api/v1/projects", params={"company_id": company_id}, headers=headers
        )
        assert by_company.json()["total"] == 1

        # Patch status + budget.
        patched = await c.patch(
            f"/api/v1/projects/{project_id}",
            json={"status": "on_hold", "budget_hours": 60},
            headers=headers,
        )
        assert patched.status_code == 200
        assert patched.json()["status"] == "on_hold"
        assert patched.json()["budget_hours"] == 60.0

        # Delete.
        deleted = await c.delete(f"/api/v1/projects/{project_id}", headers=headers)
        assert deleted.status_code == 204
        assert (await c.get("/api/v1/projects", headers=headers)).json()["total"] == 0


async def test_tasks_belong_to_project(client_for) -> None:
    t = await make_tenant("proj-tasks")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        project = await c.post("/api/v1/projects", json={"name": "P1"}, headers=headers)
        project_id = project.json()["id"]

        task = await c.post(
            "/api/v1/tasks",
            json={"title": "Design homepage", "project_id": project_id},
            headers=headers,
        )
        assert task.status_code == 201
        assert task.json()["project_id"] == project_id

        # A project's to-do list = tasks filtered by project_id.
        listed = await c.get(
            "/api/v1/tasks", params={"project_id": project_id}, headers=headers
        )
        assert listed.json()["total"] == 1
        assert listed.json()["items"][0]["title"] == "Design homepage"


async def test_project_shows_on_company_panel(client_for) -> None:
    t = await make_tenant("proj-panel")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        company = await c.post("/api/v1/companies", json={"name": "PanelCo"}, headers=headers)
        company_id = company.json()["id"]
        await c.post(
            "/api/v1/projects",
            json={"name": "Retainer", "company_id": company_id},
            headers=headers,
        )
        panels = await c.get(f"/api/v1/companies/{company_id}/panels", headers=headers)
        keys = {p["key"] for p in panels.json()}
        assert "projects.company" in keys
        projects_panel = next(p for p in panels.json() if p["key"] == "projects.company")
        assert projects_panel["data"]["projects"][0]["name"] == "Retainer"


async def test_projects_are_tenant_isolated(client_for) -> None:
    a = await make_tenant("proj-iso-a")
    b = await make_tenant("proj-iso-b")
    async with client_for(a.host) as ca, client_for(b.host) as cb:
        created = await c_post(ca, a, {"name": "Secret A"})
        project_id = created["id"]

        # Tenant B cannot list or fetch tenant A's project.
        assert (await cb.get("/api/v1/projects", headers=await auth_cookie(b.user))).json()[
            "total"
        ] == 0
        cross = await cb.get(
            f"/api/v1/projects/{project_id}", headers=await auth_cookie(b.user)
        )
        assert cross.status_code == 404


async def c_post(client, tenant, body) -> dict:
    r = await client.post("/api/v1/projects", json=body, headers=await auth_cookie(tenant.user))
    assert r.status_code == 201
    return r.json()
