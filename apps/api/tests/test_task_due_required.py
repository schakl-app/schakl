"""A task always has a deadline (#392).

The team's sentence is *"binnen het CRM moet altijd een datum bekend zijn, zodat de taak
zichtbaar blijft en niet kan worden overgeslagen"* — and the reason it is a required field
rather than a warning is that an undated task is not merely unscheduled. It is absent from
``?due=overdue``, from ``?due=today``, from ``?due=week``, from the Agenda's deadline feed and
from both dashboards' overdue counts: **invisible to the entire urgency vocabulary**.

Two halves, and the second is the one that has to hold on somebody else's data. The rule lives
in the *schema* layer — ``TaskCreate.due_date`` is required, ``TaskUpdate`` refuses an explicit
``null`` — while the column stays nullable for at least a release (expand/contract,
docs/WORKFLOW.md), because the entrypoint runs ``alembic upgrade head`` unattended. So every
row an instance carries into this release must keep opening, keep rendering and keep being
editable in every field, and there has to be a way to *find* them.

The unattended creators are the third half: a 422 raised in a worker is a task nobody ever
sees, so each of them states a default rather than inheriting ``NULL``.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import text

from app.db import async_session_maker, set_current_org
from tests.conftest import FAR_FUTURE_DUE, auth_cookie, make_tenant, org_today


async def _task(c, headers, title: str = "Homepage herzien", **extra) -> dict:
    body = {"title": title, "due_date": FAR_FUTURE_DUE, **extra}
    res = await c.post("/api/v1/tasks", json=body, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


async def _undate(org_id, task_id: str) -> None:
    """Write the one shape no API can produce any more: a task with no deadline."""
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        await session.execute(
            text("UPDATE tasks SET due_date = NULL WHERE id = :id"), {"id": uuid.UUID(task_id)}
        )
        await session.commit()


# --------------------------------------------------------------------------- #
# The rule
# --------------------------------------------------------------------------- #
async def test_a_task_cannot_be_created_without_a_deadline(client_for) -> None:
    t = await make_tenant("due-create")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        refused = await c.post("/api/v1/tasks", json={"title": "Zonder datum"}, headers=headers)
        assert refused.status_code == 422, refused.text
        # The envelope names the field, so the form can put the message under the box rather
        # than printing "er ging iets mis" over a control the user can see (CLAUDE.md §9).
        assert "due_date" in refused.text

        explicit_null = await c.post(
            "/api/v1/tasks", json={"title": "Ook niet", "due_date": None}, headers=headers
        )
        assert explicit_null.status_code == 422, explicit_null.text


async def test_a_deadline_can_be_moved_and_cannot_be_cleared(client_for) -> None:
    """CLAUDE.md §18's rule with its second half withdrawn: absent still means leave alone."""
    t = await make_tenant("due-clear")
    headers = await auth_cookie(t.user)
    today = org_today()
    async with client_for(t.host) as c:
        task = await _task(c, headers, due_date=(today + timedelta(days=5)).isoformat())

        earlier = await c.patch(
            f"/api/v1/tasks/{task['id']}", json={"due_date": today.isoformat()}, headers=headers
        )
        assert earlier.status_code == 200, earlier.text

        cleared = await c.patch(
            f"/api/v1/tasks/{task['id']}", json={"due_date": None}, headers=headers
        )
        assert cleared.status_code == 422, cleared.text
        assert cleared.json()["error"]["fields"]["due_date"] == "errors.required"

        # …and the refusal wrote nothing.
        row = (await c.get(f"/api/v1/tasks/{task['id']}", headers=headers)).json()
        assert row["due_date"] == today.isoformat()


