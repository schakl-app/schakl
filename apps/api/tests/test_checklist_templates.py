"""Checklist template repository: CRUD, apply-to-task copy, isolation."""

from __future__ import annotations

from tests.conftest import auth_cookie, make_tenant
from tests.test_task_subresources import add_member

# Items are `{title, description}` since issue #66 (a bare title list is no longer accepted).
_TEMPLATE = {
    "title": "Website launch",
    "items": [{"title": "DNS"}, {"title": "SSL"}, {"title": "Analytics", "description": "GA4 + GTM"}],
}


async def test_checklist_template_crud_needs_the_write_permission(client_for) -> None:
    """The shared repository is org configuration: readable by all, written by
    ``tasks.checklist_template.write`` (issue #19). The seeded ``member`` role does not hold it;
    an agency that wants it back ticks one box in Instellingen → Rollen."""
    t = await make_tenant("cltpl-crud")
    owner_headers = await auth_cookie(t.user)
    member = await add_member(t)
    member_headers = await auth_cookie(member)

    async with client_for(t.host) as c:
        assert (
            await c.post(
                "/api/v1/tasks/checklist-templates", json=_TEMPLATE, headers=member_headers
            )
        ).status_code == 403

        created = await c.post(
            "/api/v1/tasks/checklist-templates", json=_TEMPLATE, headers=owner_headers
        )
        assert created.status_code == 201
        template = created.json()
        assert [i["title"] for i in template["items"]] == ["DNS", "SSL", "Analytics"]
        assert template["items"][2]["description"] == "GA4 + GTM"

        updated = await c.patch(
            f"/api/v1/tasks/checklist-templates/{template['id']}",
            json={"items": [{"title": "DNS"}, {"title": "SSL"}]},
            headers=owner_headers,
        )
        assert [i["title"] for i in updated.json()["items"]] == ["DNS", "SSL"]

        # Everyone reads them — a member applies a template to a task they are on.
        assert (
            len((await c.get("/api/v1/tasks/checklist-templates", headers=member_headers)).json())
            == 1
        )
        assert (
            await c.delete(
                f"/api/v1/tasks/checklist-templates/{template['id']}", headers=owner_headers
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
        # The item description is copied from the template, not just the title (issue #66).
        assert checklist["items"][2]["description"] == "GA4 + GTM"

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
