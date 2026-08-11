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

import uuid

import pytest
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.core.auth import sso
from app.core.crypto import encrypt
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
    """Stands in for the Authlib client at ``sso.oauth_client`` — the IdP is out of reach.

    Models the id_token / userinfo-endpoint split the real bug turned on (#122): Authlib parses
    the validated id_token into ``token["userinfo"]``, while the profile ``picture`` an IdP like
    Google keeps only at the userinfo endpoint is what ``userinfo()`` returns. ``userinfo_raises``
    stands in for an IdP with no reachable endpoint, to prove enrichment is best-effort.
    """

    def __init__(
        self,
        email: str = "jit@idp-example.com",
        *,
        id_token_claims: dict | None = None,
        userinfo_claims: dict | None = None,
        userinfo_raises: bool = False,
    ) -> None:
        self.email = email
        self.seen_redirect_uri: str | None = None
        self._id_token_claims = id_token_claims
        self._userinfo_claims = userinfo_claims or {}
        self._userinfo_raises = userinfo_raises

    async def authorize_redirect(self, request, redirect_uri):  # noqa: ANN001
        self.seen_redirect_uri = redirect_uri
        return RedirectResponse(url=f"https://idp.example.com/authorize?redirect_uri={redirect_uri}")

    async def authorize_access_token(self, request):  # noqa: ANN001
        claims = self._id_token_claims
        if claims is None:
            claims = {"email": self.email, "name": "JIT User"}
        return {"userinfo": claims, "access_token": "stub-access-token"}

    async def userinfo(self, token=None):  # noqa: ANN001
        if self._userinfo_raises:
            raise RuntimeError("userinfo endpoint unreachable")
        return self._userinfo_claims


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


