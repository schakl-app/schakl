"""A schedule-mode rule is a series laid out a year ahead, from one root that keeps the rule.

Before, a rule in schedule mode handed itself to the next occurrence the night it fell due, so
the calendar knew about exactly one future task at a time. Now saving the rule creates every
occurrence inside the year (``recurrence.materialize_series``), the nightly sweep extends the
year as it slides, and three consequences are pinned here: the same rule saved twice lays out
nothing twice; a changed rule re-lays the unfinished future; and handing a task to a colleague
can hand over every following occurrence — rosters, booked blocks and the rule's own plan — in
one request (``TaskUpdate.apply_to``).
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import select

from app.db import async_session_maker, set_current_org
from app.modules.tasks.models import Task
from app.modules.tasks.recurrence import HORIZON_DAYS, spawn_scheduled_recurrences
from tests.conftest import add_membership, auth_cookie, default_company, make_tenant, org_today

_PLAN = {"blocks": [{"on": "due", "start_time": "09:00:00", "duration_minutes": 60}]}


async def _tasks(c, headers) -> list[dict]:
    return (await c.get("/api/v1/tasks?limit=200", headers=headers)).json()["items"]


async def _occurrences(c, headers, root_id: str) -> list[dict]:
    rows = [row for row in await _tasks(c, headers) if row["recurrence_source_id"] == root_id]
    return sorted(rows, key=lambda row: row["due_date"])


async def _make_root(c, headers, *, freq: str = "monthly", **extra) -> dict:
    body = {
        "title": "Nieuwsbrief",
        "due_date": (org_today() + timedelta(days=10)).isoformat(),
        "recurrence": {"freq": freq, "interval": 1, "mode": "schedule", "plan": _PLAN},
        **extra,
    }
    body.setdefault("company_id", await default_company(c, headers))
    res = await c.post("/api/v1/tasks", json=body, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


async def test_saving_a_schedule_rule_lays_the_year_out(client_for) -> None:
    t = await make_tenant("series-year")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        root = await _make_root(c, headers, assignee_user_id=str(t.user.id))
        occurrences = await _occurrences(c, headers, root["id"])
        # The months that fit inside the year follow the root's own first occurrence — eleven
        # or twelve depending on where the 365th day falls — and none past the horizon.
        assert len(occurrences) in (11, 12)
        horizon = org_today() + timedelta(days=HORIZON_DAYS)
        for row in occurrences:
            assert row["recurrence"] is None  # an occurrence is an ordinary task
            assert date.fromisoformat(row["due_date"]) <= horizon
            assert row["assignee_user_id"] == str(t.user.id)
        # Every one of them is booked, through the schedule service, on the day it is due.
        blocks = (
            await c.get(f"/api/v1/tasks/schedules?task_id={occurrences[3]['id']}", headers=headers)
        ).json()
        assert len(blocks) == 1 and blocks[0]["start"] == occurrences[3]["due_date"]

        # The root keeps the rule, and its pointer is the first date *not* laid out.
        stored = (await c.get(f"/api/v1/tasks/{root['id']}", headers=headers)).json()
        assert stored["recurrence"]["mode"] == "schedule"
        assert date.fromisoformat(stored["recurrence_next_run"]) > horizon
        # The card answers the series on the root and on any occurrence alike.
        assert stored["series"]["root_id"] == root["id"]
        # The root is the first occurrence, so it counts.
        assert stored["series"]["upcoming_total"] == len(occurrences) + 1
        assert [row["id"] for row in stored["series"]["upcoming"]][:2] == [
            root["id"],
            occurrences[0]["id"],
        ]
        occurrence = (await c.get(f"/api/v1/tasks/{occurrences[5]['id']}", headers=headers)).json()
        assert occurrence["series"]["root_id"] == root["id"]
        assert occurrence["series"]["recurrence"]["freq"] == "monthly"
        assert any(a["action"] == "recurrence_spawned" for a in occurrence["activities"])

        # Saving the very same rule again — which the edit form does on every save — lays out
        # nothing more and leaves the pointer where it was.
        again = await c.patch(
            f"/api/v1/tasks/{root['id']}",
            json={"recurrence": stored["recurrence"], "title": "Nieuwsbrief (bewerkt)"},
            headers=headers,
        )
        assert again.status_code == 200, again.text
        assert len(await _occurrences(c, headers, root["id"])) == len(occurrences)
        assert again.json()["recurrence_next_run"] == stored["recurrence_next_run"]

    # …and neither does the nightly sweep, until the horizon reaches the next date.
    assert await spawn_scheduled_recurrences({}) == 0


async def test_changing_the_rule_relays_the_unfinished_future(client_for) -> None:
    t = await make_tenant("series-relayout")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        root = await _make_root(c, headers)
        monthly = await _occurrences(c, headers, root["id"])
        # One of the laid-out occurrences was finished early: a record of work done, it stays.
        done = monthly[0]
        await c.patch(f"/api/v1/tasks/{done['id']}", json={"status": "done"}, headers=headers)
        # The rhythm changes: every unfinished future occurrence is wrong by definition.
        res = await c.patch(
            f"/api/v1/tasks/{root['id']}",
            json={"recurrence": {"freq": "weekly", "interval": 1, "mode": "schedule"}},
            headers=headers,
        )
        assert res.status_code == 200, res.text
        after = await _occurrences(c, headers, root["id"])
        ids = {row["id"] for row in after}
        assert done["id"] in ids
        assert not ({row["id"] for row in monthly[1:]} & ids), "old rhythm left standing"
        weekly = [row for row in after if row["id"] != done["id"]]
        assert 49 <= len(weekly) <= 53  # weekly from ~17 days out to the horizon
        dues = [date.fromisoformat(row["due_date"]) for row in weekly]
        assert all((b - a).days == 7 for a, b in zip(dues, dues[1:], strict=False))

        # Removing the rule removes the unfinished future the same way, and lays out nothing.
        res = await c.patch(
            f"/api/v1/tasks/{root['id']}", json={"recurrence": None}, headers=headers
        )
        assert res.status_code == 200, res.text
        remaining = await _occurrences(c, headers, root["id"])
        assert [row["id"] for row in remaining] == [done["id"]]
        assert res.json()["recurrence_next_run"] is None


async def test_handing_over_the_future_moves_rosters_blocks_and_the_plan(client_for) -> None:
    """``apply_to: future`` on an occurrence hands every following one to the newcomer: their
    roster, the block already booked on the leaver's calendar, and the plan on the root — while
    the occurrences before it, and a plain ``apply_to: this``, touch nothing else."""
    t = await make_tenant("series-handover")
    colleague = await make_tenant("series-handover-c", email="c@example.com")
    headers = await auth_cookie(t.user)
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        await add_membership(session, t.org.id, colleague.user.id, "member")
        await session.commit()
    async with client_for(t.host) as c:
        root = await _make_root(
            c,
            headers,
            assignee_user_id=str(t.user.id),
            recurrence={
                "freq": "monthly",
                "interval": 1,
                "mode": "schedule",
                # The plan names the owner outright, so the hand-off has a name to rewrite.
                "plan": {
                    "blocks": [
                        {
                            "on": "due",
                            "start_time": "09:00:00",
                            "duration_minutes": 60,
                            "user_ids": [str(t.user.id)],
                        }
                    ]
                },
            },
        )
        occurrences = await _occurrences(c, headers, root["id"])
        assert len(occurrences) >= 11
        pivot = occurrences[4]

        # "Only this one": the default, and nothing else moves — the next occurrence keeps
        # its owner, and the block on this one is left where it is (it is this task's own).
        res = await c.patch(
            f"/api/v1/tasks/{occurrences[1]['id']}",
            json={"assignee_user_id": str(colleague.user.id)},
            headers=headers,
        )
        assert res.status_code == 200, res.text
        after_this = await _occurrences(c, headers, root["id"])
        assert after_this[1]["assignee_user_id"] == str(colleague.user.id)
        assert after_this[2]["assignee_user_id"] == str(t.user.id)

        # "This one and all following".
        res = await c.patch(
            f"/api/v1/tasks/{pivot['id']}",
            json={
                "assignees": [{"user_id": str(colleague.user.id), "is_primary": True}],
                "apply_to": "future",
            },
            headers=headers,
        )
        assert res.status_code == 200, res.text
        rows = await _occurrences(c, headers, root["id"])
        for row in (rows[0], rows[2], rows[3]):
            assert row["assignee_user_id"] == str(t.user.id), "the past was rewritten"
        for row in rows[4:]:
            assert row["assignee_user_id"] == str(colleague.user.id)
            assert [a["user_id"] for a in row["assignees"]] == [str(colleague.user.id)]
        # The block booked on the owner's calendar for a following occurrence moved with it…
        moved = (
            await c.get(f"/api/v1/tasks/schedules?task_id={rows[7]['id']}", headers=headers)
        ).json()
        assert [b["user_id"] for b in moved] == [str(colleague.user.id)]
        # …and an earlier one did not.
        kept = (
            await c.get(f"/api/v1/tasks/schedules?task_id={rows[1]['id']}", headers=headers)
        ).json()
        assert [b["user_id"] for b in kept] == [str(t.user.id)]
        # The rule on the root names the newcomer now, so what is laid out next follows too.
        stored = (await c.get(f"/api/v1/tasks/{root['id']}", headers=headers)).json()
        assert stored["recurrence"]["plan"]["blocks"][0]["user_ids"] == [str(colleague.user.id)]
        # The root itself, being before the pivot, keeps its owner.
        assert stored["assignee_user_id"] == str(t.user.id)
        detail = (await c.get(f"/api/v1/tasks/{rows[7]['id']}", headers=headers)).json()
        assert any(a["action"] == "assignees_transferred" for a in detail["activities"])


async def test_deleting_the_root_takes_the_unfinished_future_with_it(client_for) -> None:
    t = await make_tenant("series-delete")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        root = await _make_root(c, headers)
        occurrences = await _occurrences(c, headers, root["id"])
        assert len(occurrences) >= 11
        finished = occurrences[2]
        await c.patch(f"/api/v1/tasks/{finished['id']}", json={"status": "done"}, headers=headers)
        res = await c.delete(f"/api/v1/tasks/{root['id']}", headers=headers)
        assert res.status_code == 204, res.text
        left = await _tasks(c, headers)
        # The finished occurrence is a record and stays — an ordinary task now, its rule gone.
        assert [row["id"] for row in left] == [finished["id"]]
        assert left[0]["recurrence_source_id"] is None

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        assert await session.scalar(select(Task).where(Task.id == uuid.UUID(root["id"]))) is None


async def test_preview_counts_the_year_for_a_schedule_rule(client_for) -> None:
    t = await make_tenant("series-preview")
    headers = await auth_cookie(t.user)
    due = (org_today() + timedelta(days=3)).isoformat()
    async with client_for(t.host) as c:
        scheduled = await c.post(
            "/api/v1/tasks/recurrence/preview",
            json={
                "recurrence": {"freq": "monthly", "interval": 1, "mode": "schedule"},
                "due_date": due,
            },
            headers=headers,
        )
        assert scheduled.status_code == 200, scheduled.text
        assert scheduled.json()["year_count"] in (11, 12)
        on_completion = await c.post(
            "/api/v1/tasks/recurrence/preview",
            json={"recurrence": {"freq": "monthly", "interval": 1}, "due_date": due},
            headers=headers,
        )
        assert on_completion.json()["year_count"] is None


# --------------------------------------------------------------------------------------- #
# Folding a series onto its current occurrence (the board, the pickers)
# --------------------------------------------------------------------------------------- #
async def _folded(c, headers, **params) -> list[dict]:
    query = "&".join(
        f"{k}={v}" for k, v in {"limit": 200, "collapse_series": "true", **params}.items()
    )
    res = await c.get(f"/api/v1/tasks?{query}", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["items"]


async def test_a_folded_list_shows_a_series_once_and_says_how_many_it_hides(client_for) -> None:
    """``?collapse_series=true`` draws one row per series — its earliest unfinished occurrence —
    and stamps that row with the number of future occurrences it stands for. Unfolded stays the
    endpoint's default, because the export and the MCP surface read the same list."""
    t = await make_tenant("series-fold")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        root = await _make_root(c, headers)
        ahead = await _occurrences(c, headers, root["id"])
        loose = (
            await c.post(
                "/api/v1/tasks",
                json={
                    "title": "Losse taak",
                    "due_date": (org_today() + timedelta(days=400)).isoformat(),
                    "company_id": root["company_id"],
                },
                headers=headers,
            )
        ).json()

        # Unfolded: every occurrence is a row, and each one names its series.
        every = await _tasks(c, headers)
        assert len(every) == 1 + len(ahead) + 1
        by_id = {row["id"]: row for row in every}
        assert {row["series_root_id"] for row in every if row["id"] != loose["id"]} == {root["id"]}
        assert by_id[loose["id"]]["series_root_id"] is None
        assert all(row["series_pending"] is None for row in every)  # nothing folded, no count

        # Folded: the root is the current occurrence (the earliest unfinished), the year behind
        # it is one number on that row, and the loose task is untouched.
        rows = await _folded(c, headers)
        assert {row["id"] for row in rows} == {root["id"], loose["id"]}
        current = next(row for row in rows if row["id"] == root["id"])
        assert current["series_pending"] == len(ahead)
        assert current["series_root_id"] == root["id"]
        assert next(row for row in rows if row["id"] == loose["id"])["series_pending"] is None

        # Finishing the current one hands the fold to the next: it is drawn, and it counts one
        # fewer. The finished root stays a row — a record of work done is never folded away.
        done = await c.patch(
            f"/api/v1/tasks/{root['id']}", json={"status": "done"}, headers=headers
        )
        assert done.status_code == 200, done.text
        rows = await _folded(c, headers)
        assert {row["id"] for row in rows} == {root["id"], ahead[0]["id"], loose["id"]}
        current = next(row for row in rows if row["id"] == ahead[0]["id"])
        assert current["series_pending"] == len(ahead) - 1
        assert next(row for row in rows if row["id"] == root["id"])["series_pending"] is None

        # The fold narrows the page, so the total follows it.
        res = await c.get("/api/v1/tasks?collapse_series=true&limit=1", headers=headers)
        assert res.json()["total"] == 3


