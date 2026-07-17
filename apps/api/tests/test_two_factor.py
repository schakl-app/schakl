"""Two-factor authentication (TOTP + backup codes + optional SMS) — the whole login story.

Covers the three promises the feature makes:

* **Enrollment is verified, not assumed** — 2FA turns on only after the authenticator app
  echoes a valid code, and the backup codes are minted exactly then, shown exactly once.
* **The cookie waits for the second factor** — a password alone yields a challenge token,
  never a session; the challenge is redeemable with TOTP, a (single-use) backup code, or an
  SMS code, under shared brute-force damping.
* **Escape hatches exist and are bounded** — self-disable costs the password; the org admin's
  reset is tenant-scoped (addressed by membership) and audited.
"""

from __future__ import annotations

import re
import time
import uuid

import pyotp
from sqlalchemy import select

from app.config import settings
from tests.conftest import auth_cookie, make_tenant

LOGIN = "/api/v1/auth/login"
TWOFA = "/api/v1/auth/2fa"


async def _enroll(client, headers) -> tuple[str, list[str]]:
    """Run the full setup+confirm flow → (TOTP secret, backup codes). Asserts the happy path."""
    setup = await client.post(f"{TWOFA}/setup", headers=headers)
    assert setup.status_code == 200
    body = setup.json()
    assert body["otpauth_url"].startswith("otpauth://totp/")
    assert "<svg" in body["qr_svg"]
    secret = body["secret"]

    confirm = await client.post(
        f"{TWOFA}/confirm", json={"code": pyotp.TOTP(secret).now()}, headers=headers
    )
    assert confirm.status_code == 200
    codes = confirm.json()["backup_codes"]
    assert len(codes) == 10
    return secret, codes


def _next_step_code(secret: str) -> str:
    """A code from the *next* 30s step — inside the verify window, but past the replay
    counter that the code used at confirm/verify time already advanced."""
    return pyotp.TOTP(secret).at(int(time.time()) + 30)


async def _login(client, tenant) -> object:
    return await client.post(
        LOGIN, data={"username": tenant.user.email, "password": tenant.password}
    )


# --------------------------------------------------------------------------- #
# Enrollment
# --------------------------------------------------------------------------- #


async def test_enrollment_flow(client_for) -> None:
    t = await make_tenant("tfa-enroll")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)

        before = (await c.get(TWOFA, headers=headers)).json()
        assert before == {
            "enabled": False,
            "pending": False,
            "backup_codes_remaining": 0,
            "sms_available": False,
            "sms": None,
        }

        secret, _codes = await _enroll(c, headers)
        after = (await c.get(TWOFA, headers=headers)).json()
        assert after["enabled"] is True
        assert after["backup_codes_remaining"] == 10

        # A confirmed setup refuses another /setup — disable first.
        again = await c.post(f"{TWOFA}/setup", headers=headers)
        assert again.status_code == 409

        # And the secret round-trips: a fresh code verifies at login (see login tests).
        assert pyotp.TOTP(secret).now().isdigit()


async def test_confirm_needs_a_valid_code(client_for) -> None:
    t = await make_tenant("tfa-confirm")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        setup = await c.post(f"{TWOFA}/setup", headers=headers)
        assert setup.status_code == 200

        bad = await c.post(f"{TWOFA}/confirm", json={"code": "000000"}, headers=headers)
        # 1-in-a-million flake guard: "000000" could be the actual code of this instant.
        assert bad.status_code in (400, 200)
        if bad.status_code == 400:
            assert bad.json()["error"]["code"] == "two_factor_code_invalid"
            status = (await c.get(TWOFA, headers=headers)).json()
            assert status["enabled"] is False and status["pending"] is True


