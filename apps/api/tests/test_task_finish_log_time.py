"""Finishing a task offers to log the hours it took (#314).

Recording the hours and finishing the task are one act and therefore one transaction: a task
that finished while its entry failed is exactly the outcome the feature exists to prevent. The
properties worth pinning are the ones no functional assertion on the JSON would catch — that it
is refused unless the update is a *finish*, that it needs `time.entry.write` in its own right,
and that hours confirmed off a planned block (#188) can never be booked a second time.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.core.auth.models import User
from app.core.tenancy import set_current_org
from app.db import async_session_maker
from tests.conftest import FAR_FUTURE_DUE, _password_hash, add_membership, auth_cookie, make_tenant

# A fixed weekday, like the scheduling suite: nothing here has holiday logic, but the window
# has to be deterministic. Times are the time module's wall-clock-as-UTC convention.
_DAY = "2026-07-20"
_START = f"{_DAY}T09:00:00Z"
_END = f"{_DAY}T11:30:00Z"


async def _finished_key(client, headers) -> str:
    statuses = (await client.get("/api/v1/tasks/statuses", headers=headers)).json()
    return next(s["key"] for s in statuses if s["is_terminal"])


async def _open_key(client, headers) -> str:
    statuses = (await client.get("/api/v1/tasks/statuses", headers=headers)).json()
    return next(s["key"] for s in statuses if not s["is_terminal"])


async def _task(client, headers, **over) -> dict:
    body = {"title": "Homepage herzien", "due_date": FAR_FUTURE_DUE}
    body.update(over)
    res = await client.post("/api/v1/tasks", json=body, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


async def test_finishing_a_task_logs_the_hours_in_the_same_request(client_for) -> None:
    """One PATCH: the status moves, `completed_at` is stamped, and the entry exists — carrying
    the task's own client/project and, with no description given, its title."""
    t = await make_tenant("task-logtime")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Klant BV"}, headers=headers)
        ).json()
        project = (
            await c.post(
                "/api/v1/projects",
                json={"name": "Website", "company_id": company["id"]},
                headers=headers,
            )
        ).json()
        task = await _task(c, headers, company_id=company["id"], project_id=project["id"])

        done = await _finished_key(c, headers)
        res = await c.patch(
            f"/api/v1/tasks/{task['id']}",
            json={"status": done, "log_time": {"started_at": _START, "ended_at": _END}},
            headers=headers,
        )
        assert res.status_code == 200, res.text
        assert res.json()["status"] == done
        assert res.json()["completed_at"] is not None

        entries = (await c.get("/api/v1/time/entries", headers=headers)).json()
        assert entries["total"] == 1
        entry = entries["items"][0]
        assert entry["task_id"] == task["id"]
        assert entry["project_id"] == project["id"]
        assert entry["company_id"] == company["id"]
        assert entry["minutes"] == 150
        # No description given → the task's title, never a blank timesheet row.
        assert entry["description"] == "Homepage herzien"

        # And the task's own budget reads it back: the number the finish prompt suggests from
        # is the number the entry moved.
        detail = (await c.get(f"/api/v1/tasks/{task['id']}", headers=headers)).json()
        assert detail["logged_minutes"] == 150


async def test_log_time_is_refused_on_anything_that_is_not_a_finish(client_for) -> None:
    """A completion ride-along, not a second way to write a time entry through PATCH.

    Three shapes that all reach the same route and must all be refused: an ordinary field edit,
    a move between two open statuses, and a re-finish of a task that is already finished (whose
    `completed_at` the service leaves alone — so "the status field is a terminal key" is not
    the same question as "this update finishes the task").
    """
    t = await make_tenant("task-logtime-guard")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        task = await _task(c, headers)
        done = await _finished_key(c, headers)
        open_key = await _open_key(c, headers)
        ride = {"log_time": {"started_at": _START, "ended_at": _END}}

        retitle = await c.patch(
            f"/api/v1/tasks/{task['id']}", json={"title": "Anders", **ride}, headers=headers
        )
        assert retitle.status_code == 422, retitle.text
        assert retitle.json()["error"]["fields"] == {
            "log_time": "errors.tasks_log_time_not_finishing"
        }

        staying_open = await c.patch(
            f"/api/v1/tasks/{task['id']}", json={"status": open_key, **ride}, headers=headers
        )
        assert staying_open.status_code == 422, staying_open.text

        assert (
            await c.patch(f"/api/v1/tasks/{task['id']}", json={"status": done}, headers=headers)
        ).status_code == 200
        again = await c.patch(
            f"/api/v1/tasks/{task['id']}", json={"status": done, **ride}, headers=headers
        )
        assert again.status_code == 422, again.text

        # Nothing was written by any of the three, and the title never moved either: a refused
        # ride-along rolls the whole update back rather than half-applying it.
        assert (await c.get("/api/v1/time/entries", headers=headers)).json()["total"] == 0
        assert (await c.get(f"/api/v1/tasks/{task['id']}", headers=headers)).json()[
            "title"
        ] == "Homepage herzien"


