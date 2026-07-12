"""Entitlements appear without the hidden manual step (#105, #108).

Adding an employment contract seeds that user's missing default entitlements in the same
transaction (#105), and the first touch of an ungenerated current/next-year pot seeds it on
demand (#108) — so "book next January in July" works without an admin remembering "Genereer".
Both ride the same core as the bulk button: missing rows only, never a recalculation.
"""

from __future__ import annotations

import uuid
from datetime import date

from app.core.auth.models import User
from tests.conftest import auth_cookie, make_tenant

_YEAR = date.today().year


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


async def _types(client, headers) -> dict[str, str]:
    rows = (await client.get("/api/v1/leave/types", headers=headers)).json()
    return {t["key"]: t["id"] for t in rows}


async def _entitlements(client, headers, user_id, year=_YEAR) -> dict[str, float]:
    rows = (
        await client.get(
            "/api/v1/leave/entitlements",
            params={"year": year, "user_id": str(user_id)},
            headers=headers,
        )
    ).json()
    return {e["leave_type_id"]: float(e["hours"]) for e in rows}


async def _add_contract(client, headers, user_id, **overrides) -> dict:
    body = {
        "user_id": str(user_id),
        "start_date": f"{_YEAR}-01-01",
        "end_date": f"{_YEAR}-12-31",
        "contract_hours_per_week": "38",
        **overrides,
    }
    res = await client.post("/api/v1/leave/contracts", json=body, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


async def test_contract_add_seeds_that_users_entitlements(client_for) -> None:
    """No separate "Genereer": the contract and its entitlements arrive together (#105)."""
    t = await make_tenant("autogen-basic")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "e@example.com")
        bystander = await _member(c, headers, "b@example.com")
        types = await _types(c, headers)

        await _add_contract(c, headers, member.id)

        ent = await _entitlements(c, headers, member.id)
        # 4 weeks × 38 h contract = 152 statutory; 40 scheduled − 38 contract → 104 h ADV.
        assert ent[types["vacation_statutory"]] == 152.0
        assert ent[types["roostervrij"]] == 104.0
        # That user only — a contract for one person is not a bulk generate.
        assert await _entitlements(c, headers, bystander.id) == {}


async def test_contract_seeding_is_idempotent_and_non_destructive(client_for) -> None:
    t = await make_tenant("autogen-idempotent")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "e@example.com")
        types = await _types(c, headers)

        first = await _add_contract(c, headers, member.id, end_date=f"{_YEAR}-05-31")
        # The admin corrects the statutory grant by hand.
        res = await c.put(
            "/api/v1/leave/entitlements",
            json={
                "user_id": str(member.id),
                "leave_type_id": types["vacation_statutory"],
                "year": _YEAR,
                "hours": 99,
            },
            headers=headers,
        )
        assert res.status_code == 200

        # A follow-up contract (new period, more hours) creates nothing new for existing rows
        # and never overwrites the manual adjustment.
        await _add_contract(
            c, headers, member.id, start_date=f"{_YEAR}-06-01", end_date=None,
            contract_hours_per_week="40",
        )
        ent = await _entitlements(c, headers, member.id)
        assert ent[types["vacation_statutory"]] == 99.0

        # Correcting a contract (terminate) doesn't recalculate anything either.
        res = await c.patch(
            f"/api/v1/leave/contracts/{first['id']}",
            json={"end_date": f"{_YEAR}-04-30"},
            headers=headers,
        )
        assert res.status_code == 200, res.text
        assert (await _entitlements(c, headers, member.id))[
            types["vacation_statutory"]
        ] == 99.0


async def test_next_year_only_contract_does_not_seed_the_current_year(client_for) -> None:
    """A hire whose contract starts next January must not be granted this year's pot."""
    t = await make_tenant("autogen-nextyear")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "e@example.com")
        await _add_contract(
            c,
            headers,
            member.id,
            start_date=f"{_YEAR + 1}-01-01",
            end_date=None,
        )
        assert await _entitlements(c, headers, member.id, year=_YEAR) == {}


async def test_contract_add_also_seeds_an_already_generated_future_year(client_for) -> None:
    """December hire: next year already exists for the rest of the staff, so the new contract
    fills it for the new person too (#105's year policy)."""
    t = await make_tenant("autogen-future")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        types = await _types(c, headers)

        # Next year is already generated org-wide (for the owner, here) — then the hire happens.
        res = await c.post(
            "/api/v1/leave/entitlements/generate", json={"year": _YEAR + 1}, headers=headers
        )
        assert res.status_code == 200
        member = await _member(c, headers, "e@example.com")

        await _add_contract(c, headers, member.id, end_date=None)
        this_year = await _entitlements(c, headers, member.id, year=_YEAR)
        next_year = await _entitlements(c, headers, member.id, year=_YEAR + 1)
        assert this_year[types["vacation_statutory"]] == 152.0
        assert next_year[types["vacation_statutory"]] == 152.0


