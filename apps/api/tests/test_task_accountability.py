"""Deadline accountability, allocated time, URL attachments, invoice-implies-approve."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.modules.tasks.recurrence import today_local
from tests.conftest import auth_cookie, make_tenant


async def test_due_extension_requires_reason(client_for) -> None:
    t = await make_tenant("acct-due")
    headers = await auth_cookie(t.user)
    today = today_local()
    async with client_for(t.host) as c:
        task = (
            await c.post(
                "/api/v1/tasks",
                json={"title": "Deadline", "due_date": today.isoformat()},
                headers=headers,
            )
        ).json()

        later = (today + timedelta(days=3)).isoformat()
        # Pushing the deadline back without a reason is rejected…
        blocked = await c.patch(
            f"/api/v1/tasks/{task['id']}", json={"due_date": later}, headers=headers
        )
        assert blocked.status_code == 422
        assert blocked.json()["error"]["fields"]["due_change_reason"] == (
            "errors.due_reason_required"
        )

        # …with a reason it succeeds and lands in the activity feed.
        ok = await c.patch(
            f"/api/v1/tasks/{task['id']}",
            json={"due_date": later, "due_change_reason": "Klant leverde input te laat"},
            headers=headers,
        )
        assert ok.status_code == 200

        detail = (await c.get(f"/api/v1/tasks/{task['id']}", headers=headers)).json()
        extension = next(a for a in detail["activities"] if a["action"] == "due_extended")
        assert extension["payload"]["reason"] == "Klant leverde input te laat"
        assert extension["payload"]["to"] == later

        # Moving a deadline EARLIER needs no reason.
        earlier = today.isoformat()
        assert (
            await c.patch(
                f"/api/v1/tasks/{task['id']}", json={"due_date": earlier}, headers=headers
            )
        ).status_code == 200


async def test_allocated_minutes_and_logged(client_for) -> None:
    t = await make_tenant("acct-alloc")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        task = (
            await c.post(
                "/api/v1/tasks",
                json={"title": "Budgeted", "allocated_minutes": 120},
                headers=headers,
            )
        ).json()
        assert task["allocated_minutes"] == 120

        await c.post(
            "/api/v1/time/entries",
            json={
                "started_at": datetime.now(UTC).isoformat(),
                "minutes": 45,
                "task_id": task["id"],
            },
            headers=headers,
        )
        detail = (await c.get(f"/api/v1/tasks/{task['id']}", headers=headers)).json()
        assert detail["logged_minutes"] == 45

        logged = (
            await c.get("/api/v1/time/logged", params={"task_id": task["id"]}, headers=headers)
        ).json()
        assert logged["minutes"] == 45


async def test_task_links_crud_and_isolation(client_for) -> None:
    a = await make_tenant("acct-links-a")
    b = await make_tenant("acct-links-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)

    async with client_for(a.host) as ca:
        task = (await ca.post("/api/v1/tasks", json={"title": "L"}, headers=a_headers)).json()
        link = (
            await ca.post(
                f"/api/v1/tasks/{task['id']}/links",
                json={"url": "figma.com/file/abc", "title": "Design"},
                headers=a_headers,
            )
        ).json()
        assert link["url"] == "https://figma.com/file/abc"  # scheme added

        detail = (await ca.get(f"/api/v1/tasks/{task['id']}", headers=a_headers)).json()
        assert [row["title"] for row in detail["links"]] == ["Design"]

    async with client_for(b.host) as cb:
        assert (
            await cb.post(
                f"/api/v1/tasks/{task['id']}/links",
                json={"url": "https://spy.example"},
                headers=b_headers,
            )
        ).status_code == 404
        assert (
            await cb.delete(
                f"/api/v1/tasks/{task['id']}/links/{link['id']}", headers=b_headers
            )
        ).status_code == 404

    async with client_for(a.host) as ca:
        assert (
            await ca.delete(
                f"/api/v1/tasks/{task['id']}/links/{link['id']}", headers=a_headers
            )
        ).status_code == 204


async def test_invoice_implies_approve(client_for) -> None:
    t = await make_tenant("acct-inv")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        entry = (
            await c.post(
                "/api/v1/time/entries",
                json={"started_at": datetime.now(UTC).isoformat(), "minutes": 60},
                headers=headers,
            )
        ).json()
        assert entry["approved_at"] is None

        await c.post(
            "/api/v1/time/entries/invoice",
            json={"entry_ids": [entry["id"]], "invoiced": True},
            headers=headers,
        )
        fetched = (await c.get(f"/api/v1/time/entries/{entry['id']}", headers=headers)).json()
        assert fetched["invoiced_at"] is not None
        assert fetched["approved_at"] is not None  # invoicing auto-approved it
