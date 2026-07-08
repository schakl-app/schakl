"""Recurring tasks: date math, after-completion spawn, scheduled cron spawn, isolation."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select

from app.db import async_session_maker, set_current_org
from app.modules.tasks.models import Task
from app.modules.tasks.recurrence import advance, spawn_scheduled_recurrences, today_local
from tests.conftest import auth_cookie, make_tenant


def test_advance_month_end_clamps() -> None:
    assert advance(date(2026, 1, 31), "monthly", 1) == date(2026, 2, 28)
    assert advance(date(2024, 1, 31), "monthly", 1) == date(2024, 2, 29)  # leap year
    assert advance(date(2026, 1, 15), "quarterly", 1) == date(2026, 4, 15)
    assert advance(date(2026, 3, 10), "yearly", 2) == date(2028, 3, 10)
    assert advance(date(2026, 1, 1), "weekly", 2) == date(2026, 1, 15)
    assert advance(date(2026, 1, 1), "daily", 3) == date(2026, 1, 4)


async def test_after_completion_spawns_next_occurrence(client_for) -> None:
    t = await make_tenant("rec-done")
    headers = await auth_cookie(t.user)
    yesterday = (today_local() - timedelta(days=1)).isoformat()

    async with client_for(t.host) as c:
        task = (
            await c.post(
                "/api/v1/tasks",
                json={
                    "title": "Monthly report",
                    "due_date": yesterday,
                    "recurrence": {"freq": "weekly", "interval": 1, "mode": "after_completion"},
                },
                headers=headers,
            )
        ).json()
        assert task["recurrence"]["freq"] == "weekly"

        # Give the carrier a checklist so the clone can inherit a reset copy.
        checklist = (
            await c.post(
                f"/api/v1/tasks/{task['id']}/checklists",
                json={"title": "Steps"},
                headers=headers,
            )
        ).json()
        item = (
            await c.post(
                f"/api/v1/tasks/{task['id']}/checklists/{checklist['id']}/items",
                json={"title": "Draft"},
                headers=headers,
            )
        ).json()
        await c.patch(
            f"/api/v1/tasks/{task['id']}/checklists/{checklist['id']}/items/{item['id']}",
            json={"done": True},
            headers=headers,
        )

        done = (
            await c.patch(
                f"/api/v1/tasks/{task['id']}", json={"status": "done"}, headers=headers
            )
        ).json()
        assert done["completed_at"] is not None
        assert done["recurrence"] is None  # recurrence moved to the clone

        listed = (await c.get("/api/v1/tasks", headers=headers)).json()["items"]
        assert len(listed) == 2
        clone = next(row for row in listed if row["id"] != task["id"])
        assert clone["status"] == "open"
        assert clone["recurrence"]["mode"] == "after_completion"
        assert date.fromisoformat(clone["due_date"]) > today_local()
        # Checklist copied with items reset to not-done.
        assert (clone["checklist_done"], clone["checklist_total"]) == (0, 1)

        detail = (await c.get(f"/api/v1/tasks/{clone['id']}", headers=headers)).json()
        assert any(a["action"] == "recurrence_spawned" for a in detail["activities"])


async def test_scheduled_cron_spawns_per_org_isolated(client_for) -> None:
    a = await make_tenant("rec-cron-a")
    b = await make_tenant("rec-cron-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)

    async with client_for(a.host) as ca:
        a_task = (
            await ca.post(
                "/api/v1/tasks",
                json={
                    "title": "Weekly digest",
                    "recurrence": {"freq": "weekly", "interval": 1, "mode": "schedule"},
                },
                headers=a_headers,
            )
        ).json()
        assert a_task["recurrence"]["mode"] == "schedule"
    async with client_for(b.host) as cb:
        await cb.post("/api/v1/tasks", json={"title": "No recurrence"}, headers=b_headers)

    # A fresh schedule carrier's next_run is always in the future; pull it into the past so
    # the cron considers it due.
    async with async_session_maker() as session:
        await set_current_org(session, a.org.id)
        carrier = await session.scalar(select(Task).where(Task.org_id == a.org.id))
        carrier.recurrence_next_run = today_local() - timedelta(days=1)
        await session.commit()

    spawned = await spawn_scheduled_recurrences({})
    assert spawned == 1

    async with client_for(a.host) as ca:
        items = (await ca.get("/api/v1/tasks", headers=a_headers)).json()["items"]
        assert len(items) == 2
        carrier_rows = [row for row in items if row["recurrence"]]
        assert len(carrier_rows) == 1  # exactly one carrier per chain
        assert carrier_rows[0]["id"] != a_task["id"]

    async with client_for(b.host) as cb:
        items = (await cb.get("/api/v1/tasks", headers=b_headers)).json()["items"]
        assert len(items) == 1  # tenant B untouched
