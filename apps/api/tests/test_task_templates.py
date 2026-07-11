"""Task templates: manager-gated CRUD, onboarding automation via company events, isolation."""

from __future__ import annotations

from datetime import date, timedelta

from app.modules.tasks.recurrence import today_local
from tests.conftest import auth_cookie, make_tenant
from tests.test_task_subresources import add_member

_TEMPLATE = {
    "name": "Client onboarding",
    "trigger": "company_status",
    "trigger_status": "onboarding",
    "items": [
        {
            "title": "Kick-off call",
            "priority": "high",
            "relative_due_days": 2,
            "checklist_title": "Prep",
            "checklist_items": [{"title": "Agenda"}, {"title": "Slides", "description": "10 max"}],
        },
        {"title": "Set up analytics", "relative_due_days": 7},
    ],
}


async def test_template_crud_is_manager_gated(client_for) -> None:
    t = await make_tenant("tpl-crud")
    owner_headers = await auth_cookie(t.user)
    member = await add_member(t, role="member")
    member_headers = await auth_cookie(member)

    async with client_for(t.host) as c:
        assert (
            await c.post("/api/v1/tasks/templates", json=_TEMPLATE, headers=member_headers)
        ).status_code == 403

        created = await c.post(
            "/api/v1/tasks/templates", json=_TEMPLATE, headers=owner_headers
        )
        assert created.status_code == 201
        template = created.json()
        assert [i["title"] for i in template["items"]] == ["Kick-off call", "Set up analytics"]

        # Items are replaced wholesale on update.
        updated = await c.patch(
            f"/api/v1/tasks/templates/{template['id']}",
            json={"items": [{"title": "Only one"}], "active": False},
            headers=owner_headers,
        )
        assert [i["title"] for i in updated.json()["items"]] == ["Only one"]
        assert updated.json()["active"] is False

        assert (
            await c.delete(
                f"/api/v1/tasks/templates/{template['id']}", headers=owner_headers
            )
        ).status_code == 204


async def test_company_created_with_trigger_status_instantiates(client_for) -> None:
    t = await make_tenant("tpl-create")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.post("/api/v1/tasks/templates", json=_TEMPLATE, headers=headers)
        company = (
            await c.post(
                "/api/v1/companies",
                json={"name": "New Client", "status": "onboarding"},
                headers=headers,
            )
        ).json()
        assert company["status"] == "onboarding"

        tasks = (
            await c.get(
                "/api/v1/tasks",
                params={"company_id": company["id"]},
                headers=headers,
            )
        ).json()["items"]
        assert {row["title"] for row in tasks} == {"Kick-off call", "Set up analytics"}

        kickoff = next(row for row in tasks if row["title"] == "Kick-off call")
        assert date.fromisoformat(kickoff["due_date"]) == today_local() + timedelta(days=2)
        assert kickoff["checklist_total"] == 2

        detail = (await c.get(f"/api/v1/tasks/{kickoff['id']}", headers=headers)).json()
        assert any(a["action"] == "template_applied" for a in detail["activities"])


async def test_status_transition_triggers_once_and_only_on_match(client_for) -> None:
    t = await make_tenant("tpl-transition")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.post("/api/v1/tasks/templates", json=_TEMPLATE, headers=headers)
        company = (
            await c.post("/api/v1/companies", json={"name": "Lead Co"}, headers=headers)
        ).json()
        assert company["status"] == "active"  # default

        # Non-matching transition → nothing.
        await c.patch(
            f"/api/v1/companies/{company['id']}", json={"status": "lead"}, headers=headers
        )
        assert (
            await c.get(
                "/api/v1/tasks", params={"company_id": company["id"]}, headers=headers
            )
        ).json()["total"] == 0

        # Matching transition → instantiated.
        await c.patch(
            f"/api/v1/companies/{company['id']}",
            json={"status": "onboarding"},
            headers=headers,
        )
        assert (
            await c.get(
                "/api/v1/tasks", params={"company_id": company["id"]}, headers=headers
            )
        ).json()["total"] == 2

        # Same status again in a PATCH → no transition, no duplicates.
        await c.patch(
            f"/api/v1/companies/{company['id']}",
            json={"status": "onboarding", "notes": "unchanged status"},
            headers=headers,
        )
        assert (
            await c.get(
                "/api/v1/tasks", params={"company_id": company["id"]}, headers=headers
            )
        ).json()["total"] == 2


async def test_manual_apply_and_inactive_templates_skipped(client_for) -> None:
    t = await make_tenant("tpl-apply")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        template = (
            await c.post("/api/v1/tasks/templates", json=_TEMPLATE, headers=headers)
        ).json()
        # Deactivate: the automation must skip it…
        await c.patch(
            f"/api/v1/tasks/templates/{template['id']}",
            json={"active": False},
            headers=headers,
        )
        company = (
            await c.post(
                "/api/v1/companies",
                json={"name": "Quiet Co", "status": "onboarding"},
                headers=headers,
            )
        ).json()
        assert (
            await c.get(
                "/api/v1/tasks", params={"company_id": company["id"]}, headers=headers
            )
        ).json()["total"] == 0

        # …but manual apply still works regardless of trigger/active.
        applied = await c.post(
            f"/api/v1/tasks/templates/{template['id']}/apply",
            json={"company_id": company["id"]},
            headers=headers,
        )
        assert applied.status_code == 201
        assert len(applied.json()) == 2


async def test_templates_tenant_isolation(client_for) -> None:
    a = await make_tenant("tpl-iso-a")
    b = await make_tenant("tpl-iso-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)

    async with client_for(a.host) as ca:
        template = (
            await ca.post("/api/v1/tasks/templates", json=_TEMPLATE, headers=a_headers)
        ).json()

    async with client_for(b.host) as cb:
        assert (await cb.get("/api/v1/tasks/templates", headers=b_headers)).json() == []
        assert (
            await cb.get(f"/api/v1/tasks/templates/{template['id']}", headers=b_headers)
        ).status_code == 404
