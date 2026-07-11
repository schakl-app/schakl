"""leave module API coverage (CLAUDE.md §6, §14): types, balances, request + approval flow."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from app.core.auth.models import User
from tests.conftest import auth_cookie, leave_workday, make_tenant

_YEAR = date.today().year


async def _invite_member(client, headers, email: str) -> User:
    """Invite a plain member via the API; return a handle usable with auth_cookie."""
    res = await client.post(
        "/api/v1/members/invite",
        json={"email": email, "full_name": "Member", "role": "member"},
        headers=headers,
    )
    assert res.status_code == 201
    data = res.json()
    return User(
        id=uuid.UUID(data["user_id"]), email=email, hashed_password="", is_active=True
    )


def _span(week: int, length: int = 1) -> tuple[str, str]:
    """`length` consecutive weekdays, starting `week` weeks into November (see `leave_workday`)."""
    start = leave_workday(week * 5)
    return start.isoformat(), (start + timedelta(days=length - 1)).isoformat()


async def test_default_types_seeded(client_for) -> None:
    t = await make_tenant("leave-types")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        res = await c.get("/api/v1/leave/types", headers=headers)
        assert res.status_code == 200
        keys = {lt["key"] for lt in res.json()}
        # Dutch defaults: statutory + extra vacation track a balance, sick doesn't.
        assert {"vacation_statutory", "vacation_extra", "sick", "special", "unpaid"} <= keys
        by_key = {lt["key"]: lt for lt in res.json()}
        assert by_key["vacation_statutory"]["tracks_balance"] is True
        assert by_key["vacation_statutory"]["carry_over_months"] == 6
        assert by_key["sick"]["requires_approval"] is False
        assert by_key["sick"]["tracks_balance"] is False


async def test_request_approval_flow_and_balance(client_for) -> None:
    t = await make_tenant("leave-flow")
    owner_headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _invite_member(c, owner_headers, "employee@example.com")
        member_headers = await auth_cookie(member)

        types = (await c.get("/api/v1/leave/types", headers=owner_headers)).json()
        statutory = next(lt for lt in types if lt["key"] == "vacation_statutory")

        # Part-time contract: 32 h/week → generated statutory entitlement = 4 × 32 = 128 h.
        res = await c.put(
            f"/api/v1/leave/profiles/{member.id}",
            json={"hours_per_week": 32},
            headers=owner_headers,
        )
        assert res.status_code == 200
        res = await c.post(
            "/api/v1/leave/entitlements/generate", json={"year": _YEAR}, headers=owner_headers
        )
        assert res.status_code == 200
        assert res.json()["created"] > 0

        balance = (
            await c.get(
                "/api/v1/leave/balance", params={"year": _YEAR}, headers=member_headers
            )
        ).json()
        statutory_balance = next(
            b for b in balance if b["leave_type_id"] == statutory["id"]
        )
        assert float(statutory_balance["entitled_hours"]) == 128.0

        # Member requests two days (16 h) → pending, reflected in the balance.
        start, end = _span(0, 2)
        res = await c.post(
            "/api/v1/leave/requests",
            json={
                "leave_type_id": statutory["id"],
                "start_date": start,
                "end_date": end,
            },
            headers=member_headers,
        )
        assert res.status_code == 201
        request = res.json()
        assert request["status"] == "pending"

        # A member may not decide — approving needs owner/admin.
        res = await c.post(
            f"/api/v1/leave/requests/{request['id']}/decide",
            json={"approved": True},
            headers=member_headers,
        )
        assert res.status_code == 403

        res = await c.post(
            f"/api/v1/leave/requests/{request['id']}/decide",
            json={"approved": True},
            headers=owner_headers,
        )
        assert res.status_code == 200
        assert res.json()["status"] == "approved"
        assert res.json()["decided_by_user_id"] == str(t.user.id)

        balance = (
            await c.get(
                "/api/v1/leave/balance", params={"year": _YEAR}, headers=member_headers
            )
        ).json()
        statutory_balance = next(b for b in balance if b["leave_type_id"] == statutory["id"])
        assert float(statutory_balance["approved_hours"]) == 16.0
        assert float(statutory_balance["remaining_hours"]) == 112.0

        # The team feed exposes the approved absence with the member's name.
        res = await c.get(
            "/api/v1/leave/team",
            params={"date_from": start, "date_to": end},
            headers=member_headers,
        )
        assert res.status_code == 200
        assert [item["user_name"] for item in res.json()] == ["Member"]

        # The member may retract their own *future* approved leave (#72); it frees the balance.
        res = await c.post(
            f"/api/v1/leave/requests/{request['id']}/cancel", headers=member_headers
        )
        assert res.status_code == 200
        assert res.json()["status"] == "cancelled"
        balance = (
            await c.get(
                "/api/v1/leave/balance", params={"year": _YEAR}, headers=member_headers
            )
        ).json()
        statutory_balance = next(b for b in balance if b["leave_type_id"] == statutory["id"])
        assert float(statutory_balance["approved_hours"]) == 0.0
        assert float(statutory_balance["remaining_hours"]) == 128.0


async def test_overlap_and_balance_guards(client_for) -> None:
    t = await make_tenant("leave-guards")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        types = (await c.get("/api/v1/leave/types", headers=headers)).json()
        statutory = next(lt for lt in types if lt["key"] == "vacation_statutory")

        # No entitlement yet → over-request on a balance-tracked type is blocked.
        start, end = _span(1)
        res = await c.post(
            "/api/v1/leave/requests",
            json={
                "leave_type_id": statutory["id"],
                "start_date": start,
                "end_date": end,
            },
            headers=headers,
        )
        assert res.status_code == 400
        assert res.json()["error"]["message"] == "errors.leave_insufficient_balance"

        granted = await c.put(
            "/api/v1/leave/entitlements",
            json={
                "user_id": str(t.user.id),
                "leave_type_id": statutory["id"],
                "year": _YEAR,
                "hours": 40,
            },
            headers=headers,
        )
        assert granted.status_code == 200, granted.text
        res = await c.post(
            "/api/v1/leave/requests",
            json={
                "leave_type_id": statutory["id"],
                "start_date": start,
                "end_date": end,
            },
            headers=headers,
        )
        assert res.status_code == 201

        # Overlapping the same day is rejected.
        res = await c.post(
            "/api/v1/leave/requests",
            json={
                "leave_type_id": statutory["id"],
                "start_date": start,
                "end_date": end,
            },
            headers=headers,
        )
        assert res.status_code == 409
        assert res.json()["error"]["message"] == "errors.leave_overlap"


async def test_sick_leave_is_registered_not_requested(client_for) -> None:
    t = await make_tenant("leave-sick")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        types = (await c.get("/api/v1/leave/types", headers=headers)).json()
        sick = next(lt for lt in types if lt["key"] == "sick")
        start, end = _span(2)
        res = await c.post(
            "/api/v1/leave/requests",
            json={"leave_type_id": sick["id"], "start_date": start, "end_date": end},
            headers=headers,
        )
        assert res.status_code == 201
        # No approval step, no balance deduction.
        assert res.json()["status"] == "approved"
        balance = (
            await c.get("/api/v1/leave/balance", params={"year": _YEAR}, headers=headers)
        ).json()
        assert all(lt["leave_type_id"] != sick["id"] for lt in balance)


async def test_member_scoping(client_for) -> None:
    t = await make_tenant("leave-scope")
    owner_headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _invite_member(c, owner_headers, "scoped@example.com")
        member_headers = await auth_cookie(member)
        types = (await c.get("/api/v1/leave/types", headers=owner_headers)).json()
        unpaid = next(lt for lt in types if lt["key"] == "unpaid")

        start, end = _span(3)
        owner_req = (
            await c.post(
                "/api/v1/leave/requests",
                json={
                    "leave_type_id": unpaid["id"],
                    "start_date": start,
                    "end_date": end,
                },
                headers=owner_headers,
            )
        ).json()

        # A member sees neither another user's request nor the org-wide list.
        res = await c.get(f"/api/v1/leave/requests/{owner_req['id']}", headers=member_headers)
        assert res.status_code == 404
        res = await c.get(
            "/api/v1/leave/requests", params={"all_users": True}, headers=member_headers
        )
        assert res.status_code == 403
        # Managers do see the org-wide list.
        res = await c.get(
            "/api/v1/leave/requests", params={"all_users": True}, headers=owner_headers
        )
        assert res.status_code == 200
        assert res.json()["total"] == 1


async def test_leave_tenant_isolation(client_for) -> None:
    a = await make_tenant("leave-org-a")
    b = await make_tenant("leave-org-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)

    async with client_for(a.host) as ca:
        types = (await ca.get("/api/v1/leave/types", headers=a_headers)).json()
        unpaid = next(lt for lt in types if lt["key"] == "unpaid")
        start, end = _span(4)
        created = (
            await ca.post(
                "/api/v1/leave/requests",
                json={
                    "leave_type_id": unpaid["id"],
                    "start_date": start,
                    "end_date": end,
                },
                headers=a_headers,
            )
        ).json()

    async with client_for(b.host) as cb:
        # B's session on B's host can never reach A's rows.
        res = await cb.get(f"/api/v1/leave/requests/{created['id']}", headers=b_headers)
        assert res.status_code == 404
        res = await cb.get(
            "/api/v1/leave/team",
            params={"date_from": start, "date_to": end},
            headers=b_headers,
        )
        assert res.json() == []
        # A's session on B's host is not a member there.
        res = await cb.get("/api/v1/leave/requests", headers=a_headers)
        assert res.status_code == 403
