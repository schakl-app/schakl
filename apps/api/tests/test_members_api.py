"""members (team/user management) API coverage: list, invite, role changes, guards, isolation."""

from __future__ import annotations

import uuid

from app.core.auth.models import User
from app.db import async_session_maker
from tests.conftest import _password_hash, auth_cookie, make_tenant


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
        # avatar_url joined the safe minimal shape in #122 (effective avatar for pickers);
        # is_active is what lets a picker retire a deactivated colleague without losing them.
        assert set(rows[0].keys()) == {
            "user_id",
            "full_name",
            "email",
            "avatar_url",
            "is_active",
        }


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

    async def _capture(session, org_id, message, **kwargs):  # noqa: ANN001, ARG001
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
        rejected = await c.patch("/api/v1/users/me", json={"password": "kort"}, headers=headers)
        assert rejected.status_code == 400, rejected.text
        # FastAPI Users' reason travels through the app's own envelope (errors.py).
        assert rejected.json()["error"]["message"] == "errors.password_too_short"

        accepted = await c.patch(
            "/api/v1/users/me", json={"password": "lang-genoeg-wachtwoord"}, headers=headers
        )
        assert accepted.status_code == 200, accepted.text


async def test_lookup_flags_a_deactivated_account_and_still_returns_it(client_for) -> None:
    """A deactivated colleague is answered, carrying ``is_active=False``.

    Both halves matter and they pull in opposite directions, which is why neither can be left
    to a caller's memory. Dropping the row would blank the assignee on every task the person
    was holding when they left, and make "show me what she was working on" unaskable. Handing
    it back unmarked is how the pickers got here: an account that cannot sign in sat between
    two colleagues, spelled identically, as an ordinary suggestion. The flag is what lets the
    web put them behind the search instead of choosing one of the two mistakes.
    """
    t = await make_tenant("mem-lookup-inactive")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        invited = await c.post(
            "/api/v1/members/invite",
            json={"email": "vertrokken@example.com", "full_name": "Vertrokken", "role": "member"},
            headers=headers,
        )
        assert invited.status_code == 201, invited.text
        left = uuid.UUID(invited.json()["user_id"])

        async with async_session_maker() as session:
            user = await session.get(User, left)
            assert user is not None
            user.is_active = False
            await session.commit()

        rows = (await c.get("/api/v1/members/lookup", headers=headers)).json()
        by_email = {m["email"]: m for m in rows}
        assert "vertrokken@example.com" in by_email, "a deactivated colleague must stay nameable"
        assert by_email["vertrokken@example.com"]["is_active"] is False
        assert by_email[t.user.email]["is_active"] is True


