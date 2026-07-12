"""Per-org SSO settings (issue #76): write-only secret, tested-before-enforce, break-glass.

No network is touched: the discovery fetch is patched at the router's seam, and the runtime
IdP flow is covered in ``test_auth_oidc_gate.py``.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text

from app.config import settings
from app.core.auth.sso import OrgAuthSettings
from app.core.crypto import decrypt
from app.db import async_session_maker, set_current_org
from tests.conftest import auth_cookie, make_tenant

_CONFIG = {
    "enabled": True,
    "enforced": False,
    "name": "Agency ID",
    "discovery_url": "https://idp.agency-example.nl/.well-known/openid-configuration",
    "client_id": "schakl-client",
    "client_secret": "very-secret-value-123",
    "default_role": "member",
    "auto_provision": True,
}

_DISCOVERY_DOC = {
    "issuer": "https://idp.agency-example.nl",
    "authorization_endpoint": "https://idp.agency-example.nl/authorize",
    "token_endpoint": "https://idp.agency-example.nl/token",
    "jwks_uri": "https://idp.agency-example.nl/jwks",
}


def _pass_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch(url: str) -> dict:
        return dict(_DISCOVERY_DOC)

    # Patch the router's own binding — that is the seam the test endpoint calls through.
    import app.core.auth.sso_router as sso_router

    monkeypatch.setattr(sso_router, "fetch_discovery", fake_fetch)


# --------------------------------------------------------------------------- #
# Secret handling
# --------------------------------------------------------------------------- #
async def test_secret_is_write_only_and_encrypted_at_rest(client_for) -> None:
    t = await make_tenant("sso-secret")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        saved = await c.put("/api/v1/settings/sso", json=_CONFIG, headers=headers)
        assert saved.status_code == 200, saved.text
        body = saved.json()
        assert body["secret_configured"] is True
        assert "client_secret" not in body  # write-only: the field does not exist on reads
        assert "very-secret-value-123" not in saved.text

        got = await c.get("/api/v1/settings/sso", headers=headers)
        assert got.status_code == 200
        assert got.json()["secret_configured"] is True
        assert "very-secret-value-123" not in got.text
        # The derived callback URL is displayed, never configured.
        assert got.json()["callback_url"] == f"https://{t.host}/api/v1/auth/oidc/callback"

    # Encrypted at rest with the shared crypto helper: the column holds a Fernet token that
    # round-trips, never the plaintext.
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        row = await session.scalar(select(OrgAuthSettings))
        assert row is not None
        assert "very-secret-value-123" not in (row.oidc_client_secret_encrypted or "")
        assert decrypt(row.oidc_client_secret_encrypted) == "very-secret-value-123"


async def test_empty_secret_on_update_keeps_the_stored_one(client_for) -> None:
    t = await make_tenant("sso-keep")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        assert (
            await c.put("/api/v1/settings/sso", json=_CONFIG, headers=headers)
        ).status_code == 200
        updated = await c.put(
            "/api/v1/settings/sso",
            json={**_CONFIG, "name": "Renamed", "client_secret": ""},
            headers=headers,
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["secret_configured"] is True
        assert updated.json()["name"] == "Renamed"

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        row = await session.scalar(select(OrgAuthSettings))
        assert decrypt(row.oidc_client_secret_encrypted) == "very-secret-value-123"


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
async def test_enabled_requires_a_complete_config(client_for) -> None:
    t = await make_tenant("sso-val")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        missing = await c.put(
            "/api/v1/settings/sso",
            json={**_CONFIG, "client_id": None, "client_secret": None},
            headers=headers,
        )
        assert missing.status_code == 422
        fields = missing.json()["error"]["fields"]
        assert fields["client_id"] == "errors.required"
        assert fields["client_secret"] == "errors.required"

        bad_url = await c.put(
            "/api/v1/settings/sso",
            json={**_CONFIG, "discovery_url": "not-a-url"},
            headers=headers,
        )
        assert bad_url.status_code == 422
        assert bad_url.json()["error"]["fields"]["discovery_url"] == "errors.invalid_url"

        unknown_role = await c.put(
            "/api/v1/settings/sso",
            json={**_CONFIG, "default_role": "nonexistent"},
            headers=headers,
        )
        assert unknown_role.status_code == 422

        # A disabled-but-partial draft is fine: nothing advertises or serves it.
        draft = await c.put(
            "/api/v1/settings/sso",
            json={**_CONFIG, "enabled": False, "client_id": None, "client_secret": None},
            headers=headers,
        )
        assert draft.status_code == 200, draft.text


# --------------------------------------------------------------------------- #
# Enforce needs a successful test — and never locks the org out
# --------------------------------------------------------------------------- #
async def test_enforce_requires_a_successful_test(
    client_for, monkeypatch: pytest.MonkeyPatch
) -> None:
    t = await make_tenant("sso-enforce")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        assert (
            await c.put("/api/v1/settings/sso", json=_CONFIG, headers=headers)
        ).status_code == 200

        # Untested config: enforcing would risk lockout, so the write is refused.
        refused = await c.put(
            "/api/v1/settings/sso", json={**_CONFIG, "enforced": True}, headers=headers
        )
        assert refused.status_code == 422
        assert refused.json()["error"]["message"] == "errors.sso_test_required"

        _pass_discovery(monkeypatch)
        tested = await c.post("/api/v1/settings/sso/test", headers=headers)
        assert tested.status_code == 200, tested.text
        assert tested.json()["ok"] is True
        assert tested.json()["issuer"] == "https://idp.agency-example.nl"

        enforced = await c.put(
            "/api/v1/settings/sso", json={**_CONFIG, "enforced": True}, headers=headers
        )
        assert enforced.status_code == 200, enforced.text
        assert enforced.json()["enforced"] is True
        assert enforced.json()["tested"] is True


async def test_changing_the_connection_clears_the_tested_marker(
    client_for, monkeypatch: pytest.MonkeyPatch
) -> None:
    t = await make_tenant("sso-retest")
    headers = await auth_cookie(t.user)
    _pass_discovery(monkeypatch)
    async with client_for(t.host) as c:
        assert (
            await c.put("/api/v1/settings/sso", json=_CONFIG, headers=headers)
        ).status_code == 200
        assert (await c.post("/api/v1/settings/sso/test", headers=headers)).json()["ok"] is True

        moved = await c.put(
            "/api/v1/settings/sso",
            json={**_CONFIG, "discovery_url": "https://other-idp.example.com/.well-known/openid-configuration"},
            headers=headers,
        )
        assert moved.status_code == 200
        assert moved.json()["tested"] is False  # the marker belonged to the old connection

        refused = await c.put(
            "/api/v1/settings/sso",
            json={
                **_CONFIG,
                "discovery_url": "https://other-idp.example.com/.well-known/openid-configuration",
                "enforced": True,
            },
            headers=headers,
        )
        assert refused.status_code == 422
        assert refused.json()["error"]["message"] == "errors.sso_test_required"


async def test_enforced_refuses_local_login_and_break_glass_overrides(
    client_for, monkeypatch: pytest.MonkeyPatch
) -> None:
    t = await make_tenant("sso-lockout")
    headers = await auth_cookie(t.user)
    _pass_discovery(monkeypatch)
    async with client_for(t.host) as c:
        assert (
            await c.put("/api/v1/settings/sso", json=_CONFIG, headers=headers)
        ).status_code == 200
        assert (await c.post("/api/v1/settings/sso/test", headers=headers)).json()["ok"] is True
        assert (
            await c.put(
                "/api/v1/settings/sso", json={**_CONFIG, "enforced": True}, headers=headers
            )
        ).status_code == 200

        credentials = {"username": t.user.email, "password": t.password}
        refused = await c.post("/api/v1/auth/login", data=credentials)
        assert refused.status_code == 403
        assert refused.json()["error"]["code"] == "local_login_disabled"

        # Registration is a password flow too; enforced turns it off with the same message.
        register = await c.post(
            "/api/v1/auth/register",
            json={"email": "x@sso-lockout-example.nl", "password": "secret1234"},
        )
        assert register.status_code == 403

        # Ending a session must always work, however it began.
        logout = await c.post("/api/v1/auth/logout", headers=headers)
        assert logout.status_code in (200, 204)

        # The login page mirrors the org's state...
        meta = await c.get("/api/v1/meta/modules")
        assert meta.json()["local_login_enabled"] is False
        assert meta.json()["oidc_enabled"] is True

        # ...and the operator break-glass re-opens local login without touching the DB.
        monkeypatch.setattr(settings, "force_local_login", True)
        allowed = await c.post("/api/v1/auth/login", data=credentials)
        assert allowed.status_code in (200, 204), allowed.text
        meta = await c.get("/api/v1/meta/modules")
        assert meta.json()["local_login_enabled"] is True


async def test_other_orgs_local_login_is_untouched(
    client_for, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Enforcement is per org: org A enforcing SSO must not refuse org B's password login."""
    a = await make_tenant("sso-enf-a")
    b = await make_tenant("sso-enf-b")
    _pass_discovery(monkeypatch)
    async with client_for(a.host) as c:
        headers = await auth_cookie(a.user)
        assert (
            await c.put("/api/v1/settings/sso", json=_CONFIG, headers=headers)
        ).status_code == 200
        assert (await c.post("/api/v1/settings/sso/test", headers=headers)).json()["ok"] is True
        assert (
            await c.put(
                "/api/v1/settings/sso", json={**_CONFIG, "enforced": True}, headers=headers
            )
        ).status_code == 200
    async with client_for(b.host) as c:
        allowed = await c.post(
            "/api/v1/auth/login", data={"username": b.user.email, "password": b.password}
        )
        assert allowed.status_code in (200, 204), allowed.text
        assert (await c.get("/api/v1/meta/modules")).json()["local_login_enabled"] is True