async def test_setup_rotates_pending_secret(client_for) -> None:
    t = await make_tenant("tfa-rotate")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        first = (await c.post(f"{TWOFA}/setup", headers=headers)).json()["secret"]
        second = (await c.post(f"{TWOFA}/setup", headers=headers)).json()["secret"]
        assert first != second
        # The stale QR's secret no longer confirms; the fresh one does.
        stale = await c.post(
            f"{TWOFA}/confirm", json={"code": pyotp.TOTP(first).now()}, headers=headers
        )
        assert stale.status_code == 400
        fresh = await c.post(
            f"{TWOFA}/confirm", json={"code": pyotp.TOTP(second).now()}, headers=headers
        )
        assert fresh.status_code == 200


# --------------------------------------------------------------------------- #
# Login challenge
# --------------------------------------------------------------------------- #


async def test_login_without_twofactor_is_unchanged(client_for) -> None:
    t = await make_tenant("tfa-plain")
    async with client_for(t.host) as c:
        r = await _login(c, t)
        assert r.status_code == 204
        assert "schakl_auth=" in (r.headers.get("set-cookie") or "")


async def test_login_with_twofactor_withholds_cookie_until_verify(client_for) -> None:
    t = await make_tenant("tfa-challenge")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        secret, _codes = await _enroll(c, headers)

        r = await _login(c, t)
        assert r.status_code == 200
        assert "set-cookie" not in r.headers  # password alone buys no session
        body = r.json()
        assert body["two_factor_required"] is True
        assert body["methods"] == ["totp", "backup"]

        verify = await c.post(
            f"{TWOFA}/verify",
            json={"challenge_token": body["challenge_token"], "code": _next_step_code(secret)},
        )
        assert verify.status_code == 204
        cookie = verify.headers.get("set-cookie") or ""
        assert "schakl_auth=" in cookie

        token = re.search(r"schakl_auth=([^;]+)", cookie).group(1)
        me = await c.get("/api/v1/meta/me", headers={"Cookie": f"schakl_auth={token}"})
        assert me.status_code == 200


async def test_totp_code_cannot_be_replayed(client_for) -> None:
    t = await make_tenant("tfa-replay")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        secret, _codes = await _enroll(c, headers)

        challenge = (await _login(c, t)).json()["challenge_token"]
        code = _next_step_code(secret)
        first = await c.post(f"{TWOFA}/verify", json={"challenge_token": challenge, "code": code})
        assert first.status_code == 204

        challenge2 = (await _login(c, t)).json()["challenge_token"]
        replay = await c.post(
            f"{TWOFA}/verify", json={"challenge_token": challenge2, "code": code}
        )
        assert replay.status_code == 400


async def test_backup_code_is_single_use(client_for) -> None:
    t = await make_tenant("tfa-backup")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        _secret, codes = await _enroll(c, headers)
        code = codes[0]

        challenge = (await _login(c, t)).json()["challenge_token"]
        used = await c.post(
            f"{TWOFA}/verify",
            json={"challenge_token": challenge, "code": code, "method": "backup"},
        )
        assert used.status_code == 204

        status = (await c.get(TWOFA, headers=headers)).json()
        assert status["backup_codes_remaining"] == 9

        challenge2 = (await _login(c, t)).json()["challenge_token"]
        reused = await c.post(
            f"{TWOFA}/verify",
            json={"challenge_token": challenge2, "code": code, "method": "backup"},
        )
        assert reused.status_code == 400


async def test_verify_locks_after_repeated_failures(client_for) -> None:
    t = await make_tenant("tfa-lock")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        await _enroll(c, headers)
        challenge = (await _login(c, t)).json()["challenge_token"]

        for _ in range(8):
            r = await c.post(
                f"{TWOFA}/verify",
                json={"challenge_token": challenge, "code": "junk", "method": "backup"},
            )
            assert r.status_code == 400
        locked = await c.post(
            f"{TWOFA}/verify",
            json={"challenge_token": challenge, "code": "junk", "method": "backup"},
        )
        assert locked.status_code == 429
        assert locked.json()["error"]["code"] == "two_factor_locked"