async def _invite(c, headers, email: str, *, role: str = "member", name: str | None = None) -> dict:
    r = await c.post(
        "/api/v1/members/invite",
        json={"email": email, "full_name": name, "role": role},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_deactivating_keeps_the_member_and_everything_they_are_named_on(client_for) -> None:
    """The control the product was missing, and the whole reason it had to exist.

    Off-boarding offered only "Toegang intrekken", which deletes the membership. Nothing in the
    database is lost by that — every historical row keys on ``users.id`` — but every screen that
    names a person resolves the name *through* a membership, so revoking a departing colleague
    silently blanked the author of every hour, task and contactmoment they had ever written.
    Deactivating is the other answer: the row stays, the name stays, only the access ends.
    """
    t = await make_tenant("mem-deactivate")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        left = await _invite(c, headers, "weg@example.com", name="Weg Gegaan")

        off = await c.patch(
            f"/api/v1/members/{left['membership_id']}/account",
            json={"active": False},
            headers=headers,
        )
        assert off.status_code == 200, off.text
        assert off.json()["is_active"] is False
        assert off.json()["deactivated_at"] is not None

        # Still on the roster, still nameable in every picker — the two things revoking took away.
        members = {m["email"]: m for m in (await c.get("/api/v1/members", headers=headers)).json()}
        assert members["weg@example.com"]["is_active"] is False
        rows = (await c.get("/api/v1/members/lookup", headers=headers)).json()
        lookup = {m["email"]: m for m in rows}
        assert lookup["weg@example.com"]["full_name"] == "Weg Gegaan"
        assert lookup["weg@example.com"]["is_active"] is False

        # And back, in one press. `deactivated_at` clears with it, or the roster keeps a date
        # for something that is no longer true.
        on = await c.patch(
            f"/api/v1/members/{left['membership_id']}/account",
            json={"active": True},
            headers=headers,
        )
        assert on.status_code == 200, on.text
        assert on.json()["is_active"] is True
        assert on.json()["deactivated_at"] is None


async def test_a_deactivated_member_can_neither_sign_in_nor_use_the_session_they_had(
    client_for,
) -> None:
    """Both doors, because closing one leaves the other open in a way nobody would notice.

    ``member_of_request_org`` stops the next sign-in and answers exactly as it would for an
    address that was never here — a colleague who has left must not be able to confirm their
    account still exists by watching the error change. ``require_context`` stops the tab already
    open on their desk; without it the session in their browser keeps working until it expires.
    """
    t = await make_tenant("mem-deact-login")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        left = await _invite(c, headers, "sessie@example.com")

        # An invite mints an unusable random password; give them a real one to log in with.
        async with async_session_maker() as session:
            user = await session.get(User, uuid.UUID(left["user_id"]))
            assert user is not None
            user.hashed_password = _password_hash.hash("secret1234")
            await session.commit()

        theirs = await auth_cookie(await _reload(uuid.UUID(left["user_id"])))
        assert (await c.get("/api/v1/meta/me", headers=theirs)).status_code == 200
        ok = await c.post(
            "/api/v1/auth/login", data={"username": "sessie@example.com", "password": "secret1234"}
        )
        assert ok.status_code in (200, 204), ok.text

        off = await c.patch(
            f"/api/v1/members/{left['membership_id']}/account",
            json={"active": False},
            headers=headers,
        )
        assert off.status_code == 200, off.text

        # The session they were holding stops serving...
        assert (await c.get("/api/v1/meta/me", headers=theirs)).status_code == 403
        # ...and the login answers the way it answers for an address it has never heard of.
        refused = await c.post(
            "/api/v1/auth/login", data={"username": "sessie@example.com", "password": "secret1234"}
        )
        assert refused.status_code == 400


async def test_cannot_deactivate_self(client_for) -> None:
    """Same reason ``cannot_remove_self`` exists, minus the drama: it would work, and the
    admin's very next request would 403 with no way back in."""
    t = await make_tenant("mem-deact-self")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        me = next(
            m for m in (await c.get("/api/v1/members", headers=headers)).json() if m["is_self"]
        )
        r = await c.patch(
            f"/api/v1/members/{me['membership_id']}/account",
            json={"active": False},
            headers=headers,
        )
        assert r.status_code == 400
        assert r.json()["error"]["message"] == "errors.cannot_deactivate_self"


async def test_cannot_deactivate_the_last_role_manager(client_for) -> None:
    """An administrator who cannot sign in administers nothing.

    ``role_manager_count`` used to count ``membership_roles`` alone, so it would have waved this
    through and left an org whose only owner cannot log in — locked out exactly as thoroughly as
    revoking them, which the same guard has always refused.
    """
    t = await make_tenant("mem-deact-last")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        owner = await _invite(c, headers, "tweede-eigenaar@example.com", role="owner")

        # Two owners: deactivating one is fine.
        first = await c.patch(
            f"/api/v1/members/{owner['membership_id']}/account",
            json={"active": False},
            headers=headers,
        )
        assert first.status_code == 200, first.text

        # Now the seeded owner is the only one who can still administer roles, and they are the
        # caller — so the self guard catches it first. Prove the *count* half by handing the
        # remaining seat to the deactivated account and trying again.
        me = next(
            m for m in (await c.get("/api/v1/members", headers=headers)).json() if m["is_self"]
        )
        demoted = await c.patch(
            f"/api/v1/members/{me['membership_id']}",
            json={"role": "member"},
            headers=headers,
        )
        assert demoted.status_code == 409
        assert demoted.json()["error"]["message"] == "errors.last_role_manager"


async def test_rename_a_member_and_leave_their_status_alone(client_for) -> None:
    """Absent means leave alone (§18).

    The ⋯ Deactiveren item posts only ``active`` and the dialog posts only what it showed; a
    field the caller did not send must never be written, or a rename quietly reactivates someone.
    """
    t = await make_tenant("mem-rename")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        person = await _invite(c, headers, "naamloos@example.com")
        assert person["full_name"] is None

        await c.patch(
            f"/api/v1/members/{person['membership_id']}/account",
            json={"active": False},
            headers=headers,
        )
        named = await c.patch(
            f"/api/v1/members/{person['membership_id']}/account",
            json={"full_name": "  Pas Benoemd  "},
            headers=headers,
        )
        assert named.status_code == 200, named.text
        assert named.json()["full_name"] == "Pas Benoemd"
        assert named.json()["is_active"] is False, "a rename must not reactivate the account"

        # An emptied input posts a blank string, and that clears the name rather than storing "".
        cleared = await c.patch(
            f"/api/v1/members/{person['membership_id']}/account",
            json={"full_name": ""},
            headers=headers,
        )
        assert cleared.json()["full_name"] is None


async def test_account_edit_is_tenant_isolated(client_for) -> None:
    a = await make_tenant("mem-acct-iso-a")
    b = await make_tenant("mem-acct-iso-b")
    async with client_for(a.host) as ca, client_for(b.host) as cb:
        theirs = await _invite(cb, await auth_cookie(b.user), "hunlid@example.com")
        r = await ca.patch(
            f"/api/v1/members/{theirs['membership_id']}/account",
            json={"active": False},
            headers=await auth_cookie(a.user),
        )
        assert r.status_code == 404


async def _reload(user_id: uuid.UUID) -> User:
    async with async_session_maker() as session:
        user = await session.get(User, user_id)
        assert user is not None
        await session.refresh(user)
        session.expunge(user)
        return user


async def test_activating_lifts_a_hand_set_instance_flag_for_staff_but_not_for_a_client(
    client_for,
) -> None:
    """Activeren has to make "Actief" true, and for two accounts that means two different bits.

    Before this endpoint existed the only way to retire a colleague was ``users.is_active`` in a
    SQL prompt, so every instance carries a few accounts off through that column and none through
    the new one. Clearing only ours would print Actief over somebody who still cannot sign in.

    The exemption is the point of the test, not a footnote: the client portal uses that *same*
    column as its "login enabled" flag, so lifting it for a ``client`` membership would switch a
    client login the agency disabled back on — from a screen that does not even list them.
    """
    t = await make_tenant("mem-legacy-flag")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        staff = await _invite(c, headers, "oud-teamlid@example.com")
        outsider = await _invite(c, headers, "klantlogin@example.com", role="client")

        async with async_session_maker() as session:
            for row in (staff, outsider):
                user = await session.get(User, uuid.UUID(row["user_id"]))
                assert user is not None
                user.is_active = False
            await session.commit()

        revived = await c.patch(
            f"/api/v1/members/{staff['membership_id']}/account",
            json={"active": True},
            headers=headers,
        )
        assert revived.status_code == 200, revived.text
        assert revived.json()["is_active"] is True

        still_off = await c.patch(
            f"/api/v1/members/{outsider['membership_id']}/account",
            json={"active": True},
            headers=headers,
        )
        assert still_off.status_code == 200, still_off.text
        assert still_off.json()["is_active"] is False, "the portal owns that column, not this one"

        async with async_session_maker() as session:
            assert (await session.get(User, uuid.UUID(staff["user_id"]))).is_active is True
            assert (await session.get(User, uuid.UUID(outsider["user_id"]))).is_active is False
