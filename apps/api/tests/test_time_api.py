"""time module API coverage (CLAUDE.md §6, §10): timer, manual entries, summary, timesheet."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.db import async_session_maker, set_current_org
from tests.conftest import add_membership, auth_cookie, make_tenant


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


async def test_entries_can_skip_count_for_lightweight_lookup(client_for) -> None:
    t = await make_tenant("time-no-count")
    headers = await auth_cookie(t.user)
    now = datetime.now(UTC)
    async with client_for(t.host) as c:
        for minutes in (15, 30):
            await c.post(
                "/api/v1/time/entries",
                json={"started_at": now.isoformat(), "minutes": minutes},
                headers=headers,
            )

        page = await c.get(
            "/api/v1/time/entries",
            params={"limit": 1, "count": "false"},
            headers=headers,
        )
        assert page.status_code == 200
        assert len(page.json()["items"]) == 1
        # With counting disabled, total intentionally describes the returned page.
        assert page.json()["total"] == 1


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


async def test_time_workspace_combines_week_day_timer_and_recent(client_for) -> None:
    t = await make_tenant("time-workspace")
    headers = await auth_cookie(t.user)
    day = datetime(2026, 7, 6, 9, 0, tzinfo=UTC)
    async with client_for(t.host) as c:
        entry = (
            await c.post(
                "/api/v1/time/entries",
                json={"started_at": day.isoformat(), "minutes": 45},
                headers=headers,
            )
        ).json()
        workspace = await c.get(
            "/api/v1/time/workspace",
            params={"week_start": "2026-07-06", "day": "2026-07-06"},
            headers=headers,
        )
        assert workspace.status_code == 200
        body = workspace.json()
        assert body["running"] is None
        assert body["week"]["total"] == 45
        assert body["day"]["total_minutes"] == 45
        assert body["day"]["entries"][0]["id"] == entry["id"]
        assert body["recent"]["id"] == entry["id"]


async def test_start_end_with_break_derives_minutes(client_for) -> None:
    t = await make_tenant("time-startend")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        klant = await c.post("/api/v1/companies", json={"name": "Klant"}, headers=headers)
        project = await c.post(
            "/api/v1/projects",
            json={"name": "P", "company_id": klant.json()["id"]},
            headers=headers,
        )
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
        klant = await c.post("/api/v1/companies", json={"name": "Klant"}, headers=headers)
        proj = await c.post(
            "/api/v1/projects",
            json={"name": "Burn", "company_id": klant.json()["id"]},
            headers=headers,
        )
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
        assert (await cb.get("/api/v1/time/entries", headers=b_headers)).json()["total"] == 0
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


async def test_hours_reach_an_agreement_through_its_project(client_for) -> None:
    """An entry can no longer be pinned to a subscription: included hours are consumed through
    the **projects** the agreement covers (#225), so there is exactly one answer to "how many
    hours are left" instead of two that can disagree.

    A posted ``subscription_id`` is ignored rather than honoured — the field is gone from the
    write schemas — and the same hours still land on the agreement via its linked project.
    """
    t = await make_tenant("time-sub-link")
    headers = await auth_cookie(t.user)
    # Both windows are "this month": the agreement's invoice period and the project's
    # (subscription-backed ⇒ monthly) budget period. The 15th at 09:00 UTC sits inside the
    # local month whatever the offset, so the test doesn't rot on the 1st.
    now = datetime.now(UTC)
    mid_month = now.replace(day=15, hour=9, minute=0, second=0, microsecond=0)
    period_start = mid_month.replace(day=1).date()
    period_end = (period_start.replace(day=28) + timedelta(days=7)).replace(day=1)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Retainerklant"}, headers=headers)
        ).json()
        project = (
            await c.post(
                "/api/v1/projects",
                json={"name": "Onderhoud", "company_id": company["id"]},
                headers=headers,
            )
        ).json()
        sub = (
            await c.post(
                "/api/v1/subscriptions",
                json={
                    "company_id": company["id"],
                    "name": "Onderhoud",
                    "status": "active",
                    "interval": "monthly",
                    "start_date": period_start.isoformat(),
                    "next_invoice_date": period_end.isoformat(),
                    "amount": "500.00",
                    "included_hours": "10",
                    "links": [{"entity_type": "project", "entity_id": project["id"]}],
                },
                headers=headers,
            )
        ).json()

        # The picker is gone: a client still sending the old field gets an unlinked entry, not
        # a 422 and not a silent link.
        created = await c.post(
            "/api/v1/time/entries",
            json={
                "started_at": mid_month.isoformat(),
                "ended_at": (mid_month + timedelta(hours=2)).isoformat(),
                "company_id": company["id"],
                "project_id": project["id"],
                "subscription_id": sub["id"],
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        assert created.json()["subscription_id"] is None

        # Usage counts those 2 h anyway — through the project the agreement covers.
        with_usage = (
            await c.get(
                f"/api/v1/subscriptions/{sub['id']}", params={"usage": True}, headers=headers
            )
        ).json()
        assert float(with_usage["usage"]["used_hours"]) == 2.0
        # And the project reports what is left of the agreement's included hours (#225).
        hours = (
            await c.get(
                f"/api/v1/projects/{project['id']}", params={"hours": True}, headers=headers
            )
        ).json()["hours"]
        assert hours["budget_hours"] == 10.0
        assert hours["spent_hours"] == 2.0
        assert hours["remaining_hours"] == 8.0


async def test_company_panel_names_the_day_and_the_colleague(client_for) -> None:
    """The client's Uren panel answers *when* and *by whom*, not only *what* and *how long*.

    #400's reproduction: three rows reading "Back-up teruggezet op de testomgeving", on three
    different days by three different colleagues, rendered identically. The date was already in
    the payload and never drawn; **who** was not in the payload at all. Both are on the row now,
    and so is the number of rows the ten are a prefix of — a panel that truncates says so.
    """
    t = await make_tenant("time-panel-context")
    mate = await make_tenant("time-panel-context-mate", email="mate-panel@example.com")
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        await add_membership(session, t.org.id, mate.user.id, role="admin")
        await session.commit()

    headers = await auth_cookie(t.user)
    mate_headers = await auth_cookie(mate.user, org_id=t.org.id)
    day = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)

    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Klant"}, headers=headers)
        ).json()
        # Eleven rows, so the panel's ten are visibly a prefix — and the eleventh is a
        # colleague's, on a different day, with the same words on it.
        for index in range(10):
            started = day + timedelta(days=index)
            created = await c.post(
                "/api/v1/time/entries",
                json={
                    "company_id": company["id"],
                    "started_at": started.isoformat(),
                    "ended_at": (started + timedelta(minutes=30)).isoformat(),
                    "description": "Back-up teruggezet op de testomgeving",
                    "billable": False,
                },
                headers=headers,
            )
            assert created.status_code == 201, created.text
        newest = day + timedelta(days=20)
        theirs = await c.post(
            "/api/v1/time/entries",
            json={
                "company_id": company["id"],
                "started_at": newest.isoformat(),
                "ended_at": (newest + timedelta(minutes=90)).isoformat(),
                "description": "Back-up teruggezet op de testomgeving",
            },
            headers=mate_headers,
        )
        assert theirs.status_code == 201, theirs.text

        panels = (await c.get(f"/api/v1/companies/{company['id']}/panels", headers=headers)).json()
        data = next(p for p in panels if p["key"] == "time.company")["data"]

        assert data["total_minutes"] == 10 * 30 + 90
        # Eleven exist, the feed default is shown (#407) — the sentence the panel prints.
        assert data["total_entries"] == 11
        assert len(data["recent"]) == 8

        top = data["recent"][0]
        assert top["user_id"] == str(mate.user.id), "the colleague who logged it is unnamed"
        assert top["started_at"].startswith(newest.date().isoformat())
        assert top["billable"] is True
        assert top["approved_at"] is None
        # What the panel's own correct-this-row dialog posts back.
        assert top["ended_at"] is not None
        assert top["break_minutes"] == 0
        # The rest are this user's, and non-billable — the marker has both states to draw.
        assert {row["user_id"] for row in data["recent"][1:]} == {str(t.user.id)}
        assert all(row["billable"] is False for row in data["recent"][1:])
