"""A client reads its projects, never the agency's economics (#449).

The hour budget, the amount, the burn and a task's time estimate are what the agency agreed
with itself about the work. A portal login gets the project — name, status, who, when — with
those fields blank on every read (list, detail, the company hub's panel, the task rows), while
staff on the same endpoints keep them. The web draws no column or block for what the API
blanks, but the API is the boundary.
"""

from __future__ import annotations

from sqlalchemy import select

from app.core.auth.models import User
from app.db import async_session_maker
from tests.conftest import FAR_FUTURE_DUE, auth_cookie, make_tenant


async def test_portal_never_reads_budget_hours_or_estimates(client_for) -> None:
    t = await make_tenant("proj-portal-budget")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Klant BV"}, headers=headers)
        ).json()
        contact = (
            await c.post(
                "/api/v1/contacts",
                json={
                    "first_name": "Piet",
                    "last_name": "Klant",
                    "email": "piet-proj-portal@example.com",
                    "company_ids": [company["id"]],
                },
                headers=headers,
            )
        ).json()
        project = (
            await c.post(
                "/api/v1/projects",
                json={
                    "name": "Webshop",
                    "company_id": company["id"],
                    "budget_hours": 120,
                    "budget_amount": 9600,
                },
                headers=headers,
            )
        ).json()
        assert project["budget_hours"] == 120
        task = (
            await c.post(
                "/api/v1/tasks",
                json={
                    "title": "Testbestelling",
                    "company_id": company["id"],
                    "project_id": project["id"],
                    "due_date": FAR_FUTURE_DUE,
                    "allocated_minutes": 90,
                    "visible_to_client": True,
                },
                headers=headers,
            )
        ).json()
        assert task["allocated_minutes"] == 90

        await c.post(f"/api/v1/portal/logins/contact/{contact['id']}", headers=headers)
        async with async_session_maker() as session:
            portal_user = await session.scalar(
                select(User).where(User.email == contact["email"])
            )
        portal = await auth_cookie(portal_user)

        # Staff keep every figure, burn included.
        staff_detail = (
            await c.get(f"/api/v1/projects/{project['id']}?hours=true", headers=headers)
        ).json()
        assert staff_detail["budget_hours"] == 120
        assert staff_detail["hours"] is not None

        # The client gets the project and none of its economics — however it is asked for.
        detail = await c.get(f"/api/v1/projects/{project['id']}?hours=true", headers=portal)
        assert detail.status_code == 200, detail.text
        body = detail.json()
        assert body["name"] == "Webshop"
        assert body["budget_hours"] is None
        assert body["budget_amount"] is None
        assert body["hours"] is None
        assert body["budget_sources"] == []

        listed = (await c.get("/api/v1/projects?hours=true", headers=portal)).json()["items"]
        assert [row["id"] for row in listed] == [project["id"]]
        assert listed[0]["budget_hours"] is None and listed[0]["hours"] is None

        panels = (
            await c.get(f"/api/v1/companies/{company['id']}/panels", headers=portal)
        ).json()
        projects_panel = next(p for p in panels if p["key"] == "projects.company")
        row = projects_panel["data"]["projects"][0]
        assert row["name"] == "Webshop"
        assert "budget_hours" not in row and "billable_default" not in row

        rows = (await c.get("/api/v1/tasks", headers=portal)).json()["items"]
        assert [r["id"] for r in rows] == [task["id"]]
        assert rows[0]["allocated_minutes"] is None
        assert (
            await c.get(f"/api/v1/tasks/{task['id']}", headers=portal)
        ).json()["allocated_minutes"] is None