async def test_an_overdue_occurrence_is_never_folded(client_for) -> None:
    """Only the *future* folds: an occurrence due today or already late is work to act on, so
    every one of them stays a row and the dashboard's overdue count and the board agree."""
    t = await make_tenant("series-fold-overdue")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        # A series nobody kept up with: the root is forty days late, and one laid-out occurrence
        # was pulled forward into last week (the layout itself never writes into the past).
        root = await _make_root(c, headers, due_date=(org_today() - timedelta(days=40)).isoformat())
        ahead = await _occurrences(c, headers, root["id"])
        late = ahead[0]
        moved = await c.patch(
            f"/api/v1/tasks/{late['id']}",
            json={"due_date": (org_today() - timedelta(days=5)).isoformat()},
            headers=headers,
        )
        assert moved.status_code == 200, moved.text
        future = ahead[1:]
        assert future, "the fixture should straddle today"

        rows = await _folded(c, headers)
        assert {row["id"] for row in rows} == {root["id"], late["id"]}
        # The number rides the current occurrence — the oldest open one — and nothing else.
        assert next(row for row in rows if row["id"] == root["id"])["series_pending"] == len(future)
        shown_late = next(row for row in rows if row["id"] == late["id"])
        assert shown_late["series_pending"] is None
        assert shown_late["series_root_id"] == root["id"]

        # Filters compose with the fold: the overdue chip still lists both late rows…
        overdue = await _folded(c, headers, due="overdue")
        assert {row["id"] for row in overdue} == {root["id"], late["id"]}
        # …and "later" is the fold's whole point: nothing where there used to be eleven rows.
        assert await _folded(c, headers, due="later") == []