async def test_logging_hours_needs_time_entry_write_and_takes_the_finish_with_it(
    client_for,
) -> None:
    """Writing a task is not writing a timesheet (§15), and the refusal is atomic.

    The member here holds `tasks.task.write:own` and no time permission at all. Finishing their
    own task must still work; finishing it *with hours* must refuse — and leave the task open,
    because a half-applied "finish without the hours" is the silent data loss this whole feature
    is about.
    """
    t = await make_tenant("task-logtime-perm")
    owner_headers = await auth_cookie(t.user)
    async with async_session_maker() as session:
        user = User(
            id=uuid.uuid4(),
            email="task-logtime-member@example.com",
            hashed_password=_password_hash.hash("secret1234"),
            is_active=True,
            is_verified=True,
        )
        session.add(user)
        await session.flush()
        await set_current_org(session, t.org.id)
        await add_membership(session, t.org.id, user.id, "member")
        await session.execute(
            text(
                "DELETE FROM role_permissions WHERE org_id = :org "
                "AND permission LIKE 'time.entry.write%'"
            ),
            {"org": t.org.id},
        )
        await session.commit()
        member = User(id=user.id, email=user.email, hashed_password="", is_active=True)
    member_headers = await auth_cookie(member)

    async with client_for(t.host) as c:
        task = await _task(c, owner_headers, assignee_user_id=str(member.id))
        done = await _finished_key(c, owner_headers)

        refused = await c.patch(
            f"/api/v1/tasks/{task['id']}",
            json={"status": done, "log_time": {"started_at": _START, "ended_at": _END}},
            headers=member_headers,
        )
        assert refused.status_code == 403, refused.text
        assert (await c.get(f"/api/v1/tasks/{task['id']}", headers=owner_headers)).json()[
            "status"
        ] != done

        # The same finish without the ride-along is theirs to make.
        plain = await c.patch(
            f"/api/v1/tasks/{task['id']}", json={"status": done}, headers=member_headers
        )
        assert plain.status_code == 200, plain.text


async def test_hours_confirmed_from_a_planned_block_are_booked_exactly_once(client_for) -> None:
    """#188's panel must stop offering the same afternoon the finish prompt just booked.

    Taking the offer with the block's id stamps `TaskSchedule.time_entry_id`, which is the
    only thing standing between "log the hours you planned" and "log them twice".
    """
    t = await make_tenant("task-logtime-block")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        task = await _task(c, headers, assignee_user_id=str(t.user.id))
        block = (
            await c.post(
                "/api/v1/tasks/schedules",
                json={
                    "task_id": task["id"],
                    "day": _DAY,
                    "start_time": "09:00",
                    "duration_minutes": 150,
                },
                headers=headers,
            )
        ).json()
        assert block["time_entry_id"] is None

        done = await _finished_key(c, headers)
        res = await c.patch(
            f"/api/v1/tasks/{task['id']}",
            json={
                "status": done,
                "log_time": {
                    "started_at": _START,
                    "ended_at": _END,
                    "schedule_id": block["id"],
                    "description": "Uitgelopen op de header",
                },
            },
            headers=headers,
        )
        assert res.status_code == 200, res.text

        entries = (await c.get("/api/v1/time/entries", headers=headers)).json()
        assert entries["total"] == 1
        assert entries["items"][0]["description"] == "Uitgelopen op de header"

        blocks = (
            await c.get(f"/api/v1/tasks/schedules?task_id={task['id']}", headers=headers)
        ).json()
        assert blocks[0]["time_entry_id"] == entries["items"][0]["id"]

        # The panel's own confirm-to-log now refuses, so the hours cannot be booked again.
        second = await c.post(
            f"/api/v1/tasks/schedules/{block['id']}/log-time", json={}, headers=headers
        )
        assert second.status_code == 409, second.text
        assert (await c.get("/api/v1/time/entries", headers=headers)).json()["total"] == 1


