"""members (team/user management) API coverage: list, invite, role changes, guards, isolation."""

from __future__ import annotations

from tests.conftest import auth_cookie, make_tenant


async def _role_key_by_id(client, headers) -> dict[str, str]:
    """id → key for the org's roles, so tests can assert which system role a member holds."""
    roles = (await client.get("/api/v1/roles", headers=headers)).json()
    return {r["id"]: r["key"] for r in roles}


async def _held_keys(client, headers, membership_id: str) -> set[str]:
    by_id = await _role_key_by_id(client, headers)
    members = (await client.get("/api/v1/members", headers=headers)).json()
    target = next(m for m in members if m["membership_id"] == membership_id)
    return {by_id[rid] for rid in target["role_ids"]}


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
        assert body["is_active"] is True
        assert "admin" in await _held_keys(c, headers, body["membership_id"])

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
        assert "admin" in await _held_keys(c, headers, owner["membership_id"])

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
        assert "owner" in await _held_keys(c, headers, other["membership_id"])


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
        assert "owner" in await _held_keys(c, headers, still_owner["membership_id"])
        assert "member" in await _held_keys(c, headers, plain["membership_id"])


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
        # avatar_url joined the safe minimal shape in #122 (effective avatar for pickers).
        assert set(rows[0].keys()) == {"user_id", "full_name", "email", "avatar_url"}


async def test_lookup_is_staff_only(client_for) -> None:
    """#221: a `client`-role membership (a portal user) is not a colleague — it never surfaces
    in the pickers built on /members/lookup; `include_clients=true` is the explicit opt-in."""
    t = await make_tenant("mem-lookup-staff")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        await c.post(
            "/api/v1/members/invite",
            json={"email": "portal@example.com", "role": "client"},
            headers=headers,
        )
        lookup = (await c.get("/api/v1/members/lookup", headers=headers)).json()
        emails = {m["email"] for m in lookup}
        assert "portal@example.com" not in emails
        assert t.user.email in emails

        both = await c.get(
            "/api/v1/members/lookup", params={"include_clients": "true"}, headers=headers
        )
        assert "portal@example.com" in {m["email"] for m in both.json()}


async def test_invite_reports_missing_email_transport(client_for) -> None:
    """#161: the invite stands, but a missing org transport is said out loud — the settings
    hint used to point at a mail that could never be sent."""
    t = await make_tenant("mem-invite-nomail")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        invited = await c.post(
            "/api/v1/members/invite",
            json={"email": "geen.mail@example.com", "role": "member"},
            headers=headers,
        )
        assert invited.status_code == 201, invited.text
        body = invited.json()
        assert body["invite_email_sent"] is False
        assert body["invite_email_error"] == "errors.email_not_configured"


async def test_invite_sends_welcome_mail_with_set_password_link(client_for, monkeypatch) -> None:
    """#161: with a transport configured, the invite mail rides the reset-token flow and
    carries a working /reset-password link on the org's own address."""
    from app.core.crypto import encrypt
    from app.core.email.models import EmailSettings
    from app.db import async_session_maker, set_current_org

    t = await make_tenant("mem-invite-mail")
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        session.add(
            EmailSettings(
                org_id=t.org.id,
                provider="smtp",
                config_enc=encrypt('{"host": "mail.example", "port": 25}'),
                from_email="noreply@agency.example",
                from_name="Agency",
            )
        )
        await session.commit()

    sent = []

    async def _capture(session, org_id, message):  # noqa: ANN001, ARG001
        sent.append(message)
        return True, None

    monkeypatch.setattr("app.core.auth.emails.send_org_email", _capture)

    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        invited = await c.post(
            "/api/v1/members/invite",
            json={"email": "welkom@example.com", "full_name": "Wel Kom", "role": "member"},
            headers=headers,
        )
        assert invited.status_code == 201, invited.text
        assert invited.json()["invite_email_sent"] is True

    assert len(sent) == 1
    assert sent[0].to == "welkom@example.com"
    assert "/reset-password?token=" in sent[0].text


async def test_password_policy_applies_everywhere(client_for) -> None:
    """#161: FastAPI Users' default accepted any string; the manager now enforces one
    policy on register, reset and self-update — the update path proves it."""
    t = await make_tenant("mem-password-policy")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        rejected = await c.patch(
            "/api/v1/users/me", json={"password": "kort"}, headers=headers
        )
        assert rejected.status_code == 400, rejected.text
        # FastAPI Users' reason travels through the app's own envelope (errors.py).
        assert rejected.json()["error"]["message"] == "errors.password_too_short"

        accepted = await c.patch(
            "/api/v1/users/me", json={"password": "lang-genoeg-wachtwoord"}, headers=headers
        )
        assert accepted.status_code == 200, accepted.text
