"""Recurring tasks: date math, after-completion spawn, scheduled cron spawn, isolation."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select

from app.db import async_session_maker, set_current_org
from app.modules.tasks.models import Task
from app.modules.tasks.recurrence import advance, spawn_scheduled_recurrences
from tests.conftest import FAR_FUTURE_DUE, auth_cookie, default_company, make_tenant, org_today


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
    yesterday = (org_today() - timedelta(days=1)).isoformat()

    async with client_for(t.host) as c:
        task = (
            await c.post(
                "/api/v1/tasks",
                json={
                    "company_id": await default_company(c, headers),
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
        assert date.fromisoformat(clone["due_date"]) > org_today()
        # Checklist copied with items reset to not-done.
        assert (clone["checklist_done"], clone["checklist_total"]) == (0, 1)

        detail = (await c.get(f"/api/v1/tasks/{clone['id']}", headers=headers)).json()
        assert any(a["action"] == "recurrence_spawned" for a in detail["activities"])


async def test_scheduled_cron_lays_the_year_out_per_org_isolated(client_for) -> None:
    """Schedule mode is a series now: the root keeps the rule and the sweep lays out every
    occurrence inside the year ahead, each naming the root — and only for the org it is
    sweeping."""
    a = await make_tenant("rec-cron-a")
    b = await make_tenant("rec-cron-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)

    async with client_for(a.host) as ca:
        a_task = (
            await ca.post(
                "/api/v1/tasks",
                json={
                    "company_id": await default_company(ca, a_headers),
                    "due_date": FAR_FUTURE_DUE,
                    "title": "Weekly digest",
                    "recurrence": {"freq": "weekly", "interval": 1, "mode": "schedule"},
                },
                headers=a_headers,
            )
        ).json()
        assert a_task["recurrence"]["mode"] == "schedule"
    async with client_for(b.host) as cb:
        await cb.post(
            "/api/v1/tasks",
            json={
                "company_id": await default_company(cb, b_headers),
                "due_date": FAR_FUTURE_DUE, "title": "No recurrence",
            },
            headers=b_headers,
        )

    # A root due in 2099 has nothing inside the horizon; pull its pointer back so the sweep
    # has a year to fill.
    async with async_session_maker() as session:
        await set_current_org(session, a.org.id)
        root = await session.scalar(select(Task).where(Task.org_id == a.org.id))
        root.recurrence_next_run = org_today() - timedelta(days=1)
        await session.commit()

    spawned = await spawn_scheduled_recurrences({})
    assert spawned in (52, 53)

    async with client_for(a.host) as ca:
        items = (await ca.get("/api/v1/tasks?limit=200", headers=a_headers)).json()["items"]
        assert len(items) == 1 + spawned
        roots = [row for row in items if row["recurrence"]]
        assert [row["id"] for row in roots] == [a_task["id"]]  # the root keeps the rule
        occurrences = [row for row in items if row["recurrence_source_id"]]
        assert len(occurrences) == spawned
        assert {row["recurrence_source_id"] for row in occurrences} == {a_task["id"]}
        # Never before today, never past the horizon, and ordered a week apart.
        dues = sorted(date.fromisoformat(row["due_date"]) for row in occurrences)
        assert dues[0] >= org_today()
        assert dues[-1] <= org_today() + timedelta(days=365)
        assert all((b - a).days == 7 for a, b in zip(dues, dues[1:], strict=False))
        # Idempotent: a second sweep the same night adds nothing.
        assert await spawn_scheduled_recurrences({}) == 0

    async with client_for(b.host) as cb:
        items = (await cb.get("/api/v1/tasks", headers=b_headers)).json()["items"]
        assert len(items) == 1  # tenant B untouched
