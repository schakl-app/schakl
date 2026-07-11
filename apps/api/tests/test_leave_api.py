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

        # Approved leave is locked for the member; a pending one could still be cancelled.
        res = await c.post(
            f"/api/v1/leave/requests/{request['id']}/cancel", headers=member_headers
        )
        assert res.status_code == 403
        assert res.json()["error"]["message"] == "errors.approved_locked"

        # The team feed exposes the approved absence with the member's name.
        res = await c.get(
            "/api/v1/leave/team",
            params={"date_from": start, "date_to": end},
            headers=member_headers,
        )
        assert res.status_code == 200
        assert [item["user_name"] for item in res.json()] == ["Member"]


async def test_over_request_warns_but_submits_and_overlap_stays_hard(client_for) -> None:
    """#109: more hours than the pot holds still submits — the balance reads negative and the
    manager decides. Overlap keeps its hard 409."""
    t = await make_tenant("leave-guards")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        types = (await c.get("/api/v1/leave/types", headers=headers)).json()
        statutory = next(lt for lt in types if lt["key"] == "vacation_statutory")

        # A deliberately too-small pot: one 8 h day against 4 granted hours.
        start, end = _span(1)
        granted = await c.put(
            "/api/v1/leave/entitlements",
            json={
                "user_id": str(t.user.id),
                "leave_type_id": statutory["id"],
                "year": _YEAR,
                "hours": 4,
            },
            headers=headers,
        )
        assert granted.status_code == 200, granted.text

        # The preview tells the form what it is about to exceed…
        res = await c.post(
            "/api/v1/leave/requests/preview",
            json={"leave_type_id": statutory["id"], "start_date": start, "end_date": end},
            headers=headers,
        )
        assert res.status_code == 200
        assert float(res.json()["hours"]) == 8.0
        assert float(res.json()["remaining_hours"]) == 4.0

        # …and the request still goes through, leaving the balance negative.
        res = await c.post(
            "/api/v1/leave/requests",
            json={
                "leave_type_id": statutory["id"],
                "start_date": start,
                "end_date": end,
            },
            headers=headers,
        )
        assert res.status_code == 201, res.text
        assert res.json()["status"] == "pending"

        balance = (
            await c.get("/api/v1/leave/balance", params={"year": _YEAR}, headers=headers)
        ).json()
        row = next(b for b in balance if b["leave_type_id"] == statutory["id"])
        assert float(row["remaining_hours"]) == -4.0

        # Overlapping the same day is still rejected.
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


async def test_preview_hands_back_the_edited_requests_own_hours(client_for) -> None:
    """Editing must not warn against a balance the request itself is occupying (#109)."""
    t = await make_tenant("leave-preview-edit")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        types = (await c.get("/api/v1/leave/types", headers=headers)).json()
        statutory = next(lt for lt in types if lt["key"] == "vacation_statutory")
        await c.put(
            "/api/v1/leave/entitlements",
            json={
                "user_id": str(t.user.id),
                "leave_type_id": statutory["id"],
                "year": _YEAR,
                "hours": 8,
            },
            headers=headers,
        )
        start, end = _span(1)
        created = await c.post(
            "/api/v1/leave/requests",
            json={"leave_type_id": statutory["id"], "start_date": start, "end_date": end},
            headers=headers,
        )
        assert created.status_code == 201

        # Same-length move: without the request_id the remaining would read 0 and warn.
        new_start, new_end = _span(2)
        res = await c.post(
            "/api/v1/leave/requests/preview",
            json={
                "leave_type_id": statutory["id"],
                "start_date": new_start,
                "end_date": new_end,
                "request_id": created.json()["id"],
            },
            headers=headers,
        )
        assert res.status_code == 200
        assert float(res.json()["remaining_hours"]) == 8.0


async def test_edit_can_set_a_part_day_window(client_for) -> None:
    """PATCHing times must reach the TIME columns as time objects — the Clock serializer
    stringifies in model_dump(), which both asyncpg and the hour computation refuse."""
    t = await make_tenant("leave-partday-edit")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        types = (await c.get("/api/v1/leave/types", headers=headers)).json()
        sick = next(lt for lt in types if lt["key"] == "sick")
        start, end = _span(1)
        created = await c.post(
            "/api/v1/leave/requests",
            json={"leave_type_id": sick["id"], "start_date": start, "end_date": end},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        assert float(created.json()["hours"]) == 8.0

        edited = await c.patch(
            f"/api/v1/leave/requests/{created.json()['id']}",
            json={"start_time": "15:00"},
            headers=headers,
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["start_time"] == "15:00"
        # 15:00 to the 17:00 day end — the window is priced, not the whole day.
        assert float(edited.json()["hours"]) == 2.0


async def test_team_feed_resolves_omitted_time_bounds(client_for) -> None:
    """A NULL bound means the scheduled day's own start/end (#48); the display feed says so
    out loud (#107): "until 14:00" reads 08:30–14:00 on the calendar, never a dangling dash."""
    t = await make_tenant("leave-team-window")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        types = (await c.get("/api/v1/leave/types", headers=headers)).json()
        sick = next(lt for lt in types if lt["key"] == "sick")

        until = _span(1)[0]
        frm = _span(2)[0]
        whole = _span(3)[0]
        for body in (
            {"start_date": until, "end_date": until, "end_time": "14:00"},
            {"start_date": frm, "end_date": frm, "start_time": "15:00"},
            {"start_date": whole, "end_date": whole},
        ):
            res = await c.post(
                "/api/v1/leave/requests",
                json={"leave_type_id": sick["id"], **body},
                headers=headers,
            )
            assert res.status_code == 201, res.text

        team = (
            await c.get(
                "/api/v1/leave/team",
                params={"date_from": until, "date_to": whole},
                headers=headers,
            )
        ).json()
        by_date = {item["start_date"]: item for item in team}
        # Default schedule runs 08:30–17:00: each omitted bound resolves to the day's own.
        assert by_date[until]["resolved_start_time"] == "08:30"
        assert by_date[until]["resolved_end_time"] == "14:00"
        assert by_date[frm]["resolved_start_time"] == "15:00"
        assert by_date[frm]["resolved_end_time"] == "17:00"
        # A whole-day absence resolves to nothing — 08:30–17:00 on every chip would be noise.
        assert by_date[whole]["resolved_start_time"] is None
        assert by_date[whole]["resolved_end_time"] is None


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
