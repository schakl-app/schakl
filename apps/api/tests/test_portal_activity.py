"""A client reads their record; the trail of how it got that way is the agency's.

The activity trail (§16) is the paper record of the agency's own work: who edited which field,
which colleague was signed in as whom, the subject line of a contactmoment, the filename of an
attachment nobody ticked ``client_visible``. Ticking a task or a project visible to the client
says nothing about any of that — it decides that the *record* is theirs to see.

The gate was written once, at ``GET /api/v1/activity``, and there were three readers:

* the endpoint itself, which refused a portal login and always had;
* the company hub's **core panel**, which composes ``ActivityService`` behind ``activity.read``
  — a permission the seeded ``client`` role holds — so a client's own hub printed fifteen lines
  of the agency's history with the staff actor names on them;
* the tasks module's **legacy** ``task_activities`` trail on ``GET /tasks/{id}``, which went out
  in full on every task a client can open, ``attachment_added: photo.jpg`` included, for files
  the file list, the bytes and the thumbnail all refuse them (docs/STORAGE.md).

So the gate moved into ``ActivityService.feed``/``count`` (one answer, every reader), the task
detail skips its own query the way the labels above it do, and the panel declares
``audience=staff`` so the hub does not compose it at all — folding it away as "nothing here yet"
would leave a ＋ chip promising a card with nothing behind it (#364).

Staff on the same endpoints keep everything: every assertion here has its pair.
"""

from __future__ import annotations

from sqlalchemy import select

from app.core.auth.models import User
from app.db import async_session_maker
from tests.conftest import FAR_FUTURE_DUE, auth_cookie, make_tenant


async def test_portal_never_reads_the_activity_trail(client_for) -> None:
    t = await make_tenant("portal-activity")
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
                    "email": "piet-activity@example.com",
                    "company_ids": [company["id"]],
                },
                headers=headers,
            )
        ).json()
        task = (
            await c.post(
                "/api/v1/tasks",
                json={
                    "title": "Productfoto's aanleveren",
                    "company_id": company["id"],
                    "due_date": FAR_FUTURE_DUE,
                    "visible_to_client": True,
                },
                headers=headers,
            )
        ).json()
        # Two edits, so both trails have something to leak: the core one on the company (an
        # ordinary field change) and the tasks module's own on the task.
        await c.patch(
            f"/api/v1/companies/{company['id']}",
            json={"notes": "Betaalt altijd te laat. Niet meer vooruit werken."},
            headers=headers,
        )
        await c.patch(
            f"/api/v1/tasks/{task['id']}", json={"priority": "high"}, headers=headers
        )

        await c.post(f"/api/v1/portal/logins/contact/{contact['id']}", headers=headers)
        async with async_session_maker() as session:
            portal_user = await session.scalar(select(User).where(User.email == contact["email"]))
        portal = await auth_cookie(portal_user)

        # --- staff keep all three surfaces ------------------------------------------------- #
        staff_feed = (
            await c.get(
                "/api/v1/activity",
                params={"entity_type": "company", "entity_id": company["id"]},
                headers=headers,
            )
        ).json()
        assert [row["action"] for row in staff_feed], staff_feed
        staff_panels = (
            await c.get(f"/api/v1/companies/{company['id']}/panels", headers=headers)
        ).json()
        trail = next(p for p in staff_panels if p["key"] == "activity.trail")
        assert trail["data"]["total"] >= 1
        staff_task = (await c.get(f"/api/v1/tasks/{task['id']}", headers=headers)).json()
        assert [a["action"] for a in staff_task["activities"]], staff_task["activities"]

        # --- the client gets the records and none of the history ---------------------------- #
        feed = await c.get(
            "/api/v1/activity",
            params={"entity_type": "company", "entity_id": company["id"]},
            headers=portal,
        )
        assert feed.status_code == 200, feed.text
        assert feed.json() == []

        panels = await c.get(f"/api/v1/companies/{company['id']}/panels", headers=portal)
        assert panels.status_code == 200, panels.text
        # Not empty-and-folded: absent. A ＋ chip headed "Activiteit" is the same question
        # asked in one word instead of ten (#364).
        assert "activity.trail" not in {p["key"] for p in panels.json()}

        detail = await c.get(f"/api/v1/tasks/{task['id']}", headers=portal)
        assert detail.status_code == 200, detail.text
        assert detail.json()["title"] == "Productfoto's aanleveren"
        assert detail.json()["activities"] == []
