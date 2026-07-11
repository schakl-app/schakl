"""The emit sites: does doing the real thing through the API actually notify anybody?

``test_notifications_fanout`` drives the bus directly to pin the *rules*. These drive the REST
endpoints instead, so a payload key renamed in a service — or an ``await emit`` never wired —
fails here rather than in production. Immediate events are asserted through the inbox API;
digest events land with a future ``visible_at``, so those are read from the table.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db import async_session_maker, set_current_org
from app.modules.notifications.models import Notification, NotificationEvent
from tests.conftest import Tenant, auth_cookie, leave_workday, make_tenant
from tests.test_notifications_fanout import _events, _member


async def _inbox(tenant: Tenant, user_id: uuid.UUID) -> list[tuple[str, dict]]:
    """Every delivered row for a user, visible or still pending, newest last."""
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        rows = (
            await session.execute(
                select(NotificationEvent.event_type, NotificationEvent.payload)
                .join(Notification, Notification.event_id == NotificationEvent.id)
                .where(
                    Notification.org_id == tenant.org.id,
                    Notification.user_id == user_id,
                )
                .order_by(Notification.created_at.asc())
            )
        ).all()
        return [(event_type, payload) for event_type, payload in rows]


# --------------------------------------------------------------------------- #
# tasks
# --------------------------------------------------------------------------- #
async def test_assigning_a_task_notifies_the_assignee(client_for) -> None:
    t = await make_tenant("emit-task-assign")
    member = await _member(t, "assignee@example.com")
    owner_headers = await auth_cookie(t.user)
    member_headers = await auth_cookie(member)

    async with client_for(t.host) as c:
        created = await c.post(
            "/api/v1/tasks",
            json={"title": "Write the brief", "assignee_user_id": str(member.id)},
            headers=owner_headers,
        )
        assert created.status_code == 201

        # task.assigned is immediate, so it is in the bell straight away.
        body = (await c.get("/api/v1/notifications", headers=member_headers)).json()
        assert body["total"] == 1
        item = body["items"][0]
        assert item["event_type"] == "task.assigned"
        assert item["payload"]["title"] == "Write the brief"
        assert item["entity_id"] == created.json()["id"]


async def test_assigning_a_task_to_yourself_is_silent(client_for) -> None:
    t = await make_tenant("emit-task-self")
    headers = await auth_cookie(t.user)

    async with client_for(t.host) as c:
        await c.post(
            "/api/v1/tasks",
            json={"title": "My own chore", "assignee_user_id": str(t.user.id)},
            headers=headers,
        )
        assert (await c.get("/api/v1/notifications", headers=headers)).json()["total"] == 0
    # The feed still records it — only the interruption was suppressed.
    assert len(await _events(t, "task.assigned")) == 1


async def test_reassigning_tells_the_new_owner_and_the_old_one(client_for) -> None:
    t = await make_tenant("emit-task-reassign")
    first = await _member(t, "first@example.com")
    second = await _member(t, "second@example.com")
    headers = await auth_cookie(t.user)

    async with client_for(t.host) as c:
        task = (
            await c.post(
                "/api/v1/tasks",
                json={"title": "Hand-off", "assignee_user_id": str(first.id)},
                headers=headers,
            )
        ).json()
        res = await c.patch(
            f"/api/v1/tasks/{task['id']}",
            json={"assignee_user_id": str(second.id)},
            headers=headers,
        )
        assert res.status_code == 200

    assert [event for event, _ in await _inbox(t, second.id)] == ["task.assigned"]
    # The person who lost it hears about it too — as a digest, not an interruption.
    assert [event for event, _ in await _inbox(t, first.id)] == [
        "task.assigned",
        "task.unassigned",
    ]


async def test_a_status_change_and_a_comment_reach_the_assignee(client_for) -> None:
    t = await make_tenant("emit-task-activity")
    member = await _member(t, "worker@example.com")
    headers = await auth_cookie(t.user)

    async with client_for(t.host) as c:
        task = (
            await c.post(
                "/api/v1/tasks",
                json={"title": "Ship it", "assignee_user_id": str(member.id)},
                headers=headers,
            )
        ).json()
        await c.patch(f"/api/v1/tasks/{task['id']}", json={"status": "done"}, headers=headers)
        commented = await c.post(
            f"/api/v1/tasks/{task['id']}/comments",
            json={"body": "  Nicely   done,  thanks!  "},
            headers=headers,
        )
        assert commented.status_code == 201

    inbox = await _inbox(t, member.id)
    assert [event for event, _ in inbox] == [
        "task.assigned",
        "task.status_changed",
        "task.commented",
    ]
    status_payload = next(p for e, p in inbox if e == "task.status_changed")
    assert status_payload["from"] == "open" and status_payload["to"] == "done"
    # The excerpt is normalized whitespace, not the raw body.
    assert next(p for e, p in inbox if e == "task.commented")["excerpt"] == (
        "Nicely done, thanks!"
    )


# --------------------------------------------------------------------------- #
# companies
# --------------------------------------------------------------------------- #
async def test_company_status_change_notifies_the_roster(client_for) -> None:
    t = await make_tenant("emit-company-status")
    member = await _member(t, "account@example.com")
    headers = await auth_cookie(t.user)

    async with client_for(t.host) as c:
        company = (
            await c.post(
                "/api/v1/companies",
                json={
                    "name": "Acme",
                    "status": "lead",
                    "assignees": [{"user_id": str(member.id), "is_primary": True}],
                },
                headers=headers,
            )
        ).json()
        patched = await c.patch(
            f"/api/v1/companies/{company['id']}", json={"status": "active"}, headers=headers
        )
        assert patched.status_code == 200

    inbox = await _inbox(t, member.id)
    assert [event for event, _ in inbox] == ["company.created", "company.status_changed"]
    changed = next(p for e, p in inbox if e == "company.status_changed")
    assert changed["from"] == "lead" and changed["to"] == "active"
    assert changed["title"] == "Acme"


async def test_a_status_patch_that_changes_nothing_announces_nothing(client_for) -> None:
    """Re-sending the current status is not a status change."""
    t = await make_tenant("emit-company-noop")
    headers = await auth_cookie(t.user)

    async with client_for(t.host) as c:
        company = (
            await c.post(
                "/api/v1/companies", json={"name": "Same", "status": "active"}, headers=headers
            )
        ).json()
        await c.patch(
            f"/api/v1/companies/{company['id']}", json={"status": "active"}, headers=headers
        )

    assert await _events(t, "company.status_changed") == []


async def test_adding_someone_to_a_company_tells_them(client_for) -> None:
    t = await make_tenant("emit-company-assign")
    member = await _member(t, "newbie@example.com")
    owner_headers = await auth_cookie(t.user)
    member_headers = await auth_cookie(member)

    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Globex"}, headers=owner_headers)
        ).json()
        await c.patch(
            f"/api/v1/companies/{company['id']}",
            json={"assignees": [{"user_id": str(member.id), "is_primary": True}]},
            headers=owner_headers,
        )
        body = (await c.get("/api/v1/notifications", headers=member_headers)).json()

    assert body["total"] == 1
    assert body["items"][0]["event_type"] == "company.assigned"
    assert body["items"][0]["payload"]["title"] == "Globex"


async def test_editing_a_company_without_touching_the_roster_reassigns_nobody(client_for) -> None:
    """A status edit must not re-announce the standing roster as freshly assigned."""
    t = await make_tenant("emit-company-noreassign")
    member = await _member(t, "steady@example.com")
    headers = await auth_cookie(t.user)

    async with client_for(t.host) as c:
        company = (
            await c.post(
                "/api/v1/companies",
                json={
                    "name": "Stable",
                    "assignees": [{"user_id": str(member.id), "is_primary": True}],
                },
                headers=headers,
            )
        ).json()
        await c.patch(
            f"/api/v1/companies/{company['id']}",
            json={"invoice_email": "billing@stable.test"},
            headers=headers,
        )

    assert await _events(t, "company.assigned") == []


# The company page's activity panel is core now (issue #67) — it reads the audit trail, not
# the notification log. Its coverage moved to test_activity_api.py
# (test_company_hub_composes_the_activity_panel); this module no longer contributes that panel.


async def test_the_onboarding_automation_still_runs_alongside_the_fan_out(client_for) -> None:
    """Regression: notifications subscribed to the same company events the tasks module uses.

    Enriching the payload must not disturb ``on_company_status``, which reads named keys.
    """
    t = await make_tenant("emit-company-template")
    headers = await auth_cookie(t.user)
    template = {
        "name": "Onboarding",
        "trigger": "company_status",
        "trigger_status": "onboarding",
        "items": [{"title": "Kick-off call"}],
    }

    async with client_for(t.host) as c:
        assert (
            await c.post("/api/v1/tasks/templates", json=template, headers=headers)
        ).status_code == 201
        company = await c.post(
            "/api/v1/companies",
            json={"name": "Initech", "status": "onboarding"},
            headers=headers,
        )
        assert company.status_code == 201

        tasks = (await c.get("/api/v1/tasks", headers=headers)).json()
        titles = [item["title"] for item in tasks["items"]]

    assert "Kick-off call" in titles, "the template automation must still fire"
    assert len(await _events(t, "company.created")) == 1, "and the event was recorded"


# --------------------------------------------------------------------------- #
# projects
# --------------------------------------------------------------------------- #
async def test_project_assignment_is_immediate_and_status_change_follows(client_for) -> None:
    t = await make_tenant("emit-project")
    member = await _member(t, "dev@example.com")
    owner_headers = await auth_cookie(t.user)
    member_headers = await auth_cookie(member)

    async with client_for(t.host) as c:
        project = (
            await c.post(
                "/api/v1/projects",
                json={
                    "name": "Website rebuild",
                    "assignees": [{"user_id": str(member.id), "is_primary": True}],
                },
                headers=owner_headers,
            )
        ).json()

        # A project has no "created" event, so the assignment is the signal — and immediate.
        body = (await c.get("/api/v1/notifications", headers=member_headers)).json()
        assert body["total"] == 1
        assert body["items"][0]["event_type"] == "project.assigned"
        assert body["items"][0]["payload"]["title"] == "Website rebuild"

        await c.patch(
            f"/api/v1/projects/{project['id']}",
            json={"status": "completed"},
            headers=owner_headers,
        )

    inbox = await _inbox(t, member.id)
    assert [event for event, _ in inbox] == ["project.assigned", "project.status_changed"]
    changed = next(p for e, p in inbox if e == "project.status_changed")
    assert changed["from"] == "active" and changed["to"] == "completed"


# --------------------------------------------------------------------------- #
# leave
# --------------------------------------------------------------------------- #
async def test_leave_asks_the_managers_and_answers_the_requester(client_for) -> None:
    t = await make_tenant("emit-leave")
    member = await _member(t, "employee@example.com")
    owner_headers = await auth_cookie(t.user)
    member_headers = await auth_cookie(member)
    start = leave_workday(0).isoformat()

    async with client_for(t.host) as c:
        types = (await c.get("/api/v1/leave/types", headers=member_headers)).json()
        unpaid = next(lt for lt in types if lt["key"] == "unpaid")
        request = (
            await c.post(
                "/api/v1/leave/requests",
                json={"leave_type_id": unpaid["id"], "start_date": start, "end_date": start},
                headers=member_headers,
            )
        ).json()
        assert request["status"] == "pending"

        # The manager is interrupted immediately: an approval queue is not digest material.
        managers = (await c.get("/api/v1/notifications", headers=owner_headers)).json()
        assert managers["total"] == 1
        assert managers["items"][0]["event_type"] == "leave.requested"

        decided = await c.post(
            f"/api/v1/leave/requests/{request['id']}/decide",
            json={"approved": True},
            headers=owner_headers,
        )
        assert decided.status_code == 200

        answered = (await c.get("/api/v1/notifications", headers=member_headers)).json()
        assert answered["total"] == 1
        assert answered["items"][0]["event_type"] == "leave.approved"
        assert answered["items"][0]["entity_id"] == request["id"]


async def test_a_sick_registration_asks_nobody(client_for) -> None:
    """Sick leave needs no approval, so it must not land in a manager's queue."""
    t = await make_tenant("emit-leave-sick")
    member = await _member(t, "ill@example.com")
    owner_headers = await auth_cookie(t.user)
    member_headers = await auth_cookie(member)
    start = leave_workday(6).isoformat()

    async with client_for(t.host) as c:
        types = (await c.get("/api/v1/leave/types", headers=member_headers)).json()
        sick = next(lt for lt in types if lt["key"] == "sick")
        created = await c.post(
            "/api/v1/leave/requests",
            json={"leave_type_id": sick["id"], "start_date": start, "end_date": start},
            headers=member_headers,
        )
        assert created.json()["status"] == "approved"
        assert (await c.get("/api/v1/notifications", headers=owner_headers)).json()["total"] == 0

    assert await _events(t, "leave.requested") == []


