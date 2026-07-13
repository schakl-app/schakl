"""Sidebar nav preferences (#169): own row → org default → none, DashboardPref's rules."""

from __future__ import annotations

import uuid

from app.core.auth.models import User
from tests.conftest import auth_cookie, make_tenant


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


async def test_nav_prefs_resolution_and_default(client_for) -> None:
    t = await make_tenant("nav-prefs")
    owner_headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, owner_headers, "lid@nav.example")
        member_headers = await auth_cookie(member)

        # Nothing saved anywhere: declared positions rule.
        res = await c.get("/api/v1/nav/prefs", headers=member_headers)
        assert res.json() == {"items": None, "source": "none"}

        # Admins set the org default; a member may not.
        default_items = [{"key": "tasks", "hidden": False}, {"key": "time", "hidden": True}]
        assert (
            await c.put(
                "/api/v1/nav/prefs/default", json={"items": default_items},
                headers=member_headers,
            )
        ).status_code == 403
        assert (
            await c.put(
                "/api/v1/nav/prefs/default", json={"items": default_items},
                headers=owner_headers,
            )
        ).status_code == 200

        # A member without their own row inherits the default...
        res = (await c.get("/api/v1/nav/prefs", headers=member_headers)).json()
        assert res["source"] == "default"
        assert [i["key"] for i in res["items"]] == ["tasks", "time"]

        # ...their own row wins over it...
        own = [{"key": "time", "hidden": False}]
        assert (
            await c.put("/api/v1/nav/prefs", json={"items": own}, headers=member_headers)
        ).status_code == 200
        res = (await c.get("/api/v1/nav/prefs", headers=member_headers)).json()
        assert res["source"] == "user" and res["items"] == [{"key": "time", "hidden": False}]

        # ...and resetting falls back to the org default again.
        assert (await c.delete("/api/v1/nav/prefs", headers=member_headers)).status_code == 204
        assert (await c.get("/api/v1/nav/prefs", headers=member_headers)).json()[
            "source"
        ] == "default"


async def test_nav_default_is_tenant_isolated(client_for) -> None:
    a = await make_tenant("nav-iso-a")
    b = await make_tenant("nav-iso-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)
    async with client_for(a.host) as ca:
        assert (
            await ca.put(
                "/api/v1/nav/prefs/default",
                json={"items": [{"key": "tasks", "hidden": True}]},
                headers=a_headers,
            )
        ).status_code == 200
    async with client_for(b.host) as cb:
        assert (await cb.get("/api/v1/nav/prefs", headers=b_headers)).json() == {
            "items": None,
            "source": "none",
        }
