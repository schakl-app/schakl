"""time module API coverage (CLAUDE.md §6, §10): timer, manual entries, summary, timesheet."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from tests.conftest import auth_cookie, make_tenant


async def test_timer_start_stop(client_for) -> None:
    t = await make_tenant("time-timer")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        started = await c.post("/api/v1/time/timer/start", json={}, headers=headers)
        assert started.status_code == 201
        assert started.json()["ended_at"] is None

        current = await c.get("/api/v1/time/timer", headers=headers)
        assert current.json() is not None

        stopped = await c.post("/api/v1/time/timer/stop", headers=headers)
        assert stopped.status_code == 200
        assert stopped.json()["ended_at"] is not None

        # No running timer now.
        assert (await c.get("/api/v1/time/timer", headers=headers)).json() is None


async def test_starting_new_timer_stops_previous(client_for) -> None:
    t = await make_tenant("time-switch")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        first = (await c.post("/api/v1/time/timer/start", json={}, headers=headers)).json()
        second = (await c.post("/api/v1/time/timer/start", json={}, headers=headers)).json()

        # Only the second is running; the first was auto-stopped.
        first_after = await c.get(f"/api/v1/time/entries/{first['id']}", headers=headers)
        assert first_after.json()["ended_at"] is not None
        running = await c.get("/api/v1/time/timer", headers=headers)
        assert running.json()["id"] == second["id"]


async def test_manual_entry_and_summary(client_for) -> None:
    t = await make_tenant("time-manual")
    headers = await auth_cookie(t.user)
    now = datetime.now(UTC)
    async with client_for(t.host) as c:
        created = await c.post(
            "/api/v1/time/entries",
            json={"started_at": now.isoformat(), "minutes": 30, "description": "Design"},
            headers=headers,
        )
        assert created.status_code == 201
        entry = created.json()
        assert entry["minutes"] == 30
        assert entry["ended_at"] is not None

        summary = await c.get(
            "/api/v1/time/summary", params={"date": now.date().isoformat()}, headers=headers
        )
        assert summary.status_code == 200
        assert summary.json()["minutes"] == 30


async def test_timesheet_grid(client_for) -> None:
    t = await make_tenant("time-sheet")
    headers = await auth_cookie(t.user)
    now = datetime.now(UTC)
    week_start = now.date()
    async with client_for(t.host) as c:
        await c.post(
            "/api/v1/time/entries",
            json={"started_at": now.isoformat(), "minutes": 45},
            headers=headers,
        )
        sheet = await c.get(
            "/api/v1/time/timesheet",
            params={"week_start": week_start.isoformat()},
            headers=headers,
        )
        assert sheet.status_code == 200
        data = sheet.json()
        assert len(data["days"]) == 7
        assert data["total"] == 45
        assert data["day_totals"][0] == 45


async def test_start_end_with_break_derives_minutes(client_for) -> None:
    t = await make_tenant("time-startend")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        project = await c.post("/api/v1/projects", json={"name": "P"}, headers=headers)
        project_id = project.json()["id"]
        start = datetime(2026, 7, 7, 9, 0, tzinfo=UTC)
        end = datetime(2026, 7, 7, 11, 0, tzinfo=UTC)
        created = await c.post(
            "/api/v1/time/entries",
            json={
                "started_at": start.isoformat(),
                "ended_at": end.isoformat(),
                "break_minutes": 15,
                "project_id": project_id,
                "billable": True,
            },
            headers=headers,
        )
        assert created.status_code == 201
        entry = created.json()
        # 2h span − 15m break = 105 worked minutes.
        assert entry["minutes"] == 105
        assert entry["project_id"] == project_id
        assert entry["is_running"] is False


async def test_day_view(client_for) -> None:
    t = await make_tenant("time-day")
    headers = await auth_cookie(t.user)
    day = datetime(2026, 7, 7, 9, 0, tzinfo=UTC)
    async with client_for(t.host) as c:
        await c.post(
            "/api/v1/time/entries",
            json={"started_at": day.isoformat(), "minutes": 60, "billable": True},
            headers=headers,
        )
        await c.post(
            "/api/v1/time/entries",
            json={"started_at": day.isoformat(), "minutes": 30, "billable": False},
            headers=headers,
        )
        view = await c.get("/api/v1/time/day", params={"date": "2026-07-07"}, headers=headers)
        assert view.status_code == 200
        body = view.json()
        assert len(body["entries"]) == 2
        assert body["total_minutes"] == 90
        assert body["billable_minutes"] == 60


async def test_logged_by_project(client_for) -> None:
    t = await make_tenant("time-logged")
    headers = await auth_cookie(t.user)
    now = datetime(2026, 7, 7, 9, 0, tzinfo=UTC)
    async with client_for(t.host) as c:
        proj = await c.post("/api/v1/projects", json={"name": "Burn"}, headers=headers)
        pid = proj.json()["id"]
        await c.post(
            "/api/v1/time/entries",
            json={
                "started_at": now.isoformat(),
                "minutes": 120,
                "project_id": pid,
                "billable": True,
            },
            headers=headers,
        )
        await c.post(
            "/api/v1/time/entries",
            json={
                "started_at": now.isoformat(),
                "minutes": 30,
                "project_id": pid,
                "billable": False,
            },
            headers=headers,
        )
        logged = await c.get("/api/v1/time/logged", params={"project_id": pid}, headers=headers)
        assert logged.status_code == 200
        assert logged.json()["minutes"] == 150
        assert logged.json()["billable_minutes"] == 120


async def test_member_cannot_read_other_users_time(client_for) -> None:
    t = await make_tenant("time-scope", role="member")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        r = await c.get(
            "/api/v1/time/entries", params={"user_id": str(uuid.uuid4())}, headers=headers
        )
        assert r.status_code == 403


async def test_time_tenant_isolation(client_for) -> None:
    a = await make_tenant("time-iso-a")
    b = await make_tenant("time-iso-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)
    now = datetime.now(UTC)

    async with client_for(a.host) as ca:
        created = await ca.post(
            "/api/v1/time/entries",
            json={"started_at": now.isoformat(), "minutes": 15},
            headers=a_headers,
        )
        a_entry_id = created.json()["id"]

    async with client_for(b.host) as cb:
        assert (
            await cb.get("/api/v1/time/entries", headers=b_headers)
        ).json()["total"] == 0
        assert (
            await cb.get(f"/api/v1/time/entries/{a_entry_id}", headers=b_headers)
        ).status_code == 404


async def test_timesheet_rows_keyed_by_company_project_task(client_for) -> None:
    t = await make_tenant("time-projrows")
    headers = await auth_cookie(t.user)
    now = datetime.now(UTC)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Rows Co"}, headers=headers)
        ).json()
        p1 = (
            await c.post(
                "/api/v1/projects",
                json={"name": "Site", "company_id": company["id"]},
                headers=headers,
            )
        ).json()
        p2 = (
            await c.post(
                "/api/v1/projects",
                json={"name": "Ads", "company_id": company["id"]},
                headers=headers,
            )
        ).json()
        for project_id, minutes in ((p1["id"], 30), (p2["id"], 60)):
            await c.post(
                "/api/v1/time/entries",
                json={
                    "started_at": now.isoformat(),
                    "minutes": minutes,
                    "company_id": company["id"],
                    "project_id": project_id,
                },
                headers=headers,
            )
        sheet = (
            await c.get(
                "/api/v1/time/timesheet",
                params={"week_start": now.date().isoformat()},
                headers=headers,
            )
        ).json()
        # Same company, two projects → two rows carrying project_id.
        assert len(sheet["rows"]) == 2
        assert {row["project_id"] for row in sheet["rows"]} == {p1["id"], p2["id"]}
        assert sheet["total"] == 90


async def test_entry_types_tenant_configurable(client_for) -> None:
    """#176: types seed lazily per org (work, email), an entry can optionally carry one,
    an unknown/inactive key is refused, an in-use type refuses deletion, the list filters
    by type, and the catalog is tenant-isolated."""
    t = await make_tenant("time-types-a")
    other = await make_tenant("time-types-b")
    headers = await auth_cookie(t.user)
    other_headers = await auth_cookie(other.user)
    base = {
        "started_at": "2026-07-06T09:00:00Z",
        "ended_at": "2026-07-06T10:00:00Z",
    }
    async with client_for(t.host) as c:
        types = (await c.get("/api/v1/time/entry-types", headers=headers)).json()
        assert {et["key"] for et in types} == {"work", "email"}

        typed = await c.post(
            "/api/v1/time/entries", json={**base, "entry_type_key": "email"}, headers=headers
        )
        assert typed.status_code == 201, typed.text
        assert typed.json()["entry_type_key"] == "email"
        untyped = await c.post("/api/v1/time/entries", json=base, headers=headers)
        assert untyped.status_code == 201
        assert untyped.json()["entry_type_key"] is None
        assert (
            await c.post(
                "/api/v1/time/entries", json={**base, "entry_type_key": "nope"}, headers=headers
            )
        ).status_code == 422

        # The list filters by type.
        filtered = (
            await c.get("/api/v1/time/entries", params={"entry_type": "email"}, headers=headers)
        ).json()
        assert filtered["total"] == 1

        # In use → deletion refused; deactivation hides it from new writes but an entry
        # keeps its retired type through edits.
        email_type = next(et for et in types if et["key"] == "email")
        assert (
            await c.delete(f"/api/v1/time/entry-types/{email_type['id']}", headers=headers)
        ).status_code == 409
        assert (
            await c.patch(
                f"/api/v1/time/entry-types/{email_type['id']}",
                json={"active": False},
                headers=headers,
            )
        ).status_code == 200
        assert (
            await c.post(
                "/api/v1/time/entries", json={**base, "entry_type_key": "email"}, headers=headers
            )
        ).status_code == 422
        assert (
            await c.patch(
                f"/api/v1/time/entries/{typed.json()['id']}",
                json={"entry_type_key": "email", "description": "nog steeds"},
                headers=headers,
            )
        ).status_code == 200

    # Tenant isolation: the other org seeds its own defaults; ids never cross.
    async with client_for(other.host) as cb:
        other_types = (await cb.get("/api/v1/time/entry-types", headers=other_headers)).json()
        assert {et["key"] for et in other_types} == {"work", "email"}
        assert (
            await cb.patch(
                f"/api/v1/time/entry-types/{email_type['id']}",
                json={"active": True},
                headers=other_headers,
            )
        ).status_code == 404
