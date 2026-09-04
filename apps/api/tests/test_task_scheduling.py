"""Task scheduling API (#188): CRUD, tenant isolation, :own/:any scoping, confirm-to-log.

A schedule is a planned time block for a task on someone's calendar. It is org-scoped and
RLS-forced like every domain row; its write is scoped (a member plans their own time, a manager
schedules anyone); the client works in local date + start time + length and the API owns the
timezone; and a passed block logs itself as a real time entry exactly once.
"""

from __future__ import annotations

import uuid

from app.core.auth.models import User
from tests.conftest import FAR_FUTURE_DUE, auth_cookie, default_company, make_tenant

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
    body: dict = {
        "title": "Redesign homepage",
        "allocated_minutes": 180,
        "due_date": FAR_FUTURE_DUE,
    }
    if assignee is not None:
        body["assignee_user_id"] = str(assignee)
    body.setdefault("company_id", await default_company(client, headers))
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
            await c.get(f"/api/v1/tasks/schedules?date_from={_DAY}&date_to={_DAY}", headers=headers)
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
            "/api/v1/tasks/schedules",
            json=_block(task_id=task_id, duration_minutes=0),
            headers=headers,
        )
        assert zero.status_code == 422
        too_long = await c.post(
            "/api/v1/tasks/schedules",
            json=_block(task_id=task_id, duration_minutes=2000),
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


async def test_feed_drops_a_deactivated_members_blocks_and_the_task_panel_keeps_them(
    client_for,
) -> None:
    """#439: the calendar feed stops drawing a departed colleague the moment the roster menus
    stop offering them — while the task page's own panel (``task_id``) keeps the record."""
    t = await make_tenant("sched-deactivated")
    owner_headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        invited = await c.post(
            "/api/v1/members/invite",
            json={"email": "weg@example.com", "full_name": "Weg Gegaan", "role": "member"},
            headers=owner_headers,
        )
        assert invited.status_code == 201, invited.text
        member_id = invited.json()["user_id"]
        membership_id = invited.json()["membership_id"]

        task_id = await _make_task(c, owner_headers, assignee=uuid.UUID(member_id))
        theirs = await c.post(
            "/api/v1/tasks/schedules",
            json=_block(task_id=task_id, user_id=member_id),
            headers=owner_headers,
        )
        assert theirs.status_code == 201, theirs.text
        ours = await c.post(
            "/api/v1/tasks/schedules",
            json=_block(task_id=task_id, user_id=str(t.user.id), start_time="13:00"),
            headers=owner_headers,
        )
        assert ours.status_code == 201, ours.text

        feed = f"/api/v1/tasks/schedules?date_from={_DAY}&date_to={_DAY}"
        both = f"{feed}&user_ids={member_id}&user_ids={t.user.id}"
        before = (await c.get(both, headers=owner_headers)).json()
        assert {row["user_id"] for row in before} == {member_id, str(t.user.id)}

        off = await c.patch(
            f"/api/v1/members/{membership_id}/account",
            json={"active": False},
            headers=owner_headers,
        )
        assert off.status_code == 200, off.text

        # The feed no longer draws their block — even when the URL still names them.
        after = (await c.get(both, headers=owner_headers)).json()
        assert {row["user_id"] for row in after} == {str(t.user.id)}

        # The task page's panel is a record surface and keeps the planned block.
        panel = (
            await c.get(f"/api/v1/tasks/schedules?task_id={task_id}", headers=owner_headers)
        ).json()
        assert {row["user_id"] for row in panel} == {member_id, str(t.user.id)}


async def test_schedule_batch_books_one_block_per_person_or_nobody(client_for) -> None:
    """Scheduling a task for several people writes one personal block each, judged by the same
    ``:own``/``:any`` rule as a single block — and a refusal for one person books nobody."""
    t = await make_tenant("sched-batch")
    owner_headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _invite_member(c, owner_headers, "mel@example.com")
        member_headers = await auth_cookie(member)
        task_id = await _make_task(c, owner_headers, assignee=member.id)

        # A member may name themselves — but a colleague beside them is refused as a whole, so
        # the member's own row is not written either (all or nothing).
        half = await c.post(
            "/api/v1/tasks/schedules/batch",
            json=_block(task_id=task_id, user_ids=[str(member.id), str(t.user.id)]),
            headers=member_headers,
        )
        assert half.status_code == 403, half.text
        none_yet = await c.get(f"/api/v1/tasks/schedules?task_id={task_id}", headers=owner_headers)
        assert none_yet.json() == []

        # Nobody named is a validation error, not an empty success.
        empty = await c.post(
            "/api/v1/tasks/schedules/batch",
            json=_block(task_id=task_id, user_ids=[]),
            headers=owner_headers,
        )
        assert empty.status_code == 422

        # The owner (:any) books both — a person named twice is booked once — and the answer is
        # every block written, in the order they were named.
        both = await c.post(
            "/api/v1/tasks/schedules/batch",
            json=_block(
                task_id=task_id,
                user_ids=[str(t.user.id), str(member.id), str(t.user.id)],
                note="kick-off",
            ),
            headers=owner_headers,
        )
        assert both.status_code == 201, both.text
        blocks = both.json()
        assert [b["user_id"] for b in blocks] == [str(t.user.id), str(member.id)]
        assert len({b["id"] for b in blocks}) == 2
        assert all(b["note"] == "kick-off" for b in blocks)
        assert all(b["starts_at"] == blocks[0]["starts_at"] for b in blocks)

        # Each person's block rides the same feed and the same notification as a single one:
        # the member (not the actor) is told their task was planned.
        panel = (
            await c.get(f"/api/v1/tasks/schedules?task_id={task_id}", headers=owner_headers)
        ).json()
        assert {row["user_id"] for row in panel} == {str(t.user.id), str(member.id)}
        feed = await c.get("/api/v1/notifications", headers=member_headers)
        assert any(n["event_type"] == "task.scheduled" for n in feed.json()["items"])


# --------------------------------------------------------------------------- #
# The conflict check behind Inplannen: `GET /tasks/schedules/busy` (app/core/busy.py)
# --------------------------------------------------------------------------- #
async def _membership_id(client, headers, user_id: uuid.UUID) -> str:
    members = (await client.get("/api/v1/members", headers=headers)).json()
    rows = members["items"] if isinstance(members, dict) else members
    return next(row["membership_id"] for row in rows if row["user_id"] == str(user_id))


async def _busy(client, headers, *user_ids: uuid.UUID, day: str = _DAY):
    query = "&".join(f"user_ids={user_id}" for user_id in user_ids)
    return await client.get(
        f"/api/v1/tasks/schedules/busy?date_from={day}&date_to={day}&{query}", headers=headers
    )


async def test_busy_is_gated_on_the_write_and_titled_by_the_read(client_for) -> None:
    """Being allowed to book someone is the reason to see when they are taken; being allowed
    to *read* their planning is a different key, and only it names what takes the time."""
    t = await make_tenant("sched-busy")
    owner_h = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _invite_member(c, owner_h, "mel@example.com")
        member_h = await auth_cookie(member)
        task_id = await _make_task(c, owner_h, assignee=member.id)
        assert (
            await c.post(
                "/api/v1/tasks/schedules",
                json=_block(task_id=task_id, user_id=str(member.id)),
                headers=owner_h,
            )
        ).status_code == 201

        # A member may ask about themselves — and sees their own block by name.
        own = await _busy(c, member_h, member.id)
        assert own.status_code == 200, own.text
        assert [row["title"] for row in own.json()["items"]] == ["Redesign homepage"]
        assert own.json()["items"][0]["ref"] is not None
        assert "tasks.schedule" in own.json()["sources"]
        # …and about nobody else: no write:any, no answer, not even a window.
        assert (await _busy(c, member_h, t.user.id)).status_code == 403

        # A planner who may book anyone but read nobody's planning gets the window, unnamed.
        planner = await _invite_member(c, owner_h, "plan@example.com")
        planner_h = await auth_cookie(planner)
        role = await c.post(
            "/api/v1/roles",
            json={
                "key": "planner",
                "name_i18n": {"en": "Planner"},
                "permissions": [
                    "tasks.task.read",
                    "tasks.schedule.write:any",
                    "tasks.schedule.read:own",
                ],
            },
            headers=owner_h,
        )
        assert role.status_code == 201, role.text
        mid = await _membership_id(c, owner_h, planner.id)
        assert (
            await c.put(
                f"/api/v1/members/{mid}/roles",
                json={"role_ids": [role.json()["id"]]},
                headers=owner_h,
            )
        ).status_code == 200
        seen = await _busy(c, planner_h, member.id)
        assert seen.status_code == 200, seen.text
        [item] = seen.json()["items"]
        assert item["user_id"] == str(member.id)
        assert item["starts_at"].startswith(_DAY)
        assert item["title"] is None and item["ref"] is None and item["href"] is None

        # The owner holds the read at :any and gets the name.
        named = await _busy(c, owner_h, member.id)
        assert named.json()["items"][0]["title"] == "Redesign homepage"

        # A window is bounded: a month is a lot of calendar, a year is a dump.
        too_wide = await c.get(
            f"/api/v1/tasks/schedules/busy?date_from=2026-01-01&date_to=2026-03-01"
            f"&user_ids={member.id}",
            headers=owner_h,
        )
        assert too_wide.status_code == 422


async def test_busy_folds_leave_and_the_google_mirror_in(client_for) -> None:
    """Three modules, one answer: an absence is an ``away`` band, and a colleague's Google
    appointment is a window with no title — the free/busy answer."""
    from datetime import UTC, datetime

    from app.core.crypto import encrypt
    from app.db import async_session_maker, set_current_org
    from app.integrations.google.calendar.models import GoogleCalendarEvent
    from app.integrations.google.models import GoogleConnection
    from app.integrations.google.oauth import SCOPE_CALENDAR
    from tests.conftest import leave_workday

    t = await make_tenant("sched-busy-mix")
    owner_h = await auth_cookie(t.user)
    day = leave_workday(3)
    async with client_for(t.host) as c:
        member = await _invite_member(c, owner_h, "mel@example.com")
        member_h = await auth_cookie(member)
        types = (await c.get("/api/v1/leave/types", headers=member_h)).json()
        leave = await c.post(
            "/api/v1/leave/requests",
            json={
                "leave_type_id": types[0]["id"],
                "start_date": day.isoformat(),
                "end_date": day.isoformat(),
            },
            headers=member_h,
        )
        assert leave.status_code == 201, leave.text

        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            connection = GoogleConnection(
                org_id=t.org.id,
                user_id=member.id,
                google_sub="sub-mel",
                email="mel@agency.nl",
                scopes=["openid", "email", SCOPE_CALENDAR],
                refresh_token_encrypted=encrypt("rt"),
            )
            session.add(connection)
            await session.flush()
            session.add(
                GoogleCalendarEvent(
                    org_id=t.org.id,
                    connection_id=connection.id,
                    google_event_id="evt-1",
                    summary="Tandarts",
                    start_at=datetime(day.year, day.month, day.day, 8, 0, tzinfo=UTC),
                    end_at=datetime(day.year, day.month, day.day, 9, 0, tzinfo=UTC),
                )
            )
            await session.commit()

        seen = await _busy(c, owner_h, member.id, day=day.isoformat())
        assert seen.status_code == 200, seen.text
        by_source = {row["source"]: row for row in seen.json()["items"]}
        assert set(by_source) == {"leave", "google.calendar"}
        assert by_source["leave"]["kind"] == "away"
        assert by_source["leave"]["all_day"] is True
        assert by_source["leave"]["tentative"] is True  # still pending
        # The owner may read the team's leave, so the *type* is named…
        assert by_source["leave"]["title"]
        # …while the colleague's diary is a window and nothing more, whoever asks.
        assert by_source["google.calendar"]["title"] is None
        assert by_source["google.calendar"]["href"] is None
        # Its owner sees their own appointment by name.
        mine = await _busy(c, member_h, member.id, day=day.isoformat())
        own_google = next(r for r in mine.json()["items"] if r["source"] == "google.calendar")
        assert own_google["title"] == "Tandarts"
