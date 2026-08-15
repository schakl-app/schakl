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
                "billable_default": True,
                "custom": {"po_number": "PO-42"},
            },
            headers=headers,
        )
        assert created.status_code == 201
        project = created.json()
        assert project["budget_hours"] == 40.0
        # No project rate exists anymore (#226): money is priced per employee.
        assert "hourly_rate" not in project
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
        # A project belongs to a client, so every create here names one.
        company = await c.post("/api/v1/companies", json={"name": "Klant"}, headers=headers)
        project = await c.post(
            "/api/v1/projects",
            json={"name": "P1", "company_id": company.json()["id"]},
            headers=headers,
        )
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
        company_a = await ca.post(
            "/api/v1/companies", json={"name": "Klant A"}, headers=await auth_cookie(a.user)
        )
        created = await c_post(ca, a, {"name": "Secret A", "company_id": company_a.json()["id"]})
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


# --- a project belongs to a client ------------------------------------------------- #
async def test_a_project_cannot_be_created_without_a_client(client_for) -> None:
    """The whole point: a project is work done *for* somebody, so there is no unattached one."""
    t = await make_tenant("proj-client-required")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        missing = await c.post("/api/v1/projects", json={"name": "Los project"}, headers=headers)
        assert missing.status_code == 422
        explicit_null = await c.post(
            "/api/v1/projects", json={"name": "Los project", "company_id": None}, headers=headers
        )
        assert explicit_null.status_code == 422


async def test_a_project_can_move_client_but_never_lose_one(client_for) -> None:
    """An update may reassign the work; it may not orphan it.

    ``exclude_unset`` is what makes the two distinguishable, so both halves are pinned: an
    update that says nothing about the client leaves it alone, and one that says ``null``
    is refused with the field named rather than silently detaching the project.
    """
    t = await make_tenant("proj-client-move")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        first = (
            await c.post("/api/v1/companies", json={"name": "Eerste"}, headers=headers)
        ).json()["id"]
        second = (
            await c.post("/api/v1/companies", json={"name": "Tweede"}, headers=headers)
        ).json()["id"]
        project = (
            await c.post(
                "/api/v1/projects",
                json={"name": "Onderhoud", "company_id": first},
                headers=headers,
            )
        ).json()

        moved = await c.patch(
            f"/api/v1/projects/{project['id']}", json={"company_id": second}, headers=headers
        )
        assert moved.status_code == 200
        assert moved.json()["company_id"] == second

        untouched = await c.patch(
            f"/api/v1/projects/{project['id']}", json={"name": "Beheer"}, headers=headers
        )
        assert untouched.json()["company_id"] == second

        detached = await c.patch(
            f"/api/v1/projects/{project['id']}", json={"company_id": None}, headers=headers
        )
        assert detached.status_code == 422
        body = detached.json()["error"]
        assert body["fields"]["company_id"] == "errors.projects_company_required"


async def test_a_project_row_names_its_client(client_for) -> None:
    """The list is sectioned by client, so the client's *name* is part of a project row.

    The browser used to resolve it against the page's client picker, which is capped at 200 and
    is a different query — every project of the 201st client printed "—" for its client and, once
    the list groups, would have fallen into "Overig". Pinned on all four paths that answer with a
    ``ProjectRead``, because a field that is only sometimes populated reads as "no client".
    """
    t = await make_tenant("proj-company-name")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        first = (
            await c.post("/api/v1/companies", json={"name": "Zeewolde BV"}, headers=headers)
        ).json()["id"]
        second = (
            await c.post("/api/v1/companies", json={"name": "Almere BV"}, headers=headers)
        ).json()["id"]

        created = await c.post(
            "/api/v1/projects", json={"name": "Huisstijl", "company_id": first}, headers=headers
        )
        assert created.status_code == 201
        assert created.json()["company_name"] == "Zeewolde BV"
        project_id = created.json()["id"]

        detail = await c.get(f"/api/v1/projects/{project_id}", headers=headers)
        assert detail.json()["company_name"] == "Zeewolde BV"

        listed = await c.get("/api/v1/projects", headers=headers)
        assert [p["company_name"] for p in listed.json()["items"]] == ["Zeewolde BV"]

        # Moving the client moves the section it lands in, so the name has to move with it.
        moved = await c.patch(
            f"/api/v1/projects/{project_id}", json={"company_id": second}, headers=headers
        )
        assert moved.json()["company_name"] == "Almere BV"


