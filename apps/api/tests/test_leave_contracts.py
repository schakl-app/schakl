"""Employment contracts + roostervrije tijd / ADV (#65).

Contract hours are the legal number, distinct from scheduled hours: statutory vacation keys off
the contract, and the gap between scheduled and contract hours accrues as ADV. Entitlement
prorates over the contract's overlap with the year; the past is locked to members.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from app.core.auth.models import User
from tests.conftest import auth_cookie, leave_workday, make_tenant

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


async def _entitled(client, headers, member_id, type_id) -> float:
    rows = (
        await client.get(
            "/api/v1/leave/entitlements",
            params={"year": _YEAR, "user_id": str(member_id)},
            headers=headers,
        )
    ).json()
    match = next((e for e in rows if e["leave_type_id"] == type_id), None)
    return float(match["hours"]) if match else 0.0


async def test_roostervrij_type_is_seeded(client_for) -> None:
    t = await make_tenant("contract-seed")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        types = await _types(c, headers)
        assert "roostervrij" in types
        row = next(
            r
            for r in (await c.get("/api/v1/leave/types", headers=headers)).json()
            if r["key"] == "roostervrij"
        )
        assert row["accrues_schedule_gap"] is True
        assert row["requires_approval"] is False  # a free day is moved, not requested
        assert row["tracks_balance"] is True


async def test_contract_crud_and_overlap_guard(client_for) -> None:
    t = await make_tenant("contract-crud")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "e@example.com")

        created = await c.post(
            "/api/v1/leave/contracts",
            json={
                "user_id": str(member.id),
                "start_date": f"{_YEAR}-01-01",
                "end_date": None,
                "contract_hours_per_week": "38",
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        assert float(created.json()["contract_hours_per_week"]) == 38.0
        # Scheduled hours are the default 40 h week — the two are finally distinct.
        assert float(created.json()["scheduled_hours_per_week"]) == 40.0

        # An overlapping period for the same employee is refused.
        clash = await c.post(
            "/api/v1/leave/contracts",
            json={
                "user_id": str(member.id),
                "start_date": f"{_YEAR}-06-01",
                "contract_hours_per_week": "40",
            },
            headers=headers,
        )
        assert clash.status_code == 409
        assert "leave_contract_overlap" in clash.text

        # Terminating the open contract frees the later period.
        cid = created.json()["id"]
        term = await c.patch(
            f"/api/v1/leave/contracts/{cid}",
            json={"end_date": f"{_YEAR}-05-31"},
            headers=headers,
        )
        assert term.status_code == 200
        ok = await c.post(
            "/api/v1/leave/contracts",
            json={
                "user_id": str(member.id),
                "start_date": f"{_YEAR}-06-01",
                "contract_hours_per_week": "40",
            },
            headers=headers,
        )
        assert ok.status_code == 201, ok.text

        listed = await c.get(
            "/api/v1/leave/contracts", params={"user_id": str(member.id)}, headers=headers
        )
        assert len(listed.json()) == 2


async def test_statutory_uses_contract_hours_not_scheduled(client_for) -> None:
    """A 38-hour contract worked as 40 scheduled hours: 4 × 38 = 152, not 4 × 40 = 160."""
    t = await make_tenant("contract-statutory")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "e@example.com")
        types = await _types(c, headers)
        await c.post(
            "/api/v1/leave/contracts",
            json={
                "user_id": str(member.id),
                "start_date": f"{_YEAR}-01-01",
                "end_date": f"{_YEAR}-12-31",
                "contract_hours_per_week": "38",
            },
            headers=headers,
        )
        gen = await c.post(
            "/api/v1/leave/entitlements/generate", json={"year": _YEAR}, headers=headers
        )
        assert gen.status_code == 200
        assert await _entitled(c, headers, member.id, types["vacation_statutory"]) == 152.0


async def test_roostervrij_accrues_from_the_schedule_gap(client_for) -> None:
    """40 scheduled − 38 contract = 2 h/week ≈ 104 h ≈ 13 days over a full year, rounded to a
    bookable half day."""
    t = await make_tenant("contract-adv")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "e@example.com")
        types = await _types(c, headers)
        await c.post(
            "/api/v1/leave/contracts",
            json={
                "user_id": str(member.id),
                "start_date": f"{_YEAR}-01-01",
                "end_date": f"{_YEAR}-12-31",
                "contract_hours_per_week": "38",
            },
            headers=headers,
        )
        await c.post(
            "/api/v1/leave/entitlements/generate", json={"year": _YEAR}, headers=headers
        )
        adv = await _entitled(c, headers, member.id, types["roostervrij"])
        # 2 h × (365/7) = 104.29 → nearest half day (4 h) = 104.0.
        assert adv == 104.0


async def test_no_contract_falls_back_to_scheduled_hours(client_for) -> None:
    """Upgrading moves nobody's balance: a contract-less employee still gets 4 × scheduled, and
    no ADV."""
    t = await make_tenant("contract-fallback")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "e@example.com")
        types = await _types(c, headers)
        await c.post(
            "/api/v1/leave/entitlements/generate", json={"year": _YEAR}, headers=headers
        )
        # Default 40 h scheduled week, no contract → statutory 4 × 40 = 160, ADV nothing.
        assert await _entitled(c, headers, member.id, types["vacation_statutory"]) == 160.0
        assert await _entitled(c, headers, member.id, types["roostervrij"]) == 0.0


async def test_member_cannot_backdate_leave_but_a_manager_can(client_for) -> None:
    t = await make_tenant("contract-pastlock")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "e@example.com")
        mh = await auth_cookie(member)
        types = await _types(c, headers)
        # A recent past weekday: exact day doesn't matter, only that it's before today.
        past = date.today() - timedelta(days=date.today().weekday() + 7)

        # The member is refused before hours are even computed.
        res = await c.post(
            "/api/v1/leave/requests",
            json={
                "leave_type_id": types["special"],
                "start_date": past.isoformat(),
                "end_date": past.isoformat(),
            },
            headers=mh,
        )
        assert res.status_code == 403
        assert "leave_past_locked" in res.text

        # A manager registering leave on their behalf may backdate (a retroactive ziekmelding).
        res = await c.post(
            "/api/v1/leave/requests",
            json={
                "leave_type_id": types["special"],
                "user_id": str(member.id),
                "start_date": past.isoformat(),
                "end_date": past.isoformat(),
                "hours_override": 8,
            },
            headers=headers,
        )
        assert res.status_code != 403, res.text


async def test_contracts_are_tenant_isolated(client_for) -> None:
    a = await make_tenant("contract-org-a")
    b = await make_tenant("contract-org-b")
    ah = await auth_cookie(a.user)
    bh = await auth_cookie(b.user)
    async with client_for(a.host) as ca:
        member = await _member(ca, ah, "shared@example.com")
        created = await ca.post(
            "/api/v1/leave/contracts",
            json={
                "user_id": str(member.id),
                "start_date": f"{_YEAR}-01-01",
                "contract_hours_per_week": "36",
            },
            headers=ah,
        )
        cid = created.json()["id"]
    async with client_for(b.host) as cb:
        # B cannot see or touch A's contract.
        res = await cb.patch(
            f"/api/v1/leave/contracts/{cid}",
            json={"contract_hours_per_week": "10"},
            headers=bh,
        )
        assert res.status_code == 404
        res = await cb.get(
            "/api/v1/leave/contracts", params={"user_id": str(member.id)}, headers=bh
        )
        # B's owner may manage, but the member isn't in B — 404 on the member lookup path or an
        # empty list; either way, none of A's data leaks.
        assert res.status_code in (200, 404)
        if res.status_code == 200:
            assert res.json() == []


def test_leave_workday_is_in_november() -> None:
    # Sanity: the shared helper anchors future leave in holiday-free November.
    assert leave_workday(0).month == 11