# --------------------------------------------------------------------------- #
# Tenant isolation & RBAC
# --------------------------------------------------------------------------- #
async def test_tenant_isolation(client_for) -> None:
    a = await make_tenant("sso-iso-a")
    b = await make_tenant("sso-iso-b")
    async with client_for(a.host) as c:
        headers = await auth_cookie(a.user)
        assert (
            await c.put("/api/v1/settings/sso", json=_CONFIG, headers=headers)
        ).status_code == 200
    async with client_for(b.host) as c:
        headers = await auth_cookie(b.user)
        got = await c.get("/api/v1/settings/sso", headers=headers)
        assert got.status_code == 200
        body = got.json()
        # Org B sees pristine defaults — nothing of org A's IdP leaks through.
        assert body["enabled"] is False
        assert body["secret_configured"] is False
        assert body["discovery_url"] is None
        # And org B writing its own row leaves org A's untouched.
        assert (
            await c.put(
                "/api/v1/settings/sso",
                json={**_CONFIG, "client_id": "b-client"},
                headers=headers,
            )
        ).status_code == 200
    async with client_for(a.host) as c:
        headers = await auth_cookie(a.user)
        assert (await c.get("/api/v1/settings/sso", headers=headers)).json()[
            "client_id"
        ] == "schakl-client"


