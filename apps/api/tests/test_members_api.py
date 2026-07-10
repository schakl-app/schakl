"""members (team/user management) API coverage: list, invite, role changes, guards, isolation."""

from __future__ import annotations

from tests.conftest import auth_cookie, make_tenant


async def test_list_requires_manager(client_for) -> None:
    t = await make_tenant("mem-member", role="member")
    async with client_for(t.host) as c:
        r = await c.get("/api/v1/members", headers=await auth_cookie(t.user))
        assert r.status_code == 403


async def test_invite_creates_member(client_for) -> None:
    t = await make_tenant("mem-invite")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        invited = await c.post(
            "/api/v1/members/invite",
            json={"email": "New.Person@Example.com", "full_name": "New Person", "role": "admin"},
            headers=headers,
        )
        assert invited.status_code == 201
        body = invited.json()
        assert body["email"] == "new.person@example.com"  # normalised
        assert body["role"] == "admin"
        assert body["is_active"] is True

        members = await c.get("/api/v1/members", headers=headers)
        emails = {m["email"] for m in members.json()}
        assert "new.person@example.com" in emails

        # Inviting the same person again conflicts.
        again = await c.post(
            "/api/v1/members/invite",
            json={"email": "new.person@example.com", "role": "member"},
            headers=headers,
        )
        assert again.status_code == 409


async def test_change_role_and_last_role_manager_guard(client_for) -> None:
    """The guard counts *role managers*, not owners (issue #19).

    An org whose last owner becomes an admin has lost nothing — ``admin`` still holds
    ``settings.roles.manage``. An org whose last one becomes a ``member`` has locked itself out,
    and that is what 409s.
    """
    t = await make_tenant("mem-roles")  # seeded user is OWNER
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        members = (await c.get("/api/v1/members", headers=headers)).json()
        owner = next(m for m in members if m["is_self"])

        demote = await c.patch(
            f"/api/v1/members/{owner['membership_id']}",
            json={"role": "member"},
            headers=headers,
        )
        assert demote.status_code == 409
        assert demote.json()["error"]["message"] == "errors.last_role_manager"

        # Demoting to admin is fine: admin still administers roles.
        to_admin = await c.patch(
            f"/api/v1/members/{owner['membership_id']}",
            json={"role": "admin"},
            headers=headers,
        )
        assert to_admin.status_code == 200
        assert to_admin.json()["role"] == "admin"

        # Invite a second person and promote them to owner.
        other = (
            await c.post(
                "/api/v1/members/invite",
                json={"email": "second@example.com", "role": "admin"},
                headers=headers,
            )
        ).json()
        promote = await c.patch(
            f"/api/v1/members/{other['membership_id']}",
            json={"role": "owner"},
            headers=headers,
        )
        assert promote.status_code == 200
        assert promote.json()["role"] == "owner"


async def test_cannot_revoke_the_last_role_manager(client_for) -> None:
    t = await make_tenant("mem-last-mgr")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        # A second owner may revoke the first; the first may not then be left alone as a member.
        second = (
            await c.post(
                "/api/v1/members/invite",
                json={"email": "co-owner@example.com", "role": "owner"},
                headers=headers,
            )
        ).json()
        assert (
            await c.delete(f"/api/v1/members/{second['membership_id']}", headers=headers)
        ).status_code == 204

        plain = (
            await c.post(
                "/api/v1/members/invite",
                json={"email": "plain@example.com", "role": "member"},
                headers=headers,
            )
        ).json()
        # Demoting the sole remaining manager is refused, and the transaction rolls back.
        me = next(
            m for m in (await c.get("/api/v1/members", headers=headers)).json() if m["is_self"]
        )
        refused = await c.patch(
            f"/api/v1/members/{me['membership_id']}", json={"role": "member"}, headers=headers
        )
        assert refused.status_code == 409
        still_owner = next(
            m for m in (await c.get("/api/v1/members", headers=headers)).json() if m["is_self"]
        )
        assert still_owner["role"] == "owner"
        assert plain["role"] == "member"


async def test_cannot_revoke_self(client_for) -> None:
    t = await make_tenant("mem-self")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        me = next(
            m for m in (await c.get("/api/v1/members", headers=headers)).json() if m["is_self"]
        )
        r = await c.delete(f"/api/v1/members/{me['membership_id']}", headers=headers)
        assert r.status_code == 400
        assert r.json()["error"]["message"] == "errors.cannot_remove_self"


async def test_revoke_member(client_for) -> None:
    t = await make_tenant("mem-revoke")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        other = (
            await c.post(
                "/api/v1/members/invite",
                json={"email": "temp@example.com", "role": "member"},
                headers=headers,
            )
        ).json()
        gone = await c.delete(f"/api/v1/members/{other['membership_id']}", headers=headers)
        assert gone.status_code == 204
        emails = {m["email"] for m in (await c.get("/api/v1/members", headers=headers)).json()}
        assert "temp@example.com" not in emails


async def test_members_are_tenant_isolated(client_for) -> None:
    a = await make_tenant("mem-iso-a")
    b = await make_tenant("mem-iso-b")
    async with client_for(a.host) as ca, client_for(b.host) as cb:
        await ca.post(
            "/api/v1/members/invite",
            json={"email": "only-a@example.com", "role": "member"},
            headers=await auth_cookie(a.user),
        )
        b_list = await cb.get("/api/v1/members", headers=await auth_cookie(b.user))
        b_emails = {m["email"] for m in b_list.json()}
        assert "only-a@example.com" not in b_emails


async def test_lookup_open_to_plain_members(client_for) -> None:
    t = await make_tenant("mem-lookup", role="member")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        # Plain members may not list full memberships…
        assert (await c.get("/api/v1/members", headers=headers)).status_code == 403
        # …but can resolve names for assignee pickers.
        r = await c.get("/api/v1/members/lookup", headers=headers)
        assert r.status_code == 200
        rows = r.json()
        assert len(rows) == 1
        assert set(rows[0].keys()) == {"user_id", "full_name", "email"}
