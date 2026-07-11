"""Hourly rate per employee (#82): admin-only write, own/any read, tenant isolation.

The rate is salary-adjacent, so the access-control design *is* the feature: a member may read
their own rate but not anyone else's, and only an admin may set one.
"""

from __future__ import annotations

import uuid

from app.core.auth.models import User
from tests.conftest import auth_cookie, make_tenant


async def _invite_member(client, headers, email: str) -> User:
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


async def test_admin_sets_and_member_reads_own_rate(client_for) -> None:
    t = await make_tenant("rate-basic")
    owner_headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _invite_member(c, owner_headers, "employee@example.com")
        member_headers = await auth_cookie(member)

        # No rate recorded yet.
        res = await c.get("/api/v1/leave/rate", headers=member_headers)
        assert res.status_code == 200
        assert res.json()["hourly_rate"] is None

        # Admin sets the member's rate.
        res = await c.put(
            f"/api/v1/leave/rate/{member.id}",
            json={"hourly_rate": "85.50"},
            headers=owner_headers,
        )
        assert res.status_code == 200
        assert float(res.json()["hourly_rate"]) == 85.50

        # The member now reads their own rate.
        res = await c.get("/api/v1/leave/rate", headers=member_headers)
        assert float(res.json()["hourly_rate"]) == 85.50

        # Clearing it with null.
        res = await c.put(
            f"/api/v1/leave/rate/{member.id}",
            json={"hourly_rate": None},
            headers=owner_headers,
        )
        assert res.status_code == 200
        assert res.json()["hourly_rate"] is None


async def test_member_cannot_read_or_write_others_rate(client_for) -> None:
    t = await make_tenant("rate-rbac")
    owner_headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        alice = await _invite_member(c, owner_headers, "alice@example.com")
        bob = await _invite_member(c, owner_headers, "bob@example.com")
        alice_headers = await auth_cookie(alice)

        await c.put(
            f"/api/v1/leave/rate/{bob.id}",
            json={"hourly_rate": "120"},
            headers=owner_headers,
        )

        # A member may not read another employee's rate (404, not 403 — never confirm existence
        # of a salary), and may not list everyone's rates.
        res = await c.get(f"/api/v1/leave/rate/{bob.id}", headers=alice_headers)
        assert res.status_code == 403
        res = await c.get("/api/v1/leave/rates", headers=alice_headers)
        assert res.status_code == 403

        # A member may not set any rate — not even their own.
        res = await c.put(
            f"/api/v1/leave/rate/{alice.id}",
            json={"hourly_rate": "999"},
            headers=alice_headers,
        )
        assert res.status_code == 403


async def test_rate_is_tenant_isolated(client_for) -> None:
    a = await make_tenant("rate-org-a")
    b = await make_tenant("rate-org-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)
    async with client_for(a.host) as ca:
        member = await _invite_member(ca, a_headers, "shared@example.com")
        await ca.put(
            f"/api/v1/leave/rate/{member.id}",
            json={"hourly_rate": "77"},
            headers=a_headers,
        )
    # B's owner, on B's host, cannot reach A's employee's rate.
    async with client_for(b.host) as cb:
        res = await cb.get(f"/api/v1/leave/rate/{member.id}", headers=b_headers)
        assert res.status_code == 404
        res = await cb.get("/api/v1/leave/rates", headers=b_headers)
        assert res.status_code == 200
        assert res.json() == []