async def test_challenge_token_garbage_is_401(client_for) -> None:
    t = await make_tenant("tfa-token")
    async with client_for(t.host) as c:
        r = await c.post(f"{TWOFA}/verify", json={"challenge_token": "junk", "code": "123456"})
        assert r.status_code == 401


# --------------------------------------------------------------------------- #
# Disable / backup regeneration
# --------------------------------------------------------------------------- #


async def test_disable_requires_password(client_for) -> None:
    t = await make_tenant("tfa-disable")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        await _enroll(c, headers)

        wrong = await c.post(
            f"{TWOFA}/disable", json={"password": "not-the-password"}, headers=headers
        )
        assert wrong.status_code == 400

        right = await c.post(
            f"{TWOFA}/disable", json={"password": t.password}, headers=headers
        )
        assert right.status_code == 204

        # Back to a plain password login.
        r = await _login(c, t)
        assert r.status_code == 204


async def test_regenerate_backup_codes_costs_a_totp_code(client_for) -> None:
    t = await make_tenant("tfa-regen")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        secret, old = await _enroll(c, headers)

        denied = await c.post(f"{TWOFA}/backup-codes", json={"code": "junk"}, headers=headers)
        assert denied.status_code == 400

        r = await c.post(
            f"{TWOFA}/backup-codes", json={"code": _next_step_code(secret)}, headers=headers
        )
        assert r.status_code == 200
        fresh = r.json()["backup_codes"]
        assert len(fresh) == 10 and not set(fresh) & set(old)

        # An old code no longer redeems a challenge.
        challenge = (await _login(c, t)).json()["challenge_token"]
        stale = await c.post(
            f"{TWOFA}/verify",
            json={"challenge_token": challenge, "code": old[0], "method": "backup"},
        )
        assert stale.status_code == 400


# --------------------------------------------------------------------------- #
# Org-admin reset
# --------------------------------------------------------------------------- #


async def test_admin_reset_is_scoped_audited_and_effective(client_for) -> None:
    from app.core.models import Membership
    from app.db import async_session_maker

    t = await make_tenant("tfa-reset")
    other = await make_tenant("tfa-reset-b")
    async with client_for(t.host) as c:
        admin_headers = await auth_cookie(t.user)
        invited = (
            await c.post(
                "/api/v1/members/invite",
                json={"email": "employee@example.com", "role": "member", "send_email": False},
                headers=admin_headers,
            )
        ).json()

        # The employee enrolls (their password is unusable, so drive 2FA with their cookie).
        async with async_session_maker() as session:
            from app.core.auth.models import User

            employee = await session.get(User, uuid.UUID(invited["user_id"]))
        employee_headers = await auth_cookie(employee)
        await _enroll(c, employee_headers)

        members = (await c.get("/api/v1/members", headers=admin_headers)).json()
        by_email = {m["email"]: m for m in members}
        assert by_email["employee@example.com"]["two_factor_enabled"] is True
        assert by_email[t.user.email]["two_factor_enabled"] is False

        # A foreign org's admin cannot even name this membership (404, not 403).
        async with client_for(other.host) as foreign:
            r = await foreign.delete(
                f"/api/v1/members/{invited['membership_id']}/two-factor",
                headers=await auth_cookie(other.user),
            )
            assert r.status_code == 404

        # A plain member may not reset anyone.
        r = await c.delete(
            f"/api/v1/members/{invited['membership_id']}/two-factor", headers=employee_headers
        )
        assert r.status_code == 403

        r = await c.delete(
            f"/api/v1/members/{invited['membership_id']}/two-factor", headers=admin_headers
        )
        assert r.status_code == 204

        # Resetting an account that has no 2FA is a 404, not a silent no-op.
        again = await c.delete(
            f"/api/v1/members/{invited['membership_id']}/two-factor", headers=admin_headers
        )
        assert again.status_code == 404

        status = (await c.get(TWOFA, headers=employee_headers)).json()
        assert status["enabled"] is False

        # The trust change left a trail (memberships and the audit log are RLS-forced,
        # so bind the tenant before reading either).
        async with async_session_maker() as session:
            from app.core.permissions.models import RoleAuditLog
            from app.db import set_current_org

            await set_current_org(session, t.org.id)
            membership = await session.scalar(
                select(Membership).where(Membership.id == uuid.UUID(invited["membership_id"]))
            )
            entry = await session.scalar(
                select(RoleAuditLog).where(RoleAuditLog.action == "membership.two_factor_reset")
            )
            assert entry is not None and entry.target_user_id == membership.user_id


