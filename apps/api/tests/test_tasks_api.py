"""tasks module API coverage (CLAUDE.md §6, §9): CRUD, My Day, company panel, isolation."""

from __future__ import annotations

from tests.conftest import auth_cookie, make_tenant


async def test_task_crud_and_status_toggle(client_for) -> None:
    t = await make_tenant("task-crud")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        created = await c.post(
            "/api/v1/tasks",
            json={"title": "Write plan", "priority": "high"},
            headers=headers,
        )
        assert created.status_code == 201
        task = created.json()
        assert task["status"] == "open"

        done = await c.patch(
            f"/api/v1/tasks/{task['id']}", json={"status": "done"}, headers=headers
        )
        assert done.json()["status"] == "done"


async def test_my_open_tasks(client_for) -> None:
    t = await make_tenant("task-mine")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        # Assigned to me and open → shows in My Day.
        await c.post(
            "/api/v1/tasks",
            json={"title": "Mine", "assignee_user_id": str(t.user.id)},
            headers=headers,
        )
        # Unassigned → excluded from My Day (only tasks assigned to me appear).
        await c.post("/api/v1/tasks", json={"title": "Unassigned"}, headers=headers)
        # Mine but done → excluded.
        done = await c.post(
            "/api/v1/tasks",
            json={"title": "Done", "assignee_user_id": str(t.user.id)},
            headers=headers,
        )
        await c.patch(
            f"/api/v1/tasks/{done.json()['id']}", json={"status": "done"}, headers=headers
        )

        mine = await c.get("/api/v1/tasks/mine", headers=headers)
        assert mine.status_code == 200
        titles = [row["title"] for row in mine.json()]
        assert titles == ["Mine"]


async def test_tasks_panel_on_company(client_for) -> None:
    t = await make_tenant("task-panel")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Panel Co"}, headers=headers)
        ).json()
        await c.post(
            "/api/v1/tasks",
            json={"title": "For company", "company_id": company["id"]},
            headers=headers,
        )
        panels = {
            p["key"]: p
            for p in (
                await c.get(f"/api/v1/companies/{company['id']}/panels", headers=headers)
            ).json()
        }
        assert "tasks.company" in panels
        assert panels["tasks.company"]["data"]["tasks"][0]["title"] == "For company"


async def test_tasks_tenant_isolation(client_for) -> None:
    a = await make_tenant("task-iso-a")
    b = await make_tenant("task-iso-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)

    async with client_for(a.host) as ca:
        created = await ca.post("/api/v1/tasks", json={"title": "Secret"}, headers=a_headers)
        a_task_id = created.json()["id"]

    async with client_for(b.host) as cb:
        assert (await cb.get("/api/v1/tasks", headers=b_headers)).json()["total"] == 0
        assert (
            await cb.get(f"/api/v1/tasks/{a_task_id}", headers=b_headers)
        ).status_code == 404