# --------------------------------------------------------------------------- #
# The rows an instance upgrades with — the criterion that matters most
# --------------------------------------------------------------------------- #
async def test_a_task_written_before_the_rule_still_opens_renders_and_updates(
    client_for,
) -> None:
    """An agency's first act after upgrading must not be being unable to tick off its backlog.

    Every field, not only the harmless ones: the status move is the one that would strand a
    whole board, and it must not be made to depend on somebody first supplying a date for work
    that is already finished.
    """
    t = await make_tenant("due-legacy")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        task = await _task(c, headers, "Oude taak")
        await _undate(t.org.id, task["id"])

        detail = await c.get(f"/api/v1/tasks/{task['id']}", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["due_date"] is None

        listed = (await c.get("/api/v1/tasks?limit=50", headers=headers)).json()
        assert [i["id"] for i in listed["items"]] == [task["id"]]

        statuses = (await c.get("/api/v1/tasks/statuses", headers=headers)).json()
        done = next(s["key"] for s in statuses if s["is_terminal"])
        moved = await c.patch(
            f"/api/v1/tasks/{task['id']}", json={"status": done}, headers=headers
        )
        assert moved.status_code == 200, moved.text
        assert moved.json()["status"] == done
        # Untouched: a PATCH that does not mention the deadline does not invent one either.
        assert moved.json()["due_date"] is None

        for body in (
            {"title": "Nieuwe titel"},
            {"priority": "high"},
            {"description": "Toelichting"},
            {"allocated_minutes": 30},
        ):
            res = await c.patch(f"/api/v1/tasks/{task['id']}", json=body, headers=headers)
            assert res.status_code == 200, (body, res.text)

        # And the way out: naming a date is an ordinary update, with no reason required —
        # ``due_change_reason`` guards *extending* an existing deadline, and there is none.
        dated = await c.patch(
            f"/api/v1/tasks/{task['id']}",
            json={"due_date": org_today().isoformat()},
            headers=headers,
        )
        assert dated.status_code == 200, dated.text


async def test_undated_filter_finds_exactly_the_legacy_rows(client_for) -> None:
    t = await make_tenant("due-filter")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        legacy = await _task(c, headers, "Zonder datum")
        dated = await _task(c, headers, "Met datum")
        await _undate(t.org.id, legacy["id"])

        found = (await c.get("/api/v1/tasks?undated=1", headers=headers)).json()
        assert [i["id"] for i in found["items"]] == [legacy["id"]]
        assert found["total"] == 1

        rest = (await c.get("/api/v1/tasks?undated=0", headers=headers)).json()
        assert [i["id"] for i in rest["items"]] == [dated["id"]]

        # Omitted returns both — the endpoint's own default stays *everything* (CLAUDE.md §9).
        both = (await c.get("/api/v1/tasks", headers=headers)).json()
        assert both["total"] == 2


async def test_a_bulk_edit_dates_a_selection_and_refuses_to_empty_one(client_for) -> None:
    """The way an agency clears its whole backlog in one gesture — and the guard beside it."""
    t = await make_tenant("due-bulk")
    headers = await auth_cookie(t.user)
    today = org_today()
    async with client_for(t.host) as c:
        ids = []
        for title in ("Een", "Twee", "Drie"):
            task = await _task(c, headers, title)
            await _undate(t.org.id, task["id"])
            ids.append(task["id"])

        result = await c.post(
            "/api/v1/bulk/task/update",
            json={"ids": ids, "values": {"due_date": today.isoformat()}},
            headers=headers,
        )
        assert result.status_code == 200, result.text
        assert result.json() == {"succeeded": 3, "failed": []}
        assert (await c.get("/api/v1/tasks?undated=1", headers=headers)).json()["total"] == 0

        # An empty box is "I did not fill this in", never "empty it on all of them": a bad
        # shared value is the caller's and refuses the whole call (CLAUDE.md §18).
        blanked = await c.post(
            "/api/v1/bulk/task/update",
            json={"ids": ids, "values": {"due_date": None}},
            headers=headers,
        )
        assert blanked.status_code == 422, blanked.text
        assert blanked.json()["error"]["fields"]["due_date"] == "errors.required"


# --------------------------------------------------------------------------- #
# Every creator with nobody in front of it states a date
# --------------------------------------------------------------------------- #
async def test_a_template_item_with_no_relative_due_day_is_due_the_day_it_is_applied(
    client_for,
) -> None:
    t = await make_tenant("due-template")
    headers = await auth_cookie(t.user)
    today = org_today()
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Klant"}, headers=headers)
        ).json()
        template = (
            await c.post(
                "/api/v1/tasks/templates",
                json={
                    "name": "Onboarding",
                    "items": [
                        {"title": "Over twee dagen", "relative_due_days": 2},
                        {"title": "Geen relatieve dag"},
                    ],
                },
                headers=headers,
            )
        ).json()

        applied = await c.post(
            f"/api/v1/tasks/templates/{template['id']}/apply",
            json={"company_id": company["id"]},
            headers=headers,
        )
        assert applied.status_code == 201, applied.text
        due = {task["title"]: task["due_date"] for task in applied.json()}
        assert due["Over twee dagen"] == (today + timedelta(days=2)).isoformat()
        assert due["Geen relatieve dag"] == today.isoformat()


async def test_a_recurring_occurrence_is_dated_by_its_own_rule(client_for) -> None:
    """The generator already computed one; this pins that it still reaches the row."""
    t = await make_tenant("due-recurring")
    headers = await auth_cookie(t.user)
    today = org_today()
    async with client_for(t.host) as c:
        task = await _task(
            c,
            headers,
            "Maandrapport",
            due_date=today.isoformat(),
            recurrence={"freq": "monthly", "interval": 1, "mode": "after_completion"},
        )
        statuses = (await c.get("/api/v1/tasks/statuses", headers=headers)).json()
        done = next(s["key"] for s in statuses if s["is_terminal"])
        await c.patch(f"/api/v1/tasks/{task['id']}", json={"status": done}, headers=headers)

        listed = (await c.get("/api/v1/tasks?limit=50", headers=headers)).json()
        spawned = [i for i in listed["items"] if i["id"] != task["id"]]
        assert len(spawned) == 1, listed
        assert spawned[0]["due_date"] is not None
