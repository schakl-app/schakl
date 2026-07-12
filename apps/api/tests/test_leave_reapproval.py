"""Editing a leave request re-triggers approval — the #72 matrix.

An edit needs (re-)approval **if the type required approval, or the edit touches the past** —
otherwise the owner may change their own future, self-service leave freely. These tests walk
each cell (future/past × requires-approval/auto-approve) plus the no-op-edit-doesn't-bounce case.

``special`` (requires_approval, no balance) and ``sick`` (auto-approve) let the flow run without
setting up entitlements.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from app.core.auth.models import User
from tests.conftest import auth_cookie, leave_workday, make_tenant


async def _member(client, headers, email: str) -> User:
    res = await client.post(
        "/api/v1/members/invite",
        json={"email": email, "full_name": "Member", "role": "member"},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return User(
        id=uuid.UUID(res.json()["user_id"]), email=email, hashed_password="", is_active=True
    )


async def _type_id(client, headers, key: str) -> str:
    types = (await client.get("/api/v1/leave/types", headers=headers)).json()
    return next(t["id"] for t in types if t["key"] == key)


def _span(week: int) -> tuple[str, str]:
    start = leave_workday(week * 5)
    return start.isoformat(), start.isoformat()


async def test_future_requires_approval_edit_bounces_to_pending(client_for) -> None:
    """The manager approved a shape; the member changes it → back to pending, decision cleared."""
    t = await make_tenant("reapprove-future-req")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, owner, "m@example.com")
        mh = await auth_cookie(member)
        special = await _type_id(c, owner, "special")

        start, end = _span(0)
        created = await c.post(
            "/api/v1/leave/requests",
            json={"leave_type_id": special, "start_date": start, "end_date": end},
            headers=mh,
        )
        assert created.status_code == 201
        rid = created.json()["id"]
        assert created.json()["status"] == "pending"

        # Manager approves.
        approved = await c.post(
            f"/api/v1/leave/requests/{rid}/decide", json={"approved": True}, headers=owner
        )
        assert approved.status_code == 200
        assert approved.json()["status"] == "approved"

        # Member moves it a week later → must go back through approval.
        new_start, new_end = _span(1)
        edited = await c.patch(
            f"/api/v1/leave/requests/{rid}",
            json={"start_date": new_start, "end_date": new_end},
            headers=mh,
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["status"] == "pending"
        assert edited.json()["decided_by_user_id"] is None
        assert edited.json()["decided_at"] is None
        # The bounce is marked as a re-submission so the approval queue can say "edit to
        # existing leave" rather than showing it like new (#120)…
        assert edited.json()["resubmitted_at"] is not None

        # …and the next decision clears the marker.
        redecided = await c.post(
            f"/api/v1/leave/requests/{rid}/decide", json={"approved": True}, headers=owner
        )
        assert redecided.status_code == 200
        assert redecided.json()["resubmitted_at"] is None


async def test_future_auto_approve_owner_edits_freely(client_for) -> None:
    """A free day / sick report the member owns, entirely in the future: stays registered."""
    t = await make_tenant("reapprove-future-auto")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, owner, "m@example.com")
        mh = await auth_cookie(member)
        sick = await _type_id(c, owner, "sick")

        start, end = _span(0)
        created = await c.post(
            "/api/v1/leave/requests",
            json={"leave_type_id": sick, "start_date": start, "end_date": end},
            headers=mh,
        )
        assert created.status_code == 201
        assert created.json()["status"] == "approved"  # auto-approve type
        rid = created.json()["id"]

        # Owner moves their own future free day → stays approved, no manager involved.
        new_start, new_end = _span(1)
        edited = await c.patch(
            f"/api/v1/leave/requests/{rid}",
            json={"start_date": new_start, "end_date": new_end},
            headers=mh,
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["status"] == "approved"


async def test_note_only_edit_does_not_bounce(client_for) -> None:
    """A no-op-for-approval edit (just the note) must not knock an approved request to pending."""
    t = await make_tenant("reapprove-noop")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, owner, "m@example.com")
        mh = await auth_cookie(member)
        special = await _type_id(c, owner, "special")

        start, end = _span(0)
        rid = (
            await c.post(
                "/api/v1/leave/requests",
                json={"leave_type_id": special, "start_date": start, "end_date": end},
                headers=mh,
            )
        ).json()["id"]
        await c.post(f"/api/v1/leave/requests/{rid}/decide", json={"approved": True}, headers=owner)

        edited = await c.patch(
            f"/api/v1/leave/requests/{rid}", json={"note": "fyi"}, headers=mh
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["status"] == "approved"
        assert edited.json()["note"] == "fyi"


async def test_fresh_pending_edit_is_not_marked_resubmitted(client_for) -> None:
    """A never-approved pending request that gets edited is *not* an edit to approved leave —
    only the approved→pending bounce may set the marker (#120)."""
    t = await make_tenant("reapprove-fresh")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, owner, "m@example.com")
        mh = await auth_cookie(member)
        special = await _type_id(c, owner, "special")

        start, end = _span(0)
        created = await c.post(
            "/api/v1/leave/requests",
            json={"leave_type_id": special, "start_date": start, "end_date": end},
            headers=mh,
        )
        rid = created.json()["id"]
        assert created.json()["status"] == "pending"
        assert created.json()["resubmitted_at"] is None

        new_start, new_end = _span(1)
        edited = await c.patch(
            f"/api/v1/leave/requests/{rid}",
            json={"start_date": new_start, "end_date": new_end},
            headers=mh,
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["status"] == "pending"
        assert edited.json()["resubmitted_at"] is None


async def test_approver_edit_stays_in_place(client_for) -> None:
    """An approver fixing an approved request keeps it approved — their edit is the approval."""
    t = await make_tenant("reapprove-approver")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, owner, "m@example.com")
        mh = await auth_cookie(member)
        special = await _type_id(c, owner, "special")

        start, end = _span(0)
        rid = (
            await c.post(
                "/api/v1/leave/requests",
                json={"leave_type_id": special, "start_date": start, "end_date": end},
                headers=mh,
            )
        ).json()["id"]
        await c.post(f"/api/v1/leave/requests/{rid}/decide", json={"approved": True}, headers=owner)

        new_start, new_end = _span(1)
        edited = await c.patch(
            f"/api/v1/leave/requests/{rid}",
            json={"start_date": new_start, "end_date": new_end},
            headers=owner,
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["status"] == "approved"


async def test_preview_reports_whether_approval_is_needed(client_for) -> None:
    """The form learns before submit whether a save will (re-)require approval (#72)."""
    t = await make_tenant("reapprove-preview")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        special = await _type_id(c, owner, "special")
        sick = await _type_id(c, owner, "sick")
        future_start, future_end = _span(0)
        # A week ago — always before today. Preview never rejects zero-hour spans, so its exact
        # weekday does not matter; only that it reaches the past.
        past = date.today() - timedelta(days=7)

        # Future + requires-approval → needs approval.
        res = await c.post(
            "/api/v1/leave/requests/preview",
            json={"leave_type_id": special, "start_date": future_start, "end_date": future_end},
            headers=owner,
        )
        assert res.json()["requires_approval"] is True
        assert res.json()["touches_past"] is False

        # Future + auto-approve → self-service, no approval.
        res = await c.post(
            "/api/v1/leave/requests/preview",
            json={"leave_type_id": sick, "start_date": future_start, "end_date": future_end},
            headers=owner,
        )
        assert res.json()["requires_approval"] is False

        # Past + auto-approve → a retroactive change is a manager's call.
        res = await c.post(
            "/api/v1/leave/requests/preview",
            json={
                "leave_type_id": sick,
                "start_date": past.isoformat(),
                "end_date": past.isoformat(),
            },
            headers=owner,
        )
        assert res.json()["touches_past"] is True
        assert res.json()["requires_approval"] is True