async def test_naming_the_clients_of_a_page_costs_one_query(client_for, count_queries) -> None:
    """One lookup over the page's distinct clients, never one per project.

    Invisible in the JSON — a per-row resolve returns the identical body (docs/PERFORMANCE.md),
    which is why this counts statements instead of reading them.
    """
    t = await make_tenant("proj-company-name-budget")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        companies = [
            (
                await c.post("/api/v1/companies", json={"name": f"Klant {i}"}, headers=headers)
            ).json()["id"]
            for i in range(3)
        ]
        for i in range(3):
            await c.post(
                "/api/v1/projects",
                json={"name": f"Klein {i}", "company_id": companies[i]},
                headers=headers,
            )

        with count_queries() as small:
            assert (await c.get("/api/v1/projects", headers=headers)).status_code == 200

        for i in range(3, 30):
            await c.post(
                "/api/v1/projects",
                json={"name": f"Groot {i}", "company_id": companies[i % 3]},
                headers=headers,
            )

        with count_queries() as large:
            res = await c.get("/api/v1/projects", headers=headers, params={"limit": "50"})
        assert len(res.json()["items"]) == 30
        assert all(p["company_name"] for p in res.json()["items"])

    assert len(large.statements) == len(small.statements), (
        f"{len(small.statements)} queries for 3 projects, {len(large.statements)} for 30 — "
        "something resolves per row"
    )


async def _project(c, headers, company_id: str, name: str, status: str) -> None:
    r = await c.post(
        "/api/v1/projects",
        json={"name": name, "company_id": company_id, "status": status},
        headers=headers,
    )
    assert r.status_code == 201, r.text


async def test_status_filter_takes_a_set_and_absent_still_means_everything(client_for) -> None:
    """``status`` takes several values, the way the client list already does (#329).

    A project list is normally wanted *without* the archive, and a single-valued filter could
    not say that: ``status=active`` also hides the paused work and the work just delivered,
    both of which are still the agency's. The screen picks that narrowing default; this
    endpoint only makes it expressible — no ``status`` still means every status, because the
    pickers, the impex export and the generated MCP surface all read this one endpoint.
    """
    t = await make_tenant("proj-status-set")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company_id = (
            await c.post("/api/v1/companies", json={"name": "Acme"}, headers=headers)
        ).json()["id"]
        await _project(c, headers, company_id, "Archiefkast", "archived")
        await _project(c, headers, company_id, "Bouwput", "active")
        await _project(c, headers, company_id, "Cadeaubon", "completed")
        await _project(c, headers, company_id, "Dakkapel", "on_hold")

        every = (await c.get("/api/v1/projects", headers=headers)).json()
        assert every["total"] == 4

        one = (
            await c.get("/api/v1/projects", params={"status": "active"}, headers=headers)
        ).json()
        assert [p["name"] for p in one["items"]] == ["Bouwput"]

        # The working set the screen opens on: every status except the archive.
        working = (
            await c.get(
                "/api/v1/projects",
                params={"status": "active,on_hold,completed"},
                headers=headers,
            )
        ).json()
        assert [p["name"] for p in working["items"]] == ["Bouwput", "Cadeaubon", "Dakkapel"]
        # The count is the filter's, not the page's — a total counted over everything would
        # report four above a list of three.
        assert working["total"] == 3


async def test_a_project_status_token_that_names_nothing_falls_back_to_the_whole_list(
    client_for,
) -> None:
    """``status`` arrives from a query string anyone can edit, so it never 422s (#316's rule).

    The *failure* direction, kept separate: a filter resolving to no statuses at all leaves the
    list alone rather than emptying it, an unknown status still matches nothing, and an unknown
    name among known ones narrows to the known ones instead of poisoning them.
    """
    t = await make_tenant("proj-status-junk")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company_id = (
            await c.post("/api/v1/companies", json={"name": "Acme"}, headers=headers)
        ).json()["id"]
        await _project(c, headers, company_id, "Archiefkast", "archived")
        await _project(c, headers, company_id, "Bouwput", "active")

        for token in (",", " , ", ",,"):
            r = await c.get("/api/v1/projects", params={"status": token}, headers=headers)
            assert r.status_code == 200, r.text
            assert r.json()["total"] == 2, token

        empty = await c.get("/api/v1/projects", params={"status": "klaar"}, headers=headers)
        assert empty.status_code == 200
        assert empty.json()["items"] == []

        mixed = (
            await c.get(
                "/api/v1/projects", params={"status": "klaar,active"}, headers=headers
            )
        ).json()
        assert [p["name"] for p in mixed["items"]] == ["Bouwput"]
