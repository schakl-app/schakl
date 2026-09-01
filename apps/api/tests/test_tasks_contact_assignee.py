"""A task assigned to a client contact is the contact's (#453).

Assigning a contact makes the task visible to the client (recorded on the trail like any other
field), every reader gets the contact's *name* rather than an id they cannot resolve, and a
portal login's "mine" — the dashboard tile and ``/tasks/mine`` — answers with the tasks assigned
to the contact behind the session, resolved through the portal-subject seam.
"""

from __future__ import annotations

from sqlalchemy import select

from app.core.auth.models import User
from app.db import async_session_maker
from tests.conftest import FAR_FUTURE_DUE, auth_cookie, make_tenant


async def test_contact_assignee_is_visible_named_and_mine(client_for) -> None:
    t = await make_tenant("tasks-contact-assignee")
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
                    "email": "piet-task-assignee@example.com",
                    "company_ids": [company["id"]],
                },
                headers=headers,
            )
        ).json()

        # Created for the contact: client-visible without anybody ticking the box, and named.
        created = await c.post(
            "/api/v1/tasks",
            json={
                "title": "Fotomateriaal aanleveren",
                "company_id": company["id"],
                "due_date": FAR_FUTURE_DUE,
                "assignee_contact_id": contact["id"],
                "assignees": [],
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        task = created.json()
        assert task["visible_to_client"] is True
        named = (await c.get(f"/api/v1/tasks/{task['id']}", headers=headers)).json()
        assert named["assignee_contact_name"] == "Piet Klant"

        # Reassigned to the contact later: the flip is recorded on the trail as a field edit.
        internal = (
            await c.post(
                "/api/v1/tasks",
                json={
                    "title": "Teksten nakijken",
                    "company_id": company["id"],
                    "due_date": FAR_FUTURE_DUE,
                },
                headers=headers,
            )
        ).json()
        assert internal["visible_to_client"] is False
        patched = await c.patch(
            f"/api/v1/tasks/{internal['id']}",
            json={"assignee_contact_id": contact["id"], "assignees": []},
            headers=headers,
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["visible_to_client"] is True
        detail = (await c.get(f"/api/v1/tasks/{internal['id']}", headers=headers)).json()
        assert detail["assignee_contact_name"] == "Piet Klant"
        changed = [
            a["payload"]["changed"] for a in detail["activities"] if a["action"] == "updated"
        ]
        assert any("visible_to_client" in fields for fields in changed)

        # The list rows carry the name too — one query for the page, whoever reads it.
        rows = (await c.get("/api/v1/tasks", headers=headers)).json()["items"]
        assert {r["assignee_contact_name"] for r in rows} == {"Piet Klant"}

        # The contact, signed in: both tasks are *mine*, and the name resolves for them too —
        # the contacts endpoint is exactly what a portal login cannot read.
        await c.post(f"/api/v1/portal/logins/contact/{contact['id']}", headers=headers)
        async with async_session_maker() as session:
            portal_user = await session.scalar(
                select(User).where(User.email == contact["email"])
            )
        portal = await auth_cookie(portal_user)
        mine = await c.get("/api/v1/tasks/dashboard-mine", headers=portal)
        assert mine.status_code == 200, mine.text
        assert {row["id"] for row in mine.json()["items"]} == {task["id"], internal["id"]}
        assert mine.json()["total"] == 2
        listed = (await c.get("/api/v1/tasks/mine", headers=portal)).json()
        assert {row["assignee_contact_name"] for row in listed} == {"Piet Klant"}
        own = (await c.get(f"/api/v1/tasks/{task['id']}", headers=portal)).json()
        assert own["assignee_contact_name"] == "Piet Klant"
        # `?open=true` is the working set a client's dashboard lists beside their own tasks.
        opened = (await c.get("/api/v1/tasks?open=true", headers=portal)).json()["items"]
        assert {row["id"] for row in opened} == {task["id"], internal["id"]}

        # Staff "mine" is untouched by the contact rule: nothing here is assigned to the owner.
        staff_mine = (await c.get("/api/v1/tasks/dashboard-mine", headers=headers)).json()
        assert staff_mine["total"] == 0
