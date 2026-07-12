"""OIDC gate consistency — per-org, DB-backed (issue #6, rebuilt for #76).

The invariant survives the move from env config to per-org rows: the login page renders its
SSO button from ``/meta/modules``' ``oidc_enabled``, and that flag must be true **iff** the
flow would work for the resolved org. The routes are now mounted unconditionally, so "off"
means the route answers ``404 errors.sso_not_configured`` — the same status an unmounted
route used to give — instead of not existing.

The IdP round-trip itself cannot run here (no real provider), so the runtime routes are
exercised with a stub Authlib client injected at the one seam ``oidc.py`` uses
(``sso.oauth_client``); everything around it — hostname → org → stored config → provisioning
policy — is the real code path.
"""

from __future__ import annotations

import pytest
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.core.auth import sso
from app.main import app
from tests.conftest import auth_cookie, make_tenant

_CONFIG = {
    "enabled": True,
    "enforced": False,
    "name": "Acme ID",
    "discovery_url": "https://idp.example.com/.well-known/openid-configuration",
    "client_id": "client-id",
    "client_secret": "client-secret",
    "default_role": "member",
    "auto_provision": True,
}


def test_oidc_routes_are_always_mounted() -> None:
    """No mount-time branch left (issue #76): the routes exist whatever any org stores."""
    assert app.url_path_for("oidc_login") == "/api/v1/auth/oidc/login"
    assert app.url_path_for("oidc_callback") == "/api/v1/auth/oidc/callback"


async def _configure(client, headers, overrides: dict | None = None) -> dict:
    saved = await client.put(
        "/api/v1/settings/sso", json={**_CONFIG, **(overrides or {})}, headers=headers
    )
    assert saved.status_code == 200, saved.text
    return saved.json()


async def test_sso_button_iff_the_flow_would_answer(client_for) -> None:
    """Per-org now: org A configures SSO, org B on the same instance keeps none of it."""
    a = await make_tenant("oidc-gate-a")
    b = await make_tenant("oidc-gate-b")

    async with client_for(a.host) as client:
        # Unconfigured: no button, and the button's target answers 404 — not a broken flow.
        meta = await client.get("/api/v1/meta/modules")
        assert meta.json()["oidc_enabled"] is False
        refused = await client.get("/api/v1/auth/oidc/login")
        assert refused.status_code == 404
        assert refused.json()["error"]["code"] == "sso_not_configured"

        await _configure(client, await auth_cookie(a.user))
        meta = await client.get("/api/v1/meta/modules")
        assert meta.json()["oidc_enabled"] is True
        assert meta.json()["oidc_name"] == "Acme ID"

    async with client_for(b.host) as client:
        meta = await client.get("/api/v1/meta/modules")
        assert meta.json()["oidc_enabled"] is False
        assert meta.json()["oidc_name"] is None
        callback = await client.get("/api/v1/auth/oidc/callback")
        assert callback.status_code == 404
        assert callback.json()["error"]["code"] == "sso_not_configured"


async def test_half_configured_is_rejected_not_advertised(client_for) -> None:
    """The issue #6 misconfiguration, per org: enabled with a hole in the config is a 422 at
    write time now — it can no longer exist as stored state the button would lie about."""
    tenant = await make_tenant("oidc-gate-half")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        saved = await client.put(
            "/api/v1/settings/sso",
            json={**_CONFIG, "discovery_url": None},
            headers=headers,
        )
        assert saved.status_code == 422
        assert saved.json()["error"]["fields"]["discovery_url"] == "errors.required"


async def test_unknown_host_gets_a_clean_error(client_for) -> None:
    async with client_for("nobody.localhost") as client:
        refused = await client.get("/api/v1/auth/oidc/login")
        assert refused.status_code == 404
        assert refused.json()["error"]["code"] == "unknown_host"


class _StubClient:
    """Stands in for the Authlib client at ``sso.oauth_client`` — the IdP is out of reach."""

    def __init__(self, email: str = "jit@idp-example.com") -> None:
        self.email = email
        self.seen_redirect_uri: str | None = None

    async def authorize_redirect(self, request, redirect_uri):  # noqa: ANN001
        self.seen_redirect_uri = redirect_uri
        return RedirectResponse(url=f"https://idp.example.com/authorize?redirect_uri={redirect_uri}")

    async def authorize_access_token(self, request):  # noqa: ANN001
        return {"userinfo": {"email": self.email, "name": "JIT User"}}


async def test_login_redirects_with_the_request_derived_callback(
    client_for, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = await make_tenant("oidc-flow-login")
    headers = await auth_cookie(tenant.user)
    stub = _StubClient()
    monkeypatch.setattr(sso, "oauth_client", lambda row: stub)

    async with client_for(tenant.host) as client:
        await _configure(client, headers)
        response = await client.get("/api/v1/auth/oidc/login")
        assert response.status_code in (302, 307)
        assert "idp.example.com" in response.headers["location"]
        # The redirect_uri is request-derived (docs/SSO.md): the org's own hostname.
        assert stub.seen_redirect_uri == f"http://{tenant.host}/api/v1/auth/oidc/callback"


async def test_callback_provisions_from_the_orgs_stored_policy(
    client_for, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The callback resolves org + config per request: the JIT user lands in the resolved org
    with the *stored* default role, and the session cookie is issued."""
    tenant = await make_tenant("oidc-flow-cb")
    headers = await auth_cookie(tenant.user)
    monkeypatch.setattr(sso, "oauth_client", lambda row: _StubClient("new-hire@idp-example.com"))

    async with client_for(tenant.host) as client:
        await _configure(client, headers, {"default_role": "client"})
        response = await client.get("/api/v1/auth/oidc/callback")
        assert response.status_code in (302, 307)
        assert response.headers["location"] == "/"
        assert "schakl_auth=" in response.headers.get("set-cookie", "")

    from app.core.auth.models import User
    from app.core.models import Membership
    from app.core.permissions.models import MembershipRole, Role
    from app.db import async_session_maker, set_current_org

    async with async_session_maker() as session:
        user = await session.scalar(
            select(User).where(User.email == "new-hire@idp-example.com")
        )
        assert user is not None and user.is_verified
        await set_current_org(session, tenant.org.id)
        membership = await session.scalar(
            select(Membership).where(
                Membership.org_id == tenant.org.id, Membership.user_id == user.id
            )
        )
        assert membership is not None
        role = await session.scalar(
            select(Role)
            .join(MembershipRole, MembershipRole.role_id == Role.id)
            .where(MembershipRole.membership_id == membership.id)
        )
        assert role is not None and role.key == "client"


async def test_callback_without_auto_provision_creates_no_membership(
    client_for, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = await make_tenant("oidc-flow-noprov")
    headers = await auth_cookie(tenant.user)
    monkeypatch.setattr(sso, "oauth_client", lambda row: _StubClient("guest@idp-example.com"))

    async with client_for(tenant.host) as client:
        await _configure(client, headers, {"auto_provision": False})
        response = await client.get("/api/v1/auth/oidc/callback")
        assert response.status_code in (302, 307)

    from app.core.auth.models import User
    from app.core.models import Membership
    from app.db import async_session_maker, set_current_org

    async with async_session_maker() as session:
        user = await session.scalar(select(User).where(User.email == "guest@idp-example.com"))
        assert user is not None  # identity exists, access does not
        await set_current_org(session, tenant.org.id)
        membership = await session.scalar(
            select(Membership).where(Membership.user_id == user.id)
        )
        assert membership is None
