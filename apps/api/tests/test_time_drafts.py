"""Autosaved time-entry drafts (#44): author-only access, upsert/discard, ride-alongs.

A draft is a user's keystrokes, not a record — stricter than the rest of the platform: even an
owner must not read a member's draft, and no aggregate may count one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.conftest import auth_cookie, make_tenant
from tests.test_task_subresources import add_member


def _monday() -> str:
    today = datetime.now(UTC).date()
    return (today - timedelta(days=today.weekday())).isoformat()


async def test_draft_upsert_rides_day_and_timesheet(client_for) -> None:
    t = await make_tenant("draft-basic")
    headers = await auth_cookie(t.user)
    day = _monday()
    async with client_for(t.host) as c:
        # No draft yet.
        view = (await c.get("/api/v1/time/day", params={"date": day}, headers=headers)).json()
        assert view["draft"] is None

        saved = await c.put(
            f"/api/v1/time/drafts/{day}",
            json={"start": "09:00", "duration_text": "1,", "description": "half a thought"},
            headers=headers,
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["payload"]["duration_text"] == "1,"

        # Upsert, not insert: a second save updates the same row.
        again = await c.put(
            f"/api/v1/time/drafts/{day}", json={"start": "09:15"}, headers=headers
        )
        assert again.status_code == 200

        # The draft rides the day view and marks the timesheet tab — zero extra calls.
        view = (await c.get("/api/v1/time/day", params={"date": day}, headers=headers)).json()
        assert view["draft"]["payload"]["start"] == "09:15"
        sheet = (
            await c.get("/api/v1/time/timesheet", params={"week_start": day}, headers=headers)
        ).json()
        assert day in sheet["draft_days"]

        # Unknown keys are rejected — the payload is a closed shape.
        assert (
            await c.put(f"/api/v1/time/drafts/{day}", json={"evil": "x"}, headers=headers)
        ).status_code == 422

        # Discard, idempotently.
        assert (await c.delete(f"/api/v1/time/drafts/{day}", headers=headers)).status_code == 204
        assert (await c.delete(f"/api/v1/time/drafts/{day}", headers=headers)).status_code == 204
        view = (await c.get("/api/v1/time/day", params={"date": day}, headers=headers)).json()
        assert view["draft"] is None


async def test_draft_is_author_only_even_for_owners(client_for) -> None:
    """Stricter than :any — a manager viewing a member's day never sees their draft."""
    t = await make_tenant("draft-author")
    owner_headers = await auth_cookie(t.user)
    member = await add_member(t)
    member_headers = await auth_cookie(member)
    day = _monday()
    async with client_for(t.host) as c:
        await c.put(
            f"/api/v1/time/drafts/{day}",
            json={"description": "private keystrokes"},
            headers=member_headers,
        )

        # The owner viewing the member's day: entries yes, draft no.
        view = (
            await c.get(
                "/api/v1/time/day",
                params={"date": day, "user_id": str(member.id)},
                headers=owner_headers,
            )
        ).json()
        assert view["draft"] is None
        sheet = (
            await c.get(
                "/api/v1/time/timesheet",
                params={"week_start": day, "user_id": str(member.id)},
                headers=owner_headers,
            )
        ).json()
        assert sheet["draft_days"] == []

        # And the owner's own surfaces don't show it either.
        own = (
            await c.get("/api/v1/time/day", params={"date": day}, headers=owner_headers)
        ).json()
        assert own["draft"] is None


async def test_entry_create_clears_the_draft(client_for) -> None:
    t = await make_tenant("draft-clear")
    headers = await auth_cookie(t.user)
    day = _monday()
    async with client_for(t.host) as c:
        await c.put(f"/api/v1/time/drafts/{day}", json={"start": "09:00"}, headers=headers)
        created = await c.post(
            "/api/v1/time/entries",
            json={"started_at": f"{day}T09:00:00Z", "ended_at": f"{day}T10:00:00Z"},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        view = (await c.get("/api/v1/time/day", params={"date": day}, headers=headers)).json()
        assert view["draft"] is None


async def test_draft_tenant_isolation(client_for) -> None:
    a = await make_tenant("draft-iso-a")
    b = await make_tenant("draft-iso-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)
    day = _monday()
    async with client_for(a.host) as ca:
        await ca.put(
            f"/api/v1/time/drafts/{day}", json={"description": "tenant a"}, headers=a_headers
        )
    async with client_for(b.host) as cb:
        view = (await cb.get("/api/v1/time/day", params={"date": day}, headers=b_headers)).json()
        assert view["draft"] is None
