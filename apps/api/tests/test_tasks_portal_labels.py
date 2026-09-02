"""A client reads its task, never the agency's filing on it.

A task label is what the agency sorts its own work by — "moeilijke klant", "wacht op
betaling", the name of a colleague's queue — and ticking ``visible_to_client`` on a task says
nothing about those words. A portal login gets the row and the card with no chips, and the
org's label vocabulary answers empty on the one lookup a client can reach, while staff on the
same endpoints keep everything (docs/PORTAL.md, the #446–#449 rule: the API is the boundary).
"""

from __future__ import annotations

from sqlalchemy import select

from app.core.auth.models import User
from app.db import async_session_maker
from tests.conftest import FAR_FUTURE_DUE, auth_cookie, make_tenant


async def test_portal_never_reads_task_labels(client_for) -> None:
    t = await make_tenant("task-portal-labels")
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
                    "email": "piet-task-labels@example.com",
                    "company_ids": [company["id"]],
                },
                headers=headers,
            )
        ).json()
        label = (
            await c.post(
                "/api/v1/tasks/labels",
                json={"name": "Moeilijke klant", "color": "red"},
                headers=headers,
            )
        ).json()
        task = (
            await c.post(
                "/api/v1/tasks",
                json={
                    "title": "Testbestelling",
                    "company_id": company["id"],
                    "due_date": FAR_FUTURE_DUE,
                    "visible_to_client": True,
                    "label_ids": [label["id"]],
                },
                headers=headers,
            )
        ).json()

        await c.post(f"/api/v1/portal/logins/contact/{contact['id']}", headers=headers)
        async with async_session_maker() as session:
            portal_user = await session.scalar(select(User).where(User.email == contact["email"]))
        portal = await auth_cookie(portal_user)

        # Staff keep the chips on the row, the card and the vocabulary.
        staff_rows = (await c.get("/api/v1/tasks", headers=headers)).json()["items"]
        assert [lbl["name"] for lbl in staff_rows[0]["labels"]] == ["Moeilijke klant"]
        staff_detail = (await c.get(f"/api/v1/tasks/{task['id']}", headers=headers)).json()
        assert [lbl["id"] for lbl in staff_detail["labels"]] == [label["id"]]
        staff_vocabulary = (await c.get("/api/v1/tasks/labels", headers=headers)).json()
        assert [lbl["id"] for lbl in staff_vocabulary] == [label["id"]]

        # The client gets the task and none of the agency's filing — however it is asked for.
        rows = (await c.get("/api/v1/tasks", headers=portal)).json()["items"]
        assert [r["id"] for r in rows] == [task["id"]]
        assert rows[0]["labels"] == []

        detail = await c.get(f"/api/v1/tasks/{task['id']}", headers=portal)
        assert detail.status_code == 200, detail.text
        assert detail.json()["title"] == "Testbestelling"
        assert detail.json()["labels"] == []

        vocabulary = await c.get("/api/v1/tasks/labels", headers=portal)
        assert vocabulary.status_code == 200, vocabulary.text
        assert vocabulary.json() == []
