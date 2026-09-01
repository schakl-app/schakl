"""A contact is mailed when a task is assigned to them — if they can open it (#454).

The request queues a job; the worker decides. A contact with no portal login, or a disabled
one, gets nothing (a link to a login they do not have is a control that always refuses); an
active login gets the tenant's own words, in the org's language, with a link into the portal.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.core.auth.models import User
from app.db import async_session_maker
from app.modules.tasks.emails import tasks_send_contact_assigned
from tests.conftest import FAR_FUTURE_DUE, auth_cookie, make_tenant

_BREVO = {
    "provider": "brevo",
    "from_email": "noreply@agency-example.nl",
    "from_name": "Agency",
    "api_key": "xkeysib-secret-123",
}


async def test_assignment_queues_a_job_and_the_worker_mails_an_active_login(
    client_for, monkeypatch
) -> None:
    t = await make_tenant("tasks-contact-mail")
    headers = await auth_cookie(t.user)

    queued: list[tuple] = []

    async def _enqueue(function, *args, **kwargs):  # noqa: ANN001, ARG001
        queued.append((function, args))
        return object()

    monkeypatch.setattr("app.modules.tasks.service.enqueue", _enqueue)

    captured: list = []

    async def _capture(provider, config, sender, message):  # noqa: ANN001, ARG001
        captured.append(message)
        return True, None

    monkeypatch.setattr("app.core.email.service.send_email", _capture)

    async with client_for(t.host) as c:
        assert (
            await c.put("/api/v1/settings/email", json=_BREVO, headers=headers)
        ).status_code == 200
        company = (
            await c.post("/api/v1/companies", json={"name": "Klant BV"}, headers=headers)
        ).json()
        contact = (
            await c.post(
                "/api/v1/contacts",
                json={
                    "first_name": "Piet",
                    "last_name": "Klant",
                    "email": "piet-task-mail@example.com",
                    "company_ids": [company["id"]],
                },
                headers=headers,
            )
        ).json()

        # Assigned on create: one job is queued for the task.
        task = (
            await c.post(
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
        ).json()
        assert [f for f, _ in queued] == ["tasks_send_contact_assigned"]
        assert queued[0][1] == (str(t.org.id), task["id"])

        # No portal login yet: the worker sends nothing.
        await tasks_send_contact_assigned({}, str(t.org.id), task["id"])
        assert captured == []

        # With an active login it mails the contact, in the tenant's built-in words, with a
        # link into the portal — and it is not the agency's own address it went to.
        await c.post(f"/api/v1/portal/logins/contact/{contact['id']}", headers=headers)
        # The invite itself leaves by the same seam; only what the worker sends is under test.
        captured.clear()
        await tasks_send_contact_assigned({}, str(t.org.id), task["id"])
        assert len(captured) == 1
        message = captured[0]
        assert message.to == contact["email"]
        assert "Fotomateriaal aanleveren" in message.subject
        assert f"/tasks/{task['id']}" in message.text
        assert message.html is not None and f"/tasks/{task['id']}" in message.html
        assert "Piet Klant" in message.text

        # A disabled login is a login they cannot use: nothing goes out.
        await c.delete(f"/api/v1/portal/logins/contact/{contact['id']}", headers=headers)
        async with async_session_maker() as session:
            user = await session.scalar(select(User).where(User.email == contact["email"]))
            assert user is not None and user.is_active is False
        await tasks_send_contact_assigned({}, str(t.org.id), task["id"])
        assert len(captured) == 1

        # An update that hands the task to a contact queues too; one that does not, does not.
        queued.clear()
        other = (
            await c.post(
                "/api/v1/tasks",
                json={"title": "Teksten", "company_id": company["id"], "due_date": FAR_FUTURE_DUE},
                headers=headers,
            )
        ).json()
        assert queued == []
        await c.patch(
            f"/api/v1/tasks/{other['id']}", json={"title": "Teksten nakijken"}, headers=headers
        )
        assert queued == []
        await c.patch(
            f"/api/v1/tasks/{other['id']}",
            json={"assignee_contact_id": contact["id"], "assignees": []},
            headers=headers,
        )
        assert [f for f, _ in queued] == ["tasks_send_contact_assigned"]
        # Saving the same contact again is not a new assignment.
        queued.clear()
        await c.patch(
            f"/api/v1/tasks/{other['id']}",
            json={"assignee_contact_id": contact["id"], "assignees": []},
            headers=headers,
        )
        assert queued == []

        # The tenant rewords it: the override is what the worker sends.
        assert (
            await c.put(
                "/api/v1/settings/email/templates",
                json={
                    "kind": "tasks.assigned_contact",
                    "locale": "nl",
                    "subject": "Nieuwe taak van {brand}: {title}",
                    "body_html": "<p>Dag {name}</p><a href=\"{link}\">open</a>",
                },
                headers=headers,
            )
        ).status_code == 200, "template save"
        await c.post(f"/api/v1/portal/logins/contact/{contact['id']}", headers=headers)
        captured.clear()
        await tasks_send_contact_assigned({}, str(t.org.id), task["id"])
        assert len(captured) == 1
        assert captured[0].subject.endswith(": Fotomateriaal aanleveren")
        assert "Dag Piet Klant" in captured[0].html
        # A task deleted or reassigned before pickup says nothing.
        await tasks_send_contact_assigned({}, str(t.org.id), str(uuid.uuid4()))
        assert len(captured) == 1
