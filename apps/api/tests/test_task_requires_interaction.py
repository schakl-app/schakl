"""Per-task and per-template "close only with a contact moment" (#157 extended).

The per-status gate already had coverage; this proves the two new dimensions: a task's own
``requires_interaction`` flag blocks reaching a terminal status without a designated closing
moment, the flag rides recurrence to the next occurrence, and a template item copies it onto the
tasks it spawns.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from app.db import async_session_maker, set_current_org
from app.modules.tasks.models import Task
from tests.conftest import auth_cookie, make_tenant

_NOW = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)


async def _log_moment(c, headers, task_id: str) -> str:
    """Log a team-visible contact moment linked to the task; return its id."""
    created = await c.post(
        "/api/v1/interactions",
        json={
            "kind": "note",
            "task_id": task_id,
            "subject": "Besproken met klant",
            "occurred_at": _NOW.isoformat(),
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


async def test_per_task_flag_blocks_close_without_moment(client_for) -> None:
    t = await make_tenant("reqint-task")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        task = (
            await c.post(
                "/api/v1/tasks",
                json={"title": "Bespreken met klant", "requires_interaction": True},
                headers=headers,
            )
        ).json()
        assert task["requires_interaction"] is True

        # Entering the terminal status without a moment is refused on the status field.
        blocked = await c.patch(
            f"/api/v1/tasks/{task['id']}", json={"status": "done"}, headers=headers
        )
        assert blocked.status_code == 422, blocked.text
        assert blocked.json()["error"]["fields"]["status"] == (
            "errors.tasks_closing_interaction_required"
        )

        # A non-terminal move is unaffected by the per-task flag.
        moved = await c.patch(
            f"/api/v1/tasks/{task['id']}", json={"status": "in_progress"}, headers=headers
        )
        assert moved.status_code == 200, moved.text

        # With a linked, team-visible moment the close goes through.
        moment_id = await _log_moment(c, headers, task["id"])
        closed = await c.patch(
            f"/api/v1/tasks/{task['id']}",
            json={"status": "done", "closing_interaction_id": moment_id},
            headers=headers,
        )
        assert closed.status_code == 200, closed.text
        assert closed.json()["status"] == "done"
        assert closed.json()["closing_interaction_id"] == moment_id


async def test_unflagged_task_closes_freely(client_for) -> None:
    t = await make_tenant("reqint-free")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        task = (
            await c.post("/api/v1/tasks", json={"title": "Gewoon af"}, headers=headers)
        ).json()
        assert task["requires_interaction"] is False
        closed = await c.patch(
            f"/api/v1/tasks/{task['id']}", json={"status": "done"}, headers=headers
        )
        assert closed.status_code == 200, closed.text


async def test_flag_rides_recurrence_to_next_occurrence(client_for) -> None:
    t = await make_tenant("reqint-recur")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        task = (
            await c.post(
                "/api/v1/tasks",
                json={
                    "title": "Maandelijks nabellen",
                    "requires_interaction": True,
                    "due_date": "2026-07-31",
                    "recurrence": {"freq": "monthly", "interval": 1, "mode": "after_completion"},
                },
                headers=headers,
            )
        ).json()
        moment_id = await _log_moment(c, headers, task["id"])
        closed = await c.patch(
            f"/api/v1/tasks/{task['id']}",
            json={"status": "done", "closing_interaction_id": moment_id},
            headers=headers,
        )
        assert closed.status_code == 200, closed.text

    # The after-completion spawn cloned the carrier; the new occurrence keeps the policy.
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        spawned = (
            await session.execute(
                select(Task).where(Task.recurrence_next_run.is_(None), Task.completed_at.is_(None))
            )
        ).scalars().all()
        assert len(spawned) == 1
        assert spawned[0].requires_interaction is True
        assert spawned[0].closing_interaction_id is None  # a fresh occurrence needs its own


async def test_template_item_flag_copies_to_spawned_task(client_for) -> None:
    t = await make_tenant("reqint-tmpl")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Klant BV"}, headers=headers)
        ).json()
        template = (
            await c.post(
                "/api/v1/tasks/templates",
                json={
                    "name": "Onboarding",
                    "items": [
                        {"title": "Kick-off bespreken", "requires_interaction": True},
                        {"title": "Map aanmaken", "requires_interaction": False},
                    ],
                },
                headers=headers,
            )
        ).json()
        assert template["items"][0]["requires_interaction"] is True

        applied = await c.post(
            f"/api/v1/tasks/templates/{template['id']}/apply",
            json={"company_id": company["id"]},
            headers=headers,
        )
        assert applied.status_code == 201, applied.text
        spawned = {task["title"]: task for task in applied.json()}
        assert spawned["Kick-off bespreken"]["requires_interaction"] is True
        assert spawned["Map aanmaken"]["requires_interaction"] is False

        # The flagged one cannot be closed without a moment; the other can.
        flagged = spawned["Kick-off bespreken"]
        blocked = await c.patch(
            f"/api/v1/tasks/{flagged['id']}", json={"status": "done"}, headers=headers
        )
        assert blocked.status_code == 422, blocked.text
