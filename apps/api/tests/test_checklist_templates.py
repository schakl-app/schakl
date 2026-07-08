"""Checklist template repository: CRUD, apply-to-task copy, isolation."""

from __future__ import annotations

from tests.conftest import auth_cookie, make_tenant
from tests.test_task_subresources import add_member

_TEMPLATE = {"title": "Website launch", "items": ["DNS", "SSL", "Analytics"]}


async def test_checklist_template_crud_staff_writable(client_for) -> None:
    t = await make_tenant("cltpl-crud")
    member = await add_member(t)
    member_headers = await auth_cookie(member)

    async with client_for(t.host) as c:
        # Plain staff members may manage the shared repository.
        created = await c.post(
            "/api/v1/tasks/checklist-templates", json=_TEMPLATE, headers=member_headers
        )
        assert created.status_code == 201
        template = created.json()
        assert template["items"] == ["DNS", "SSL", "Analytics"]

        updated = await c.patch(
            f"/api/v1/tasks/checklist-templates/{template['id']}",
            json={"items": ["DNS", "SSL"]},
            headers=member_headers,
        )
        assert updated.json()["items"] == ["DNS", "SSL"]

        assert (
            len((await c.get("/api/v1/tasks/checklist-templates", headers=member_headers)).json())
            == 1
        )
        assert (
            await c.delete(
                f"/api/v1/tasks/checklist-templates/{template['id']}", headers=member_headers
            )
        ).status_code == 204


async def test_add_checklist_from_template_copies_items(client_for) -> None:
    t = await make_tenant("cltpl-apply")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        template = (
            await c.post("/api/v1/tasks/checklist-templates", json=_TEMPLATE, headers=headers)
        ).json()
        task = (await c.post("/api/v1/tasks", json={"title": "T"}, headers=headers)).json()

        added = await c.post(
            f"/api/v1/tasks/{task['id']}/checklists",
            json={"template_id": template["id"]},
            headers=headers,
        )
        assert added.status_code == 201

        detail = (await c.get(f"/api/v1/tasks/{task['id']}", headers=headers)).json()
        checklist = detail["checklists"][0]
        assert checklist["title"] == "Website launch"
        assert [i["title"] for i in checklist["items"]] == ["DNS", "SSL", "Analytics"]
        assert all(not i["done"] for i in checklist["items"])

        # A custom title still overrides the template's.
        renamed = await c.post(
            f"/api/v1/tasks/{task['id']}/checklists",
            json={"template_id": template["id"], "title": "Launch v2"},
            headers=headers,
        )
        assert renamed.json()["title"] == "Launch v2"

        # Neither title nor template → validation error.
        assert (
            await c.post(f"/api/v1/tasks/{task['id']}/checklists", json={}, headers=headers)
        ).status_code == 422


async def test_checklist_templates_tenant_isolation(client_for) -> None:
    a = await make_tenant("cltpl-iso-a")
    b = await make_tenant("cltpl-iso-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)

    async with client_for(a.host) as ca:
        template = (
            await ca.post("/api/v1/tasks/checklist-templates", json=_TEMPLATE, headers=a_headers)
        ).json()

    async with client_for(b.host) as cb:
        assert (await cb.get("/api/v1/tasks/checklist-templates", headers=b_headers)).json() == []
        task = (await cb.post("/api/v1/tasks", json={"title": "B"}, headers=b_headers)).json()
        assert (
            await cb.post(
                f"/api/v1/tasks/{task['id']}/checklists",
                json={"template_id": template["id"]},
                headers=b_headers,
            )
        ).status_code == 404