async def test_member_cannot_manage_sso(client_for) -> None:
    from tests.test_notification_channels import _member

    t = await make_tenant("sso-rbac")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "m@sso-rbac-example.nl")
        member_headers = await auth_cookie(member)
        assert (
            await c.get("/api/v1/settings/sso", headers=member_headers)
        ).status_code == 403
        assert (
            await c.put("/api/v1/settings/sso", json=_CONFIG, headers=member_headers)
        ).status_code == 403
        assert (
            await c.post("/api/v1/settings/sso/test", headers=member_headers)
        ).status_code == 403


# --------------------------------------------------------------------------- #
# The startup reconciler reaches pre-#76 orgs
# --------------------------------------------------------------------------- #
async def test_reconciler_grants_settings_auth_manage_to_existing_orgs(client_for) -> None:
    """An org seeded before this shipped has an admin role that never heard of the key; the
    lifespan reconciler must grant it exactly once (CLAUDE.md §15)."""
    t = await make_tenant("sso-reconcile", role="admin")
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        # Rewind to the pre-#76 state: the key was never offered, admin never held it.
        await session.execute(
            text(
                "UPDATE org_settings SET applied_permission_defaults ="
                " array_remove(applied_permission_defaults, 'settings.auth.manage')"
                " WHERE org_id = :org"
            ),
            {"org": str(t.org.id)},
        )
        await session.execute(
            text(
                "DELETE FROM role_permissions"
                " WHERE org_id = :org AND permission = 'settings.auth.manage'"
            ),
            {"org": str(t.org.id)},
        )
        await session.commit()

    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        assert (await c.get("/api/v1/settings/sso", headers=headers)).status_code == 403

    from app.core.permissions.reconcile import reconcile_permission_defaults

    await reconcile_permission_defaults()

    async with client_for(t.host) as c:
        assert (await c.get("/api/v1/settings/sso", headers=headers)).status_code == 200
