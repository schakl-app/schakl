"""Editing a leave request and when it re-triggers approval (#72, CLAUDE.md §14, §15).

The rule: editing an **approved** request bounces it back to ``pending`` when the leave type
requires approval or the edit touches the past — unless the editor may approve leave themselves,
in which case their edit stands. The one relaxation is a request-owner nudging their own future
auto-approve leave, which stays approved and needs nobody.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from app.core.auth.models import User
from app.modules.leave import holidays as hol
from tests.conftest import auth_cookie, leave_workday, make_tenant


async def _invite_member(client, headers, email: str) -> User:
    res = await client.post(
        "/api/v1/members/invite",
        json={"email": email, "full_name": "Member", "role": "member"},
        headers=headers,
    )
    assert res.status_code == 201
    return User(
        id=uuid.UUID(res.json()["user_id"]), email=email, hashed_password="", is_active=True
    )


def _span(index: int, length: int = 1) -> dict[str, str]:
    """``length`` weekdays from the ``index``-th November workday — always a full 8-hour day."""
    start = leave_workday(index)
    return {
        "start_date": start.isoformat(),
        "end_date": (start + timedelta(days=length - 1)).isoformat(),
    }


def _past_workday() -> date:
    """A weekday at least two weeks back that is not a Dutch public holiday — so its hours are
    positive and the edit is rejected for *touching the past*, never for having no working hours.
    """
    today = date.today()
    blocked = {h.day for h in hol.generate(hol.COUNTRY_NL, today.year)}
    blocked |= {h.day for h in hol.generate(hol.COUNTRY_NL, today.year - 1)}
    day = today - timedelta(days=14)
    while day.weekday() >= 5 or day in blocked:
        day -= timedelta(days=1)
    return day


async def _type(client, headers, key: str) -> dict:
    types = (await client.get("/api/v1/leave/types", headers=headers)).json()
    return next(lt for lt in types if lt["key"] == key)


async def _create(client, headers, type_id: str, span: dict) -> dict:
    res = await client.post(
        "/api/v1/leave/requests",
        json={"leave_type_id": type_id, **span},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()


async def test_future_auto_approve_edit_stays_approved(client_for) -> None:
    """A free day, moved to another free day: no manager, no re-approval (#72)."""
    t = await make_tenant("leave-edit-free")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _invite_member(c, owner, "free@example.com")
        member_h = await auth_cookie(member)
        sick = await _type(c, owner, "sick")  # auto-approve

        req = await _create(c, member_h, sick["id"], _span(0))
        assert req["status"] == "approved"

        res = await c.patch(
            f"/api/v1/leave/requests/{req['id']}",
            json=_span(1),
            headers=member_h,
        )
        assert res.status_code == 200
        assert res.json()["status"] == "approved"


async def test_future_requires_approval_edit_bounces_to_pending(client_for) -> None:
    """Editing approved leave of a type that needs approval re-opens the decision (#72)."""
    t = await make_tenant("leave-edit-bounce")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _invite_member(c, owner, "bounce@example.com")
        member_h = await auth_cookie(member)
        special = await _type(c, owner, "special")  # requires approval, no balance

        req = await _create(c, member_h, special["id"], _span(0))
        assert req["status"] == "pending"
        decided = await c.post(
            f"/api/v1/leave/requests/{req['id']}/decide",
            json={"approved": True},
            headers=owner,
        )
        assert decided.json()["status"] == "approved"
        assert decided.json()["decided_by_user_id"] == str(t.user.id)

        # The member reshapes their own approved leave → back to pending, decision cleared.
        res = await c.patch(
            f"/api/v1/leave/requests/{req['id']}",
            json=_span(1),
            headers=member_h,
        )
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "pending"
        assert body["decided_by_user_id"] is None
        assert body["decided_at"] is None


async def test_edit_into_the_past_bounces_even_when_auto_approve(client_for) -> None:
    """Touching the past always re-triggers approval, whatever the type says (#72)."""
    t = await make_tenant("leave-edit-past")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _invite_member(c, owner, "past@example.com")
        member_h = await auth_cookie(member)
        sick = await _type(c, owner, "sick")  # auto-approve — yet the past still needs review

        req = await _create(c, member_h, sick["id"], _span(0))
        assert req["status"] == "approved"

        past = _past_workday().isoformat()
        res = await c.patch(
            f"/api/v1/leave/requests/{req['id']}",
            json={"start_date": past, "end_date": past},
            headers=member_h,
        )
        assert res.status_code == 200
        assert res.json()["status"] == "pending"


async def test_manager_edit_of_approved_leave_stays_approved(client_for) -> None:
    """An approver's edit stands — they need not re-approve their own change (#72)."""
    t = await make_tenant("leave-edit-mgr")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _invite_member(c, owner, "mgr@example.com")
        member_h = await auth_cookie(member)
        special = await _type(c, owner, "special")

        req = await _create(c, member_h, special["id"], _span(0))
        await c.post(
            f"/api/v1/leave/requests/{req['id']}/decide",
            json={"approved": True},
            headers=owner,
        )
        # Owner edits it — even into the past — and it stays approved.
        past = _past_workday().isoformat()
        res = await c.patch(
            f"/api/v1/leave/requests/{req['id']}",
            json={"start_date": past, "end_date": past},
            headers=owner,
        )
        assert res.status_code == 200
        assert res.json()["status"] == "approved"


async def test_note_only_edit_keeps_approval(client_for) -> None:
    """A note is not approval-relevant, so a note-only edit never unapproves (#72)."""
    t = await make_tenant("leave-edit-note")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _invite_member(c, owner, "note@example.com")
        member_h = await auth_cookie(member)
        special = await _type(c, owner, "special")

        req = await _create(c, member_h, special["id"], _span(0))
        await c.post(
            f"/api/v1/leave/requests/{req['id']}/decide",
            json={"approved": True},
            headers=owner,
        )
        res = await c.patch(
            f"/api/v1/leave/requests/{req['id']}",
            json={"note": "context for HR"},
            headers=member_h,
        )
        assert res.status_code == 200
        assert res.json()["status"] == "approved"
        assert res.json()["note"] == "context for HR"


async def test_member_cannot_cancel_own_past_approved_leave(client_for) -> None:
    """Future approved leave is a plan a member may drop; the past is a record they may not."""
    t = await make_tenant("leave-cancel-past")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _invite_member(c, owner, "cancelpast@example.com")
        member_h = await auth_cookie(member)
        sick = await _type(c, owner, "sick")

        # A manager registers a past sick day for the member → approved, and in the past.
        past = _past_workday().isoformat()
        registered = await c.post(
            "/api/v1/leave/requests",
            json={
                "leave_type_id": sick["id"],
                "start_date": past,
                "end_date": past,
                "user_id": str(member.id),
            },
            headers=owner,
        )
        assert registered.status_code == 201
        assert registered.json()["status"] == "approved"

        res = await c.post(
            f"/api/v1/leave/requests/{registered.json()['id']}/cancel", headers=member_h
        )
        assert res.status_code == 403
        assert res.json()["error"]["message"] == "errors.approved_locked"


async def test_preview_flags_a_past_span(client_for) -> None:
    """The preview tells the form a span touches the past, so it can warn before saving (#72)."""
    t = await make_tenant("leave-preview-past")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        past = _past_workday().isoformat()
        res = await c.post(
            "/api/v1/leave/requests/preview",
            json={"start_date": past, "end_date": past},
            headers=owner,
        )
        assert res.status_code == 200
        assert res.json()["touches_past"] is True

        res = await c.post(
            "/api/v1/leave/requests/preview",
            json=_span(0),
            headers=owner,
        )
        assert res.json()["touches_past"] is False