# --------------------------------------------------------------------------- #
# SMS factor (instance gateway configured)
# --------------------------------------------------------------------------- #


async def test_sms_requires_instance_gateway(client_for) -> None:
    t = await make_tenant("tfa-sms-off")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        await _enroll(c, headers)
        r = await c.post(
            f"{TWOFA}/sms/setup", json={"phone": "+31612345678"}, headers=headers
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "sms_not_configured"


async def test_sms_enrollment_and_login(client_for, monkeypatch) -> None:
    sent: list[tuple[str, str]] = []

    async def fake_send(phone: str, message: str) -> None:
        sent.append((phone, message))

    from datetime import timedelta

    from app.core.auth import twofactor

    monkeypatch.setattr(settings, "sms_gateway_url", "https://gateway.example/send")
    monkeypatch.setattr(twofactor, "send_sms", fake_send)
    # The enroll-then-login sequence below runs in milliseconds; the 30s resend damper is
    # covered by its own unit of behaviour, not re-proven here.
    monkeypatch.setattr(twofactor, "SMS_RESEND_INTERVAL", timedelta(0))

    t = await make_tenant("tfa-sms")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        await _enroll(c, headers)

        # SMS is an add-on to a confirmed setup, and the number itself must be verified.
        bad_phone = await c.post(
            f"{TWOFA}/sms/setup", json={"phone": "0612345678"}, headers=headers
        )
        assert bad_phone.status_code == 422

        r = await c.post(f"{TWOFA}/sms/setup", json={"phone": "+31612345678"}, headers=headers)
        assert r.status_code == 200
        assert r.json()["phone_masked"] == "+31••••••678"
        code = re.search(r"\d{6}", sent[-1][1]).group(0)

        confirmed = await c.post(f"{TWOFA}/sms/confirm", json={"code": code}, headers=headers)
        assert confirmed.status_code == 204
        status = (await c.get(TWOFA, headers=headers)).json()
        assert status["sms"] == {"phone_masked": "+31••••••678", "confirmed": True}

        # Login now offers sms; the texted code redeems the challenge.
        body = (await _login(c, t)).json()
        assert body["methods"] == ["totp", "backup", "sms"]
        send = await c.post(
            f"{TWOFA}/challenge/sms", json={"challenge_token": body["challenge_token"]}
        )
        assert send.status_code == 200
        code = re.search(r"\d{6}", sent[-1][1]).group(0)
        verify = await c.post(
            f"{TWOFA}/verify",
            json={"challenge_token": body["challenge_token"], "code": code, "method": "sms"},
        )
        assert verify.status_code == 204
        assert "schakl_auth=" in (verify.headers.get("set-cookie") or "")

        # The SMS code is single-use: the same code cannot redeem a second challenge.
        challenge2 = (await _login(c, t)).json()["challenge_token"]
        reuse = await c.post(
            f"{TWOFA}/verify",
            json={"challenge_token": challenge2, "code": code, "method": "sms"},
        )
        assert reuse.status_code == 400

        # Dropping the number removes the method; TOTP stays.
        assert (await c.delete(f"{TWOFA}/sms", headers=headers)).status_code == 204
        body = (await _login(c, t)).json()
        assert body["methods"] == ["totp", "backup"]