@pytest.mark.parametrize("field", ["already_logged", "other_task"])
async def test_a_block_that_cannot_be_claimed_refuses_the_whole_finish(
    client_for, field: str
) -> None:
    """The claim carries `log_time`'s own refusals, and each takes the finish down with it."""
    t = await make_tenant(f"task-logtime-claim-{field.replace('_', '-')}")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        task = await _task(c, headers, assignee_user_id=str(t.user.id))
        other = await _task(c, headers, title="Andere taak", assignee_user_id=str(t.user.id))
        target = other if field == "other_task" else task
        block = (
            await c.post(
                "/api/v1/tasks/schedules",
                json={
                    "task_id": target["id"],
                    "day": _DAY,
                    "start_time": "09:00",
                    "duration_minutes": 150,
                },
                headers=headers,
            )
        ).json()
        if field == "already_logged":
            assert (
                await c.post(
                    f"/api/v1/tasks/schedules/{block['id']}/log-time", json={}, headers=headers
                )
            ).status_code == 200

        done = await _finished_key(c, headers)
        res = await c.patch(
            f"/api/v1/tasks/{task['id']}",
            json={
                "status": done,
                "log_time": {
                    "started_at": _START,
                    "ended_at": _END,
                    "schedule_id": block["id"],
                },
            },
            headers=headers,
        )
        assert res.status_code == (409 if field == "already_logged" else 422), res.text
        assert (await c.get(f"/api/v1/tasks/{task['id']}", headers=headers)).json()[
            "status"
        ] != done


async def test_a_block_from_another_tenant_is_never_claimable(client_for) -> None:
    """Golden Rule 1: the id comes from the caller, so the lookup is tenant-scoped and a
    stranger's block is simply not there — 404, and the finish rolls back with it."""
    mine = await make_tenant("task-logtime-mine")
    theirs = await make_tenant("task-logtime-theirs")
    my_headers = await auth_cookie(mine.user)
    their_headers = await auth_cookie(theirs.user)

    async with client_for(theirs.host) as c:
        their_task = await _task(c, their_headers, assignee_user_id=str(theirs.user.id))
        their_block = (
            await c.post(
                "/api/v1/tasks/schedules",
                json={
                    "task_id": their_task["id"],
                    "day": _DAY,
                    "start_time": "09:00",
                    "duration_minutes": 150,
                },
                headers=their_headers,
            )
        ).json()

    async with client_for(mine.host) as c:
        task = await _task(c, my_headers, assignee_user_id=str(mine.user.id))
        done = await _finished_key(c, my_headers)
        res = await c.patch(
            f"/api/v1/tasks/{task['id']}",
            json={
                "status": done,
                "log_time": {
                    "started_at": _START,
                    "ended_at": _END,
                    "schedule_id": their_block["id"],
                },
            },
            headers=my_headers,
        )
        assert res.status_code == 404, res.text
        assert (await c.get("/api/v1/time/entries", headers=my_headers)).json()["total"] == 0

    async with client_for(theirs.host) as c:
        blocks = (
            await c.get(
                f"/api/v1/tasks/schedules?task_id={their_task['id']}", headers=their_headers
            )
        ).json()
        assert blocks[0]["time_entry_id"] is None


async def test_the_logged_entry_follows_the_project_on_billable(client_for) -> None:
    """#284, on the path that had no way of saying so: `billable` left out defers to the
    project, so finishing a task on a retainer-covered project bills nobody. A ride-along that
    quietly posted `true` would be the one write path that forgot."""
    t = await make_tenant("task-logtime-billable")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Klant BV"}, headers=headers)
        ).json()
        project = (
            await c.post(
                "/api/v1/projects",
                json={
                    "name": "Retainer",
                    "company_id": company["id"],
                    "billable_default": False,
                },
                headers=headers,
            )
        ).json()
        task = await _task(c, headers, company_id=company["id"], project_id=project["id"])
        done = await _finished_key(c, headers)
        assert (
            await c.patch(
                f"/api/v1/tasks/{task['id']}",
                json={"status": done, "log_time": {"started_at": _START, "ended_at": _END}},
                headers=headers,
            )
        ).status_code == 200

        entry = (await c.get("/api/v1/time/entries", headers=headers)).json()["items"][0]
        assert entry["billable"] is False

        # Stated by the caller, it stands — the deferral is what silence means, not a ceiling.
        other = await _task(
            c, headers, title="Los werk", company_id=company["id"], project_id=project["id"]
        )
        assert (
            await c.patch(
                f"/api/v1/tasks/{other['id']}",
                json={
                    "status": done,
                    "log_time": {
                        "started_at": _START,
                        "ended_at": _END,
                        "billable": True,
                    },
                },
                headers=headers,
            )
        ).status_code == 200
        rows = (await c.get("/api/v1/time/entries", headers=headers)).json()["items"]
        assert {r["task_id"]: r["billable"] for r in rows} == {
            task["id"]: False,
            other["id"]: True,
        }