async def test_profile_manager_without_entitlement_write_still_seeds(client_for) -> None:
    """The generation is a side effect of a write the caller was allowed to make (#105) — it
    must not be gated on the caller also holding ``leave.entitlement.write``."""
    t = await make_tenant("autogen-perm")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        manager = await _member(c, headers, "hr@example.com")
        employee = await _member(c, headers, "e@example.com")
        types = await _types(c, headers)

        role = await c.post(
            "/api/v1/roles",
            json={"key": "hr", "permissions": ["leave.profile.manage"]},
            headers=headers,
        )
        assert role.status_code == 201, role.text
        members = (await c.get("/api/v1/members", headers=headers)).json()
        manager_row = next(m for m in members if m["user_id"] == str(manager.id))
        member_role = next(
            r
            for r in (await c.get("/api/v1/roles", headers=headers)).json()
            if r["key"] == "member"
        )
        res = await c.put(
            f"/api/v1/members/{manager_row['membership_id']}/roles",
            json={"role_ids": [member_role["id"], role.json()["id"]]},
            headers=headers,
        )
        assert res.status_code == 200, res.text

        mh = await auth_cookie(manager)
        # The HR manager may not write entitlements directly...
        res = await c.put(
            "/api/v1/leave/entitlements",
            json={
                "user_id": str(employee.id),
                "leave_type_id": types["vacation_statutory"],
                "year": _YEAR,
                "hours": 1,
            },
            headers=mh,
        )
        assert res.status_code == 403
        # ...but adding a contract still seeds them, as the contract's consequence.
        await _add_contract(c, mh, employee.id)
        ent = await _entitlements(c, headers, employee.id)
        assert ent[types["vacation_statutory"]] == 152.0


def _next_year_workday() -> date:
    """First Monday of November next year — a holiday-free, full-hours weekday (#48)."""
    from datetime import timedelta

    first = date(_YEAR + 1, 11, 1)
    return first + timedelta(days=(7 - first.weekday()) % 7)


async def test_member_can_request_leave_for_next_year(client_for) -> None:
    """#108: the next-year pot seeds on first touch, so a member can plan ahead."""
    t = await make_tenant("autogen-book-ahead")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "e@example.com")
        mh = await auth_cookie(member)
        types = await _types(c, headers)
        await _add_contract(c, headers, member.id, end_date=None)

        # Next year's balance exists the moment it is looked at...
        balance = (
            await c.get("/api/v1/leave/balance", params={"year": _YEAR + 1}, headers=mh)
        ).json()
        statutory = next(b for b in balance if b["leave_type_id"] == types["vacation_statutory"])
        assert float(statutory["entitled_hours"]) == 152.0

        # ...and a next-year request goes through instead of dying on an empty pot.
        day = _next_year_workday().isoformat()
        res = await c.post(
            "/api/v1/leave/requests",
            json={
                "leave_type_id": types["vacation_statutory"],
                "start_date": day,
                "end_date": day,
            },
            headers=mh,
        )
        assert res.status_code == 201, res.text
        assert res.json()["status"] == "pending"


async def test_balance_read_never_backfills_history(client_for) -> None:
    """On-demand seeding is bounded to the current and the next year — never the past."""
    t = await make_tenant("autogen-no-backfill")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "e@example.com")
        mh = await auth_cookie(member)
        await _add_contract(c, headers, member.id, start_date=f"{_YEAR - 1}-01-01", end_date=None)

        for year in (_YEAR - 1, _YEAR + 2):
            res = await c.get("/api/v1/leave/balance", params={"year": year}, headers=mh)
            assert res.status_code == 200
            assert await _entitlements(c, headers, member.id, year=year) == {}


async def test_contract_seeding_is_tenant_isolated(client_for) -> None:
    a = await make_tenant("autogen-org-a")
    b = await make_tenant("autogen-org-b")
    ah = await auth_cookie(a.user)
    bh = await auth_cookie(b.user)
    async with client_for(a.host) as ca:
        member = await _member(ca, ah, "shared@example.com")
        await _add_contract(ca, ah, member.id)
    async with client_for(b.host) as cb:
        rows = (
            await cb.get(
                "/api/v1/leave/entitlements", params={"year": _YEAR}, headers=bh
            )
        ).json()
        assert rows == []
