"""Self-approval of one's own leave is tenant policy, off by default (#110).

While off, an approver is an ordinary owner on their *own* requests: deciding their own
pending request is refused, an approval-relevant edit of their own approved request bounces
back to pending, and their own past is locked like anyone else's. The org's **sole** approver
may always self-manage — a one-person agency must not deadlock. ``leave_settings.self_approval``
restores the trusted-approver behaviour.
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


async def _second_approver(client, headers) -> User:
    """Invite a member and grant them ``leave.request.approve`` through a custom role."""
    user = await _member(client, headers, "approver2@example.com")
    role = await client.post(
        "/api/v1/roles",
        json={
            "key": "approver",
            "permissions": [
                "leave.request.approve",
                "leave.request.read:any",
                "leave.request.write:any",
            ],
        },
        headers=headers,
    )
    assert role.status_code == 201, role.text
    members = (await client.get("/api/v1/members", headers=headers)).json()
    membership = next(m for m in members if m["user_id"] == str(user.id))
    member_role = next(
        r
        for r in (await client.get("/api/v1/roles", headers=headers)).json()
        if r["key"] == "member"
    )
    res = await client.put(
        f"/api/v1/members/{membership['membership_id']}/roles",
        json={"role_ids": [member_role["id"], role.json()["id"]]},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    return user


async def _type_id(client, headers, key: str) -> str:
    types = (await client.get("/api/v1/leave/types", headers=headers)).json()
    return next(t["id"] for t in types if t["key"] == key)


def _span(week: int) -> tuple[str, str]:
    start = leave_workday(week * 5)
    return start.isoformat(), start.isoformat()


def _past_monday() -> date:
    today = date.today()
    return today - timedelta(days=today.weekday() + 7)


async def _own_request(client, headers, type_id, week: int) -> dict:
    start, end = _span(week)
    res = await client.post(
        "/api/v1/leave/requests",
        json={"leave_type_id": type_id, "start_date": start, "end_date": end},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()


async def test_sole_approver_may_self_manage(client_for) -> None:
    """A one-person agency must not deadlock: the only approver decides their own leave."""
    t = await make_tenant("selfapp-sole")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        special = await _type_id(c, headers, "special")
        created = await _own_request(c, headers, special, 0)
        decided = await c.post(
            f"/api/v1/leave/requests/{created['id']}/decide",
            json={"approved": True},
            headers=headers,
        )
        assert decided.status_code == 200, decided.text
        assert decided.json()["status"] == "approved"


async def test_own_decide_needs_the_other_approver(client_for) -> None:
    t = await make_tenant("selfapp-decide")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        second = await _second_approver(c, owner)
        special = await _type_id(c, owner, "special")
        created = await _own_request(c, owner, special, 0)

        refused = await c.post(
            f"/api/v1/leave/requests/{created['id']}/decide",
            json={"approved": True},
            headers=owner,
        )
        assert refused.status_code == 403
        assert refused.json()["error"]["message"] == "errors.leave_self_approval"

        other = await auth_cookie(second)
        decided = await c.post(
            f"/api/v1/leave/requests/{created['id']}/decide",
            json={"approved": True},
            headers=other,
        )
        assert decided.status_code == 200, decided.text
        assert decided.json()["status"] == "approved"


async def test_own_edit_bounces_to_pending_like_anyone_elses(client_for) -> None:
    """The edit path enforces the same policy as decide — or the control is sidestepped."""
    t = await make_tenant("selfapp-edit")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        second = await _second_approver(c, owner)
        other = await auth_cookie(second)
        special = await _type_id(c, owner, "special")
        created = await _own_request(c, owner, special, 0)
        await c.post(
            f"/api/v1/leave/requests/{created['id']}/decide",
            json={"approved": True},
            headers=other,
        )

        new_start, new_end = _span(1)
        edited = await c.patch(
            f"/api/v1/leave/requests/{created['id']}",
            json={"start_date": new_start, "end_date": new_end},
            headers=owner,
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["status"] == "pending"
        assert edited.json()["decided_by_user_id"] is None

        # The *other* approver's edit of someone else's request still stands as approval.
        await c.post(
            f"/api/v1/leave/requests/{created['id']}/decide",
            json={"approved": True},
            headers=other,
        )
        third_start, third_end = _span(2)
        edited = await c.patch(
            f"/api/v1/leave/requests/{created['id']}",
            json={"start_date": third_start, "end_date": third_end},
            headers=other,
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["status"] == "approved"


async def test_own_past_is_locked_while_self_approval_is_off(client_for) -> None:
    t = await make_tenant("selfapp-past")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _second_approver(c, owner)
        special = await _type_id(c, owner, "special")
        past = _past_monday().isoformat()

        res = await c.post(
            "/api/v1/leave/requests",
            json={
                "leave_type_id": special,
                "start_date": past,
                "end_date": past,
                "hours_override": 8,
            },
            headers=owner,
        )
        assert res.status_code == 403
        assert res.json()["error"]["message"] == "errors.leave_past_locked"


async def test_toggle_restores_trusted_approver_behaviour(client_for) -> None:
    t = await make_tenant("selfapp-toggle")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        second = await _second_approver(c, owner)
        other = await auth_cookie(second)
        assert other is not None  # the second approver exists; the toggle is what allows self

        res = await c.put(
            "/api/v1/leave/settings", json={"self_approval": True}, headers=owner
        )
        assert res.status_code == 200, res.text
        assert res.json()["self_approval"] is True

        special = await _type_id(c, owner, "special")
        created = await _own_request(c, owner, special, 0)
        decided = await c.post(
            f"/api/v1/leave/requests/{created['id']}/decide",
            json={"approved": True},
            headers=owner,
        )
        assert decided.status_code == 200
        assert decided.json()["status"] == "approved"

        # Own edit stays approved (their edit is the approval), and own backdating works.
        new_start, new_end = _span(1)
        edited = await c.patch(
            f"/api/v1/leave/requests/{created['id']}",
            json={"start_date": new_start, "end_date": new_end},
            headers=owner,
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["status"] == "approved"

        past = _past_monday().isoformat()
        res = await c.post(
            "/api/v1/leave/requests",
            json={
                "leave_type_id": special,
                "start_date": past,
                "end_date": past,
                "hours_override": 8,
            },
            headers=owner,
        )
        assert res.status_code == 201, res.text
