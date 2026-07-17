"""Change-email (``POST /users/me/email``) — the sign-in address moves only with the password.

Also pins the invariant that motivated the dedicated route: the bare ``PATCH /users/me``
must *ignore* an ``email`` field, or a stolen session could redirect the account and reset
the password to it.
"""

from __future__ import annotations

import pyotp

from tests.conftest import auth_cookie, make_tenant

CHANGE = "/api/v1/users/me/email"
LOGIN = "/api/v1/auth/login"


async def test_change_email_costs_the_password_and_moves_the_login(client_for) -> None:
    t = await make_tenant("mail-change")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)

        wrong = await c.post(
            CHANGE, json={"email": "new@example.com", "password": "nope"}, headers=headers
        )
        assert wrong.status_code == 400
        assert wrong.json()["error"]["code"] == "password_incorrect"

        r = await c.post(
            CHANGE,
            json={"email": "New.Address@Example.com", "password": t.password},
            headers=headers,
        )
        assert r.status_code == 200
        assert r.json()["email"] == "new.address@example.com"  # normalised, like invites
        assert r.json()["is_verified"] is False  # the new address was never proven

        # The login moved with it: new address works, the old one is nobody.
        new_login = await c.post(
            LOGIN, data={"username": "new.address@example.com", "password": t.password}
        )
        assert new_login.status_code == 204
        old_login = await c.post(
            LOGIN, data={"username": t.user.email, "password": t.password}
        )
        assert old_login.status_code == 400


async def test_change_email_refuses_a_taken_address(client_for) -> None:
    t = await make_tenant("mail-taken")
    other = await make_tenant("mail-taken-b", email="claimed@example.com")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        r = await c.post(
            CHANGE,
            json={"email": "Claimed@Example.com", "password": t.password},
            headers=headers,
        )
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "email_taken"
        # Nothing moved for either identity.
        assert (
            await c.post(LOGIN, data={"username": other.user.email, "password": other.password})
        ).status_code == 204


async def test_change_to_own_address_is_a_noop(client_for) -> None:
    t = await make_tenant("mail-noop")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        r = await c.post(
            CHANGE, json={"email": t.user.email.upper(), "password": t.password}, headers=headers
        )
        assert r.status_code == 200
        assert r.json()["email"] == t.user.email


async def test_patch_users_me_ignores_email(client_for) -> None:
    """The unguarded path stays closed: PATCH /users/me accepts the field and discards it."""
    t = await make_tenant("mail-patch")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        r = await c.patch(
            "/api/v1/users/me", json={"email": "hijacked@example.com"}, headers=headers
        )
        assert r.status_code == 200
        assert r.json()["email"] == t.user.email
        assert (
            await c.post(LOGIN, data={"username": t.user.email, "password": t.password})
        ).status_code == 204


async def test_change_email_keeps_two_factor(client_for) -> None:
    """2FA hangs off the user id, not the address — a rename must not weaken it."""
    t = await make_tenant("mail-tfa")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        setup = (await c.post("/api/v1/auth/2fa/setup", headers=headers)).json()
        confirm = await c.post(
            "/api/v1/auth/2fa/confirm",
            json={"code": pyotp.TOTP(setup["secret"]).now()},
            headers=headers,
        )
        assert confirm.status_code == 200

        r = await c.post(
            CHANGE, json={"email": "renamed@example.com", "password": t.password}, headers=headers
        )
        assert r.status_code == 200

        challenge = await c.post(
            LOGIN, data={"username": "renamed@example.com", "password": t.password}
        )
        assert challenge.status_code == 200
        assert challenge.json()["two_factor_required"] is True