async def test_a_deep_link_survives_the_idp_round_trip(
    client_for, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An org that *enforces* SSO renders no password form, so this is the only door it has —
    and the callback used to land on ``/`` unconditionally, which meant every guarded link such
    an org's people followed dropped them on the dashboard. The target is parked in the session
    on the way out and read back on the way in."""
    tenant = await make_tenant("oidc-next")
    headers = await auth_cookie(tenant.user)
    monkeypatch.setattr(sso, "oauth_client", lambda row: _verified("deep@idp-example.com"))

    # One client, so the session cookie Starlette sets on /login is sent back on /callback —
    # which is the mechanism under test, not an incidental detail of the fixture.
    async with client_for(tenant.host) as client:
        await _configure(client, headers)
        started = await client.get(
            "/api/v1/auth/oidc/login", params={"next": "/companies/abc-123"}
        )
        assert started.status_code in (302, 307)
        landed = await client.get("/api/v1/auth/oidc/callback")
        assert landed.headers["location"] == "/companies/abc-123"
        assert "schakl_auth=" in landed.headers.get("set-cookie", "")


async def test_an_external_next_is_refused_and_the_login_still_works(
    client_for, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The open-redirect shape, and the reason it is worth guarding *here* of all places: the
    visitor is mid-sign-in, so a look-alike host asking for the password again has borrowed this
    app's credibility. Refused as a target, never as a login — a stale link is not their fault.

    ``/\\evil.example`` is the case an inline ``startsWith("//")`` check misses: a browser reads
    the backslash as a slash and the whole thing as another origin."""
    tenant = await make_tenant("oidc-next-evil")
    headers = await auth_cookie(tenant.user)
    monkeypatch.setattr(sso, "oauth_client", lambda row: _verified("wary@idp-example.com"))

    for hostile in ("//evil.example", "/\\evil.example", "https://evil.example"):
        async with client_for(tenant.host) as client:
            await _configure(client, headers)
            await client.get("/api/v1/auth/oidc/login", params={"next": hostile})
            landed = await client.get("/api/v1/auth/oidc/callback")
            assert landed.headers["location"] == "/", hostile
            assert "schakl_auth=" in landed.headers.get("set-cookie", "")


async def test_an_abandoned_target_does_not_haunt_the_next_sign_in(
    client_for, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The session is a place a value can *linger*. Start a deep-linked login, walk away, then
    sign in plainly from the same browser: the second attempt must land on the dashboard, not on
    a page the first attempt was headed for."""
    tenant = await make_tenant("oidc-next-stale")
    headers = await auth_cookie(tenant.user)
    monkeypatch.setattr(sso, "oauth_client", lambda row: _verified("twice@idp-example.com"))

    async with client_for(tenant.host) as client:
        await _configure(client, headers)
        await client.get("/api/v1/auth/oidc/login", params={"next": "/companies/abandoned"})
        # …no callback. They come back later and press the SSO button with nothing in mind.
        await client.get("/api/v1/auth/oidc/login")
        landed = await client.get("/api/v1/auth/oidc/callback")
        assert landed.headers["location"] == "/"


async def test_the_target_is_consumed_by_the_callback_that_reads_it(
    client_for, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Popped once, whatever the request answers: a target that outlives its own attempt is a
    redirect waiting to fire on some later, unrelated sign-in."""
    tenant = await make_tenant("oidc-next-once")
    headers = await auth_cookie(tenant.user)
    monkeypatch.setattr(sso, "oauth_client", lambda row: _verified("once@idp-example.com"))

    async with client_for(tenant.host) as client:
        await _configure(client, headers)
        await client.get("/api/v1/auth/oidc/login", params={"next": "/tasks?status=open"})
        first = await client.get("/api/v1/auth/oidc/callback")
        assert first.headers["location"] == "/tasks?status=open"
        second = await client.get("/api/v1/auth/oidc/callback")
        assert second.headers["location"] == "/"


async def test_a_refused_login_hands_the_target_back_to_the_login_screen(
    client_for, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The IdP authenticated someone this org has no membership for. They bounce to ``/login``
    with the reason — and with the target, so signing in with a local account that *does* have
    access still finishes the journey rather than restarting it."""
    tenant = await make_tenant("oidc-next-refused")
    headers = await auth_cookie(tenant.user)
    monkeypatch.setattr(sso, "oauth_client", lambda row: _StubClient("outsider@idp-example.com"))

    async with client_for(tenant.host) as client:
        await _configure(client, headers, {"auto_provision": False})
        await client.get("/api/v1/auth/oidc/login", params={"next": "/companies/abc-123"})
        landed = await client.get("/api/v1/auth/oidc/callback")
        assert landed.headers["location"] == (
            "/login?error=oidc_no_access&next=%2Fcompanies%2Fabc-123"
        )
        assert "schakl_auth=" not in landed.headers.get("set-cookie", "")


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


async def _avatar_of(email: str) -> str | None:
    from app.core.auth.models import User
    from app.db import async_session_maker

    async with async_session_maker() as session:
        user = await session.scalar(select(User).where(User.email == email))
        assert user is not None
        return user.oidc_avatar_url


async def test_callback_imports_avatar_from_the_userinfo_endpoint(
    client_for, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The #122 bug: the id_token carries no ``picture`` (Google's common shape), and the code
    read only the parsed id_token — so the avatar the userinfo endpoint held was never fetched.
    With the merge, the endpoint's ``picture`` lands on the user."""
    tenant = await make_tenant("oidc-avatar-endpoint")
    headers = await auth_cookie(tenant.user)
    picture = "https://lh3.googleusercontent.com/a/portrait"
    monkeypatch.setattr(
        sso,
        "oauth_client",
        lambda row: _StubClient(
            "shot@idp-example.com",
            id_token_claims={"email": "shot@idp-example.com", "name": "Shot"},
            userinfo_claims={"email": "shot@idp-example.com", "picture": picture},
        ),
    )
    async with client_for(tenant.host) as client:
        await _configure(client, headers)
        response = await client.get("/api/v1/auth/oidc/callback")
        assert response.status_code in (302, 307)

    assert await _avatar_of("shot@idp-example.com") == picture


async def test_id_token_picture_wins_over_the_endpoint(
    client_for, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When both carry a picture, the validated id_token is authoritative — the endpoint only
    fills gaps, it never overrides identity-bearing claims."""
    tenant = await make_tenant("oidc-avatar-idtoken")
    headers = await auth_cookie(tenant.user)
    monkeypatch.setattr(
        sso,
        "oauth_client",
        lambda row: _StubClient(
            "both@idp-example.com",
            id_token_claims={"email": "both@idp-example.com", "picture": "https://idp/id-token"},
            userinfo_claims={"email": "both@idp-example.com", "picture": "https://idp/endpoint"},
        ),
    )
    async with client_for(tenant.host) as client:
        await _configure(client, headers)
        assert (await client.get("/api/v1/auth/oidc/callback")).status_code in (302, 307)

    assert await _avatar_of("both@idp-example.com") == "https://idp/id-token"


async def test_userinfo_failure_does_not_break_login(
    client_for, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Enrichment is best-effort: an unreachable userinfo endpoint still logs the user in on the
    id_token's claims, just without the endpoint-only picture."""
    tenant = await make_tenant("oidc-avatar-failopen")
    headers = await auth_cookie(tenant.user)
    monkeypatch.setattr(
        sso,
        "oauth_client",
        lambda row: _StubClient(
            "resilient@idp-example.com",
            id_token_claims={"email": "resilient@idp-example.com", "name": "Resilient"},
            userinfo_raises=True,
        ),
    )
    async with client_for(tenant.host) as client:
        await _configure(client, headers)
        response = await client.get("/api/v1/auth/oidc/callback")
        assert response.status_code in (302, 307)
        assert "schakl_auth=" in response.headers.get("set-cookie", "")

    assert await _avatar_of("resilient@idp-example.com") is None


async def test_an_oversized_picture_url_does_not_break_login(
    client_for, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The v0.5.0 prod regression: the first real Google picture claim (the signed
    ``lh3.googleusercontent.com/a-/ALV-…`` form) ran past the old VARCHAR(1024) and the
    overflow failed the *login's own commit* with a 500. The column is TEXT now — the login
    succeeds and the URL is stored whole."""
    tenant = await make_tenant("oidc-avatar-oversize")
    headers = await auth_cookie(tenant.user)
    picture = "https://lh3.googleusercontent.com/a-/" + "x" * 1500
    monkeypatch.setattr(
        sso,
        "oauth_client",
        lambda row: _StubClient(
            "portrait@idp-example.com",
            id_token_claims={"email": "portrait@idp-example.com", "name": "Portrait"},
            userinfo_claims={"email": "portrait@idp-example.com", "picture": picture},
        ),
    )
    async with client_for(tenant.host) as client:
        await _configure(client, headers)
        response = await client.get("/api/v1/auth/oidc/callback")
        assert response.status_code in (302, 307)
        assert "schakl_auth=" in response.headers.get("set-cookie", "")

    assert await _avatar_of("portrait@idp-example.com") == picture


# --------------------------------------------------------------------------- #
# What the flow guarantees regardless of what the tenant configured
# --------------------------------------------------------------------------- #
def test_every_authorization_request_carries_pkce() -> None:
    """S256 is built into the client, not a setting: an authorization code observed in a proxy
    log or in browser history is useless without the verifier that never left the server."""
    row = sso.OrgAuthSettings(
        org_id=uuid.uuid4(),
        oidc_enabled=True,
        oidc_discovery_url="https://idp.example.com/.well-known/openid-configuration",
        oidc_client_id="client-id",
        oidc_client_secret_encrypted=encrypt("client-secret"),
    )
    client = sso.oauth_client(row)
    assert client.client_kwargs.get("code_challenge_method") == "S256"
    sso.invalidate_client(row.org_id)


def _verified(email: str) -> _StubClient:
    """A stub whose IdP vouches for the address — what a *returning* user needs, since adopting
    an existing local account on a bare email claim is the account-takeover guard's whole
    concern (audit C2)."""
    return _StubClient(
        email, id_token_claims={"email": email, "name": "JIT User", "email_verified": True}
    )


async def test_auto_provision_does_not_undo_an_admins_removal(
    client_for, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of first-contact-only: sign in, get provisioned, be removed, sign in
    again — and stay removed. "Has no membership" cannot be the test, because that is equally
    true of someone who was just taken out of the org."""
    tenant = await make_tenant("oidc-reprov")
    headers = await auth_cookie(tenant.user)
    monkeypatch.setattr(sso, "oauth_client", lambda row: _verified("leaver@idp-example.com"))

    from app.core.auth.models import User
    from app.core.models import Membership
    from app.db import async_session_maker, set_current_org

    async def _membership_id():
        async with async_session_maker() as session:
            user = await session.scalar(
                select(User).where(User.email == "leaver@idp-example.com")
            )
            assert user is not None
            await set_current_org(session, tenant.org.id)
            return await session.scalar(
                select(Membership.id).where(
                    Membership.org_id == tenant.org.id, Membership.user_id == user.id
                )
            )

    async with client_for(tenant.host) as client:
        await _configure(client, headers)

        first = await client.get("/api/v1/auth/oidc/callback")
        assert first.status_code in (302, 307)
        assert first.headers["location"] == "/"
        membership_id = await _membership_id()
        assert membership_id is not None

        # The admin takes their access away.
        async with async_session_maker() as session:
            await set_current_org(session, tenant.org.id)
            membership = await session.get(Membership, membership_id)
            await session.delete(membership)
            await session.commit()

        # They sign in again through the same IdP, with auto-provision still on.
        second = await client.get("/api/v1/auth/oidc/callback")
        assert second.status_code in (302, 307)
        # No membership handed back, and no session either: refused, and told why.
        assert second.headers["location"] == "/login?error=oidc_no_access"
        assert "schakl_auth=" not in second.headers.get("set-cookie", "")
        assert await _membership_id() is None


async def test_a_first_sign_in_at_a_second_org_still_provisions(
    client_for, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First contact is per **org**, not per account: the memory of one tenant's provisioning
    must not read as "already handled" at another."""
    one = await make_tenant("oidc-multi-a")
    two = await make_tenant("oidc-multi-b", email="multi-b@example.com")
    monkeypatch.setattr(sso, "oauth_client", lambda row: _verified("nomad@idp-example.com"))

    from app.core.auth.models import User
    from app.core.models import Membership
    from app.db import async_session_maker, set_current_org

    for tenant in (one, two):
        async with client_for(tenant.host) as client:
            await _configure(client, await auth_cookie(tenant.user))
            response = await client.get("/api/v1/auth/oidc/callback")
            assert response.headers["location"] == "/"

    async with async_session_maker() as session:
        user = await session.scalar(select(User).where(User.email == "nomad@idp-example.com"))
        assert user is not None
        for tenant in (one, two):
            await set_current_org(session, tenant.org.id)
            assert (
                await session.scalar(
                    select(Membership.id).where(
                        Membership.org_id == tenant.org.id, Membership.user_id == user.id
                    )
                )
                is not None
            )


async def test_the_sso_session_is_minted_for_the_callbacks_own_org(
    client_for, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A federated login is a login: the cookie it sets is a session for this org and nowhere
    else (CLAUDE.md §5)."""
    tenant = await make_tenant("oidc-scoped")
    other = await make_tenant("oidc-scoped-other", email="scoped-other@example.com")
    monkeypatch.setattr(sso, "oauth_client", lambda row: _verified("fed@idp-example.com"))

    async with client_for(tenant.host) as client:
        await _configure(client, await auth_cookie(tenant.user))
        response = await client.get("/api/v1/auth/oidc/callback")
        cookie = response.headers["set-cookie"].split(";", 1)[0]

    async with client_for(tenant.host) as client:
        assert (
            await client.get("/api/v1/meta/me", headers={"Cookie": cookie})
        ).status_code == 200
    async with client_for(other.host) as client:
        assert (
            await client.get("/api/v1/meta/me", headers={"Cookie": cookie})
        ).status_code == 401
