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


async def test_position_assigned_and_reorder(client_for) -> None:
    t = await make_tenant("task-order")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        first = (await c.post("/api/v1/tasks", json={"title": "First"}, headers=headers)).json()
        second = (
            await c.post("/api/v1/tasks", json={"title": "Second"}, headers=headers)
        ).json()
        assert second["position"] > first["position"]

        # Fractional-midpoint reorder: move Second before First.
        await c.patch(
            f"/api/v1/tasks/{second['id']}",
            json={"position": first["position"] - 1},
            headers=headers,
        )
        listed = (await c.get("/api/v1/tasks", headers=headers)).json()["items"]
        assert [row["title"] for row in listed] == ["Second", "First"]


async def test_completed_at_set_and_cleared(client_for) -> None:
    t = await make_tenant("task-completed")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        task = (await c.post("/api/v1/tasks", json={"title": "T"}, headers=headers)).json()
        assert task["completed_at"] is None

        done = (
            await c.patch(
                f"/api/v1/tasks/{task['id']}", json={"status": "done"}, headers=headers
            )
        ).json()
        assert done["completed_at"] is not None

        reopened = (
            await c.patch(
                f"/api/v1/tasks/{task['id']}", json={"status": "open"}, headers=headers
            )
        ).json()
        assert reopened["completed_at"] is None


async def test_due_filters(client_for) -> None:
    from datetime import timedelta

    from app.modules.tasks.recurrence import today_local

    t = await make_tenant("task-due")
    headers = await auth_cookie(t.user)
    today = today_local()
    async with client_for(t.host) as c:
        await c.post(
            "/api/v1/tasks",
            json={"title": "Late", "due_date": (today - timedelta(days=2)).isoformat()},
            headers=headers,
        )
        await c.post(
            "/api/v1/tasks",
            json={"title": "Today", "due_date": today.isoformat()},
            headers=headers,
        )
        await c.post("/api/v1/tasks", json={"title": "Sometime"}, headers=headers)

        overdue = (
            await c.get("/api/v1/tasks", params={"due": "overdue"}, headers=headers)
        ).json()
        assert [row["title"] for row in overdue["items"]] == ["Late"]

        due_today = (
            await c.get("/api/v1/tasks", params={"due": "today"}, headers=headers)
        ).json()
        assert [row["title"] for row in due_today["items"]] == ["Today"]


async def test_task_detail_shape(client_for) -> None:
    t = await make_tenant("task-detail")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        task = (
            await c.post(
                "/api/v1/tasks",
                json={"title": "Card", "description": "Body"},
                headers=headers,
            )
        ).json()
        await c.post(
            f"/api/v1/tasks/{task['id']}/comments", json={"body": "Hello"}, headers=headers
        )

        detail = (await c.get(f"/api/v1/tasks/{task['id']}", headers=headers)).json()
        assert detail["description"] == "Body"
        assert detail["labels"] == []
        assert detail["checklists"] == []
        assert [comment["body"] for comment in detail["comments"]] == ["Hello"]
        assert {a["action"] for a in detail["activities"]} >= {"created", "commented"}

        # List rows carry the comment count aggregate.
        listed = (await c.get("/api/v1/tasks", headers=headers)).json()["items"]
        assert listed[0]["comment_count"] == 1


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
