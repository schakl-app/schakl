"""Task scheduling API (#188): CRUD, tenant isolation, :own/:any scoping, confirm-to-log.

A schedule is a planned time block for a task on someone's calendar. It is org-scoped and
RLS-forced like every domain row; its write is scoped (a member plans their own time, a manager
schedules anyone); the client works in local date + start time + length and the API owns the
timezone; and a passed block logs itself as a real time entry exactly once.
"""

from __future__ import annotations

import uuid

from app.core.auth.models import User
from tests.conftest import auth_cookie, make_tenant

# A fixed weekday well clear of any calendar edge; scheduling has no holiday logic (unlike leave),
# so any local day works — pin it so the range window is deterministic.
_DAY = "2026-07-20"


def _block(**over) -> dict:
    body = {"day": _DAY, "start_time": "09:00", "duration_minutes": 180}
    body.update(over)
    return body


async def _invite_member(client, headers, email: str) -> User:
    res = await client.post(
        "/api/v1/members/invite",
        json={"email": email, "full_name": "Mel Member", "role": "member"},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return User(
        id=uuid.UUID(res.json()["user_id"]), email=email, hashed_password="", is_active=True
    )


async def _make_task(client, headers, *, assignee: uuid.UUID | None = None) -> str:
    body: dict = {"title": "Redesign homepage", "allocated_minutes": 180}
    if assignee is not None:
        body["assignee_user_id"] = str(assignee)
    res = await client.post("/api/v1/tasks", json=body, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def test_schedule_crud_move_and_log(client_for) -> None:
    t = await make_tenant("sched-crud")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        task_id = await _make_task(c, headers, assignee=t.user.id)

        # Create: user_id omitted → defaults to the task's assignee (the owner here).
        created = await c.post(
            "/api/v1/tasks/schedules", json=_block(task_id=task_id), headers=headers
        )
        assert created.status_code == 201, created.text
        block = created.json()
        assert block["user_id"] == str(t.user.id)
        assert block["time_entry_id"] is None
        # 09:00–12:00 local is a 3-hour instant span.
        assert block["starts_at"] < block["ends_at"]

        # The range feed decorates with the task + person (one fetch).
        rows = (
            await c.get(
                f"/api/v1/tasks/schedules?date_from={_DAY}&date_to={_DAY}", headers=headers
            )
        ).json()
        assert len(rows) == 1
        assert rows[0]["task_title"] == "Redesign homepage"
        assert rows[0]["allocated_minutes"] == 180

        # Move it to the next day, keeping the time (server-authoritative).
        moved = await c.patch(
            f"/api/v1/tasks/schedules/{block['id']}", json={"day": "2026-07-21"}, headers=headers
        )
        assert moved.status_code == 200, moved.text
        assert (
            await c.get(f"/api/v1/tasks/schedules?date_from={_DAY}&date_to={_DAY}", headers=headers)
        ).json() == []  # it left the original day

        # Confirm-to-log: creates a real time entry and links it; a second log is refused.
        logged = await c.post(
            f"/api/v1/tasks/schedules/{block['id']}/log-time",
            json={"description": "Worked the block", "billable": True},
            headers=headers,
        )
        assert logged.status_code == 200, logged.text
        assert logged.json()["time_entry_id"] is not None
        again = await c.post(
            f"/api/v1/tasks/schedules/{block['id']}/log-time", json={}, headers=headers
        )
        assert again.status_code == 409

        # The linked entry really exists on the timesheet side.
        entries = await c.get("/api/v1/time/entries", headers=headers)
        assert any(e["task_id"] == task_id for e in entries.json()["items"])

        # Delete removes the block.
        assert (
            await c.delete(f"/api/v1/tasks/schedules/{block['id']}", headers=headers)
        ).status_code == 204


async def test_schedule_duration_validation(client_for) -> None:
    t = await make_tenant("sched-window")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        task_id = await _make_task(c, headers, assignee=t.user.id)
        zero = await c.post(
            "/api/v1/tasks/schedules", json=_block(task_id=task_id, duration_minutes=0),
            headers=headers,
        )
        assert zero.status_code == 422
        too_long = await c.post(
            "/api/v1/tasks/schedules", json=_block(task_id=task_id, duration_minutes=2000),
            headers=headers,
        )
        assert too_long.status_code == 422


async def test_schedule_tenant_isolation(client_for) -> None:
    """One org's block is invisible and untouchable from another org (Golden Rule 1)."""
    a = await make_tenant("sched-a")
    b = await make_tenant("sched-b")
    ha, hb = await auth_cookie(a.user), await auth_cookie(b.user)
    async with client_for(a.host) as ca, client_for(b.host) as cb:
        task_id = await _make_task(ca, ha, assignee=a.user.id)
        block = (
            await ca.post("/api/v1/tasks/schedules", json=_block(task_id=task_id), headers=ha)
        ).json()

        # B cannot read it in a range, nor fetch/patch/delete it by id.
        assert (
            await cb.get(f"/api/v1/tasks/schedules?date_from={_DAY}&date_to={_DAY}", headers=hb)
        ).json() == []
        got = await cb.get(f"/api/v1/tasks/schedules/{block['id']}", headers=hb)
        assert got.status_code == 404
        assert (
            await cb.patch(
                f"/api/v1/tasks/schedules/{block['id']}", json={"note": "hijack"}, headers=hb
            )
        ).status_code == 404
        assert (
            await cb.delete(f"/api/v1/tasks/schedules/{block['id']}", headers=hb)
        ).status_code == 404


async def test_schedule_own_vs_any_scoping(client_for) -> None:
    """A member (``:own``) plans their own time only; scheduling someone else needs ``:any``,
    and one member never sees another's block."""
    t = await make_tenant("sched-scope")
    owner_headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _invite_member(c, owner_headers, "mel@example.com")
        member_headers = await auth_cookie(member)

        # A task assigned to the member; scheduling it for themselves is allowed.
        task_id = await _make_task(c, owner_headers, assignee=member.id)
        mine = await c.post(
            "/api/v1/tasks/schedules", json=_block(task_id=task_id), headers=member_headers
        )
        assert mine.status_code == 201, mine.text

        # Scheduling that task for the owner (someone else) is refused without :any.
        for_owner = await c.post(
            "/api/v1/tasks/schedules",
            json=_block(task_id=task_id, user_id=str(t.user.id)),
            headers=member_headers,
        )
        assert for_owner.status_code == 403

        # The owner (holds ``*`` ⇒ :any) may schedule anyone and sees the member's block.
        for_member = await c.post(
            "/api/v1/tasks/schedules",
            json=_block(task_id=task_id, user_id=str(member.id), start_time="13:00"),
            headers=owner_headers,
        )
        assert for_member.status_code == 201, for_member.text

        # Scheduling for the member notifies *them* (actor excluded, so the member's own earlier
        # self-schedule was silent): the whole emit → fan-out → inbox chain lands one bell item.
        unread = await c.get("/api/v1/notifications/unread-count", headers=member_headers)
        assert unread.json()["count"] >= 1
        feed = await c.get("/api/v1/notifications", headers=member_headers)
        assert any(n["event_type"] == "task.scheduled" for n in feed.json()["items"])

        owner_view = await c.get(
            f"/api/v1/tasks/schedules?date_from={_DAY}&date_to={_DAY}&user_ids={member.id}",
            headers=owner_headers,
        )
        assert len(owner_view.json()) == 2

        # The member's own personal feed shows only their own two blocks, never the owner's roster.
        member_view = await c.get(
            f"/api/v1/tasks/schedules?date_from={_DAY}&date_to={_DAY}", headers=member_headers
        )
        assert {row["user_id"] for row in member_view.json()} == {str(member.id)}
        # A member asking for someone else's feed by id is refused.
        denied = await c.get(
            f"/api/v1/tasks/schedules?date_from={_DAY}&date_to={_DAY}&user_ids={t.user.id}",
            headers=member_headers,
        )
        assert denied.status_code == 403