async def test_series_id_answers_the_whole_series_from_any_member(client_for) -> None:
    t = await make_tenant("series-by-id")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        root = await _make_root(c, headers)
        ahead = await _occurrences(c, headers, root["id"])
        other = await _make_root(c, headers, title="Andere reeks")
        whole = {root["id"], *(row["id"] for row in ahead)}

        async def series(task_id: str) -> list[dict]:
            res = await c.get(
                f"/api/v1/tasks?series_id={task_id}&limit=200&sort=due_date", headers=headers
            )
            assert res.status_code == 200, res.text
            return res.json()["items"]

        by_root = await series(root["id"])
        assert {row["id"] for row in by_root} == whole
        assert by_root[0]["id"] == root["id"]  # due order: the root is the first occurrence
        # An occurrence's id names the same series — a caller holding one row need not know
        # whether it is the root.
        assert {row["id"] for row in await series(ahead[4]["id"])} == whole
        # A finished occurrence is still in its series.
        await c.patch(f"/api/v1/tasks/{ahead[0]['id']}", json={"status": "done"}, headers=headers)
        assert {row["id"] for row in await series(root["id"])} == whole
        # Another series is another answer, and an id that is nothing is an empty page.
        assert other["id"] in {row["id"] for row in await series(other["id"])}
        assert not whole & {row["id"] for row in await series(other["id"])}
        assert await series(str(uuid.uuid4())) == []