# --------------------------------------------------------------------------- #
# time
# --------------------------------------------------------------------------- #
async def test_approving_time_notifies_the_owner_once_for_the_batch(client_for) -> None:
    t = await make_tenant("emit-time")
    member = await _member(t, "logger@example.com")
    owner_headers = await auth_cookie(t.user)
    member_headers = await auth_cookie(member)
    now = datetime.now(UTC)

    async with client_for(t.host) as c:
        entries = []
        for offset, minutes in ((3, 30), (2, 45)):
            created = await c.post(
                "/api/v1/time/entries",
                json={
                    "started_at": (now - timedelta(hours=offset)).isoformat(),
                    "minutes": minutes,
                    "description": "Work",
                },
                headers=member_headers,
            )
            assert created.status_code == 201
            entries.append(created.json()["id"])

        approved = await c.post(
            "/api/v1/time/entries/approve",
            json={"entry_ids": entries, "approved": True},
            headers=owner_headers,
        )
        assert approved.status_code == 200 and approved.json()["updated"] == 2

    inbox = await _inbox(t, member.id)
    assert [event for event, _ in inbox] == ["time.entry_approved"], "one line, not one per entry"
    payload = inbox[0][1]
    assert payload["count"] == 2
    assert payload["minutes"] == 75


async def test_unapproving_time_notifies_nobody(client_for) -> None:
    t = await make_tenant("emit-time-unapprove")
    member = await _member(t, "revoked@example.com")
    owner_headers = await auth_cookie(t.user)
    member_headers = await auth_cookie(member)
    now = datetime.now(UTC)

    async with client_for(t.host) as c:
        entry = (
            await c.post(
                "/api/v1/time/entries",
                json={"started_at": (now - timedelta(hours=1)).isoformat(), "minutes": 15},
                headers=member_headers,
            )
        ).json()["id"]
        await c.post(
            "/api/v1/time/entries/approve",
            json={"entry_ids": [entry], "approved": False},
            headers=owner_headers,
        )

    assert await _events(t, "time.entry_approved") == []
