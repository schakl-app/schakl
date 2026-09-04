"""Somebody is always on a task.

The team's ask was that a task made from the board, from a dictation or from a contact moment
always names at least one person — and the reason it is a rule rather than a nicety is #392's,
one column over: an unassigned task is on no one's board, in no one's *mijn taken* and in no
one's nudges, so it is the one shape the urgency vocabulary cannot reach.

Two halves, decided differently on purpose. **Create** resolves rather than refuses: the
service already handed a task that named nobody to the project's responsible, else the
client's, and that chain now ends at the *caller* — because the callers with nobody in front
of them (an MCP agent, the assistant's ``create_task``, an import row with an empty cell) mean
the person who is obviously meant, and a 422 there would refuse the commonest sentence ever
spoken to the assistant. Every screen still asks explicitly (``TaskQuickCreate`` cancels an
empty roster before the round trip; the dictation sheet disables Aanmaken). **Update**
refuses: there is no sensible default for "hand this to nobody", so ``assignees: []`` with no
contact — or a bare ``assignee_user_id: null`` — is refused with the field named, exactly as an
explicit ``null`` deadline is. Absent still means leave alone, which is what keeps a status
move working on a row written before the rule.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text

from app.db import async_session_maker, set_current_org
from app.modules.tasks.bulk import TASK_BULK
from tests.conftest import FAR_FUTURE_DUE, auth_cookie, make_tenant
from tests.test_notifications_fanout import _member


async def _task(c, headers, title: str = "Homepage herzien", **extra) -> dict:
    body = {"title": title, "due_date": FAR_FUTURE_DUE, **extra}
    res = await c.post("/api/v1/tasks", json=body, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def _roster(task: dict) -> list[str]:
    return [link["user_id"] for link in task["assignees"]]


async def _unassign(org_id, task_id: str) -> None:
    """Write the one shape no API can produce any more: a task with nobody on it."""
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        await session.execute(
            text("DELETE FROM task_assignees WHERE task_id = :id"), {"id": uuid.UUID(task_id)}
        )
        await session.execute(
            text("UPDATE tasks SET assignee_user_id = NULL WHERE id = :id"),
            {"id": uuid.UUID(task_id)},
        )
        await session.commit()


# --------------------------------------------------------------------------- #
# Create: the chain ends at the caller
# --------------------------------------------------------------------------- #
async def test_a_task_that_names_nobody_is_the_creators(client_for) -> None:
    t = await make_tenant("assignee-create")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        silent = await _task(c, headers, "Niets gezegd")
        assert _roster(silent) == [str(t.user.id)]
        assert silent["assignee_user_id"] == str(t.user.id)
        assert silent["assignees"][0]["is_primary"] is True

        # ``[]`` is not "assign nobody" any more: it is "I named nobody", and resolves the same.
        empty = await _task(c, headers, "Lege lijst", assignees=[])
        assert _roster(empty) == [str(t.user.id)]

        explicit_null = await _task(c, headers, "Expliciet niemand", assignee_user_id=None)
        assert _roster(explicit_null) == [str(t.user.id)]


async def test_the_clients_responsible_still_outranks_the_creator(client_for) -> None:
    """The default is the *last* link: a client with a responsible keeps handing them the task."""
    t = await make_tenant("assignee-inherit")
    member = await _member(t, "verantwoordelijke@example.com")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await c.post(
            "/api/v1/companies",
            json={
                "name": "Nova Fietsen",
                "assignees": [{"user_id": str(member.id), "is_primary": True}],
            },
            headers=headers,
        )
        assert company.status_code == 201, company.text
        task = await _task(c, headers, "Voor de klant", company_id=company.json()["id"])
        assert _roster(task) == [str(member.id)]

        # …and an explicit roster is never overruled by either default.
        chosen = await _task(
            c,
            headers,
            "Zelf gekozen",
            company_id=company.json()["id"],
            assignees=[{"user_id": str(t.user.id), "is_primary": True}],
        )
        assert _roster(chosen) == [str(t.user.id)]


# --------------------------------------------------------------------------- #
# Update: a roster may be handed over and may not be emptied
# --------------------------------------------------------------------------- #
async def test_a_roster_can_be_handed_over_and_cannot_be_emptied(client_for) -> None:
    t = await make_tenant("assignee-clear")
    member = await _member(t, "collega@example.com")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        task = await _task(c, headers)

        handed = await c.patch(
            f"/api/v1/tasks/{task['id']}",
            json={"assignee_user_id": str(member.id)},
            headers=headers,
        )
        assert handed.status_code == 200, handed.text
        assert _roster(handed.json()) == [str(member.id)]

        for body in (
            {"assignees": []},
            {"assignee_user_id": None},
            {"assignees": [], "assignee_contact_id": None},
        ):
            refused = await c.patch(f"/api/v1/tasks/{task['id']}", json=body, headers=headers)
            assert refused.status_code == 422, (body, refused.text)
            assert (
                refused.json()["error"]["fields"]["assignee_user_id"]
                == "errors.tasks_assignee_required"
            ), body

        # …and the refusals wrote nothing.
        row = (await c.get(f"/api/v1/tasks/{task['id']}", headers=headers)).json()
        assert _roster(row) == [str(member.id)]


async def test_a_task_written_before_the_rule_still_updates_in_every_other_field(
    client_for,
) -> None:
    """Absent means leave alone (#392's shape): the backlog must stay tickable after upgrading."""
    t = await make_tenant("assignee-legacy")
    member = await _member(t, "collega@example.com")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        task = await _task(c, headers, "Oude taak")
        await _unassign(t.org.id, task["id"])

        detail = (await c.get(f"/api/v1/tasks/{task['id']}", headers=headers)).json()
        assert detail["assignees"] == [] and detail["assignee_user_id"] is None

        statuses = (await c.get("/api/v1/tasks/statuses", headers=headers)).json()
        done = next(s["key"] for s in statuses if s["is_terminal"])
        for body in (
            {"status": done},
            {"title": "Nieuwe titel"},
            {"priority": "high"},
            {"due_date": FAR_FUTURE_DUE},
        ):
            res = await c.patch(f"/api/v1/tasks/{task['id']}", json=body, headers=headers)
            assert res.status_code == 200, (body, res.text)
            # Untouched: a PATCH that does not mention the roster does not invent one either.
            assert res.json()["assignees"] == []

        # Re-confirming "nobody" is the one thing that stops being allowed…
        refused = await c.patch(
            f"/api/v1/tasks/{task['id']}", json={"assignees": []}, headers=headers
        )
        assert refused.status_code == 422, refused.text

        # …and the way out is an ordinary update naming someone.
        fixed = await c.patch(
            f"/api/v1/tasks/{task['id']}",
            json={"assignees": [{"user_id": str(member.id), "is_primary": True}]},
            headers=headers,
        )
        assert fixed.status_code == 200, fixed.text
        assert _roster(fixed.json()) == [str(member.id)]


def test_the_bulk_editor_can_set_the_assignee_and_cannot_clear_it() -> None:
    """A control that empties a field the very next write refuses is #253's control that can
    only refuse — so the ✎ dialog offers "set" and never "clear" for the roster."""
    field = next(f for f in TASK_BULK.editable if f.key == "assignee")
    assert field.clearable is False