async def test_a_portal_login_reads_folded_rows_without_the_series(client_for) -> None:
    """A client's list folds too — twelve "Nieuwsbrief" rows are noise on any screen — but the
    series fields stay off their rows with the rest of the repeat machinery (#449)."""
    from sqlalchemy import select as sa_select

    from app.core.auth.models import User

    t = await make_tenant("series-portal")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Klant"}, headers=headers)
        ).json()
        contact = (
            await c.post(
                "/api/v1/contacts",
                json={
                    "first_name": "Piet",
                    "last_name": "Klant",
                    "email": "piet-series@example.com",
                    "company_ids": [company["id"]],
                },
                headers=headers,
            )
        ).json()
        root = await _make_root(c, headers, company_id=company["id"], visible_to_client=True)
        ahead = await _occurrences(c, headers, root["id"])
        assert ahead and all(row["visible_to_client"] for row in ahead)
        await c.post(f"/api/v1/portal/logins/contact/{contact['id']}", headers=headers)
        async with async_session_maker() as session:
            portal_user = await session.scalar(
                sa_select(User).where(User.email == contact["email"])
            )
        portal_headers = await auth_cookie(portal_user)

        rows = await _folded(c, portal_headers)
        assert [row["id"] for row in rows] == [root["id"]]
        assert rows[0]["series_root_id"] is None
        assert rows[0]["series_pending"] is None
        assert rows[0]["recurrence"] is None
