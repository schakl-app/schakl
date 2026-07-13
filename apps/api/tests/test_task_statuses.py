"""Tenant-configurable task statuses (issue #62): seeding, CRUD, semantics, isolation."""

from __future__ import annotations

from tests.conftest import auth_cookie, make_tenant


async def test_statuses_seeded_on_first_read(client_for) -> None:
    """A fresh org gets the default open/in_progress/done vocabulary with no setup step."""
    t = await make_tenant("status-seed")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        statuses = (await c.get("/api/v1/tasks/statuses", headers=headers)).json()
        keys = [s["key"] for s in statuses]
        assert keys == ["open", "in_progress", "done"]
        assert next(s for s in statuses if s["key"] == "open")["is_default"] is True
        assert next(s for s in statuses if s["key"] == "done")["is_terminal"] is True


async def test_new_task_lands_in_default_status(client_for) -> None:
    t = await make_tenant("status-default")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        task = (await c.post("/api/v1/tasks", json={"title": "T"}, headers=headers)).json()
        assert task["status"] == "open"
        assert task["completed_at"] is None


async def test_completed_at_keys_off_is_terminal(client_for) -> None:
    """Moving into any terminal status stamps completed_at; leaving it clears it."""
    t = await make_tenant("status-terminal")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        # A custom terminal status, distinct from the seeded "done".
        shipped = (
            await c.post(
                "/api/v1/tasks/statuses",
                json={"key": "shipped", "name": "Shipped", "color": "teal", "is_terminal": True},
                headers=headers,
            )
        ).json()
        assert shipped["is_terminal"] is True

        task = (await c.post("/api/v1/tasks", json={"title": "T"}, headers=headers)).json()
        done = (
            await c.patch(
                f"/api/v1/tasks/{task['id']}", json={"status": "shipped"}, headers=headers
            )
        ).json()
        assert done["status"] == "shipped"
        assert done["completed_at"] is not None

        reopened = (
            await c.patch(
                f"/api/v1/tasks/{task['id']}", json={"status": "open"}, headers=headers
            )
        ).json()
        assert reopened["completed_at"] is None


async def test_unknown_status_key_is_rejected(client_for) -> None:
    t = await make_tenant("status-reject")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        resp = await c.post(
            "/api/v1/tasks", json={"title": "T", "status": "nope"}, headers=headers
        )
        assert resp.status_code == 422


async def test_single_default_and_delete_guards(client_for) -> None:
    t = await make_tenant("status-guards")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        # Making a new status the default clears the flag on the previous one.
        review = (
            await c.post(
                "/api/v1/tasks/statuses",
                json={"key": "review", "name": "Review", "color": "amber", "is_default": True},
                headers=headers,
            )
        ).json()
        statuses = (await c.get("/api/v1/tasks/statuses", headers=headers)).json()
        defaults = [s["key"] for s in statuses if s["is_default"]]
        assert defaults == ["review"]

        # A status still holding a task can't be deleted.
        await c.post("/api/v1/tasks", json={"title": "T", "status": "review"}, headers=headers)
        blocked = await c.delete(f"/api/v1/tasks/statuses/{review['id']}", headers=headers)
        assert blocked.status_code == 409


async def test_statuses_are_tenant_isolated(client_for) -> None:
    a = await make_tenant("status-iso-a")
    b = await make_tenant("status-iso-b")
    ha = await auth_cookie(a.user)
    hb = await auth_cookie(b.user)
    async with client_for(a.host) as c:
        await c.post(
            "/api/v1/tasks/statuses",
            json={"key": "blocked", "name": "Blocked", "color": "red"},
            headers=ha,
        )
    async with client_for(b.host) as c:
        keys = [s["key"] for s in (await c.get("/api/v1/tasks/statuses", headers=hb)).json()]
        assert "blocked" not in keys


async def test_closing_interaction_gate_and_designation(client_for) -> None:
    """#157: a status flagged requires_interaction cannot be entered without a designated
    closing contact moment; the moment must be linked to *this* task and team-visible;
    reopening clears the designation so the next close picks afresh."""
    from datetime import UTC, datetime

    t = await make_tenant("status-closing")
    headers = await auth_cookie(t.user)
    now = datetime(2026, 7, 10, 14, 30, tzinfo=UTC).isoformat()
    async with client_for(t.host) as c:
        statuses = (await c.get("/api/v1/tasks/statuses", headers=headers)).json()
        done = next(s for s in statuses if s["key"] == "done")
        flagged = await c.patch(
            f"/api/v1/tasks/statuses/{done['id']}",
            json={"requires_interaction": True},
            headers=headers,
        )
        assert flagged.status_code == 200 and flagged.json()["requires_interaction"] is True

        task = (await c.post("/api/v1/tasks", json={"title": "Bel klant"}, headers=headers)).json()
        other_task = (await c.post("/api/v1/tasks", json={"title": "Andere"}, headers=headers)).json()

        # No contact moment: the gate refuses.
        refused = await c.patch(
            f"/api/v1/tasks/{task['id']}", json={"status": "done"}, headers=headers
        )
        assert refused.status_code == 422
        assert (
            refused.json()["error"]["fields"]["status"]
            == "errors.tasks_closing_interaction_required"
        )

        moment = (
            await c.post(
                "/api/v1/interactions",
                json={
                    "kind": "call",
                    "occurred_at": now,
                    "subject": "Afgestemd met klant",
                    "task_id": task["id"],
                },
                headers=headers,
            )
        ).json()

        # A moment linked to a *different* task never satisfies the gate.
        wrong = await c.patch(
            f"/api/v1/tasks/{other_task['id']}",
            json={"status": "done", "closing_interaction_id": moment["id"]},
            headers=headers,
        )
        assert wrong.status_code == 422

        closed = await c.patch(
            f"/api/v1/tasks/{task['id']}",
            json={"status": "done", "closing_interaction_id": moment["id"]},
            headers=headers,
        )
        assert closed.status_code == 200, closed.text
        body = closed.json()
        assert body["closing_interaction_id"] == moment["id"]
        assert body["completed_at"] is not None

        # Reopening clears the designation — last year's phone call can't close it again.
        reopened = (
            await c.patch(
                f"/api/v1/tasks/{task['id']}", json={"status": "open"}, headers=headers
            )
        ).json()
        assert reopened["closing_interaction_id"] is None
        again = await c.patch(
            f"/api/v1/tasks/{task['id']}", json={"status": "done"}, headers=headers
        )
        assert again.status_code == 422
