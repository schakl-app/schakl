"""OAuth 2.1 for MCP (docs/MCP.md): discovery, registration, consent, redemption, refresh.

The property worth asserting over and over here is the one the design is built on: **the flow
issues no new authority**. What a client ends up holding is an ``api_keys`` row belonging to the
person who consented, so it is capped by their live permissions on every request, scoped to their
tenant by hostname, and revocable. Every test below either walks the protocol or pins one of the
places that property could quietly stop holding.
"""

from __future__ import annotations

import base64
import hashlib
import secrets

from app.db import async_session_maker, set_current_org
from tests.conftest import add_membership, auth_cookie, make_tenant

_VERIFIER = secrets.token_urlsafe(48)
_CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(_VERIFIER.encode()).digest()).decode().rstrip(
    "="
)


async def _register(client, name: str = "Claude") -> dict:
    response = await client.post(
        "/api/v1/oauth/register",
        json={"client_name": name, "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"]},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _consent(client, headers, registered, *, scopes: list[str]) -> str:
    """Approve, and return the authorization code off the redirect."""
    approved = await client.post(
        "/api/v1/oauth/consent",
        json={
            "client_id": registered["client_id"],
            "redirect_uri": registered["redirect_uris"][0],
            "code_challenge": _CHALLENGE,
            "code_challenge_method": "S256",
            "scopes": scopes,
            "state": "xyz",
        },
        headers=headers,
    )
    assert approved.status_code == 200, approved.text
    redirect_to = approved.json()["redirect_to"]
    assert "state=xyz" in redirect_to
    return redirect_to.split("code=")[1].split("&")[0]


async def test_discovery_documents_name_this_tenant(client_for) -> None:
    """Both RFC documents are issued for the hostname the caller arrived on.

    The issuer *is* the tenant boundary here (§5): there is no cross-tenant authorization server
    to get wrong, because the org is resolved from the host before anything is answered.
    """
    t = await make_tenant("oauth-disco")
    async with client_for(t.host) as c:
        server = (await c.get("/api/v1/oauth/metadata/authorization-server")).json()
        assert server["issuer"].endswith(t.host)
        # The authorization endpoint is a *page* (the web app renders consent on the session it
        # already holds); the token endpoint is an API route. The split is the whole reason a
        # metadata document exists rather than a convention.
        assert server["authorization_endpoint"] == f"{server['issuer']}/oauth/authorize"
        assert server["token_endpoint"] == f"{server['issuer']}/api/v1/oauth/token"
        # S256 only: OAuth 2.1 drops `plain`, and advertising it would invite a verifier that
        # anyone who saw the authorization request already holds.
        assert server["code_challenge_methods_supported"] == ["S256"]

        # One document shape per /mcp URL, section segment included — so a section added
        # tomorrow is discoverable tomorrow with no route to add.
        resource = (
            await c.get(
                "/api/v1/oauth/metadata/protected-resource",
                params={"resource_path": "/mcp/google-ads"},
            )
        ).json()
        assert resource["resource"].endswith("/mcp/google-ads")
        assert resource["authorization_servers"] == [server["issuer"]]


async def test_unauthenticated_mcp_answers_401_with_the_challenge(client_for) -> None:
    """``/mcp`` refuses an anonymous request and says where to get a credential.

    Both halves matter. A client that speaks OAuth discovers the authorization server *by being
    refused*, so a 200 here means "Add connector" can never complete a flow. And listing 600-odd
    tool names to nobody discloses the tenant's whole module set before anyone has proved they
    may see it.
    """
    from tests.test_mcp_api import _MCP_HEADERS, mcp_running

    t = await make_tenant("oauth-challenge")
    async with mcp_running(), client_for(t.host) as c:
        response = await c.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers=_MCP_HEADERS,
        )
        assert response.status_code == 401, response.text
        challenge = response.headers["www-authenticate"]
        assert "resource_metadata=" in challenge
        assert "/.well-known/oauth-protected-resource/mcp" in challenge

        # The section's own URL advertises the section's own resource: RFC 8707 binds a token to
        # an audience, and "near enough" is not something an audience check does.
        section = await c.post(
            "/mcp/google-ads",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers=_MCP_HEADERS,
        )
        assert "/.well-known/oauth-protected-resource/mcp/google-ads" in (
            section.headers["www-authenticate"]
        )


async def test_the_full_flow_yields_a_working_api_key(client_for) -> None:
    """Register → consent → redeem → call ``/mcp``, and the token is an ordinary key.

    The last assertion is the design: the same secret authenticates a plain REST call. There is
    no second credential and no second authorization path to keep in agreement.
    """
    from tests.test_mcp_api import _rpc, mcp_running

    t = await make_tenant("oauth-flow")
    headers = await auth_cookie(t.user)
    async with mcp_running(), client_for(t.host) as c:
        await c.post("/api/v1/companies", json={"name": "OAuth BV"}, headers=headers)
        registered = await _register(c)
        assert "client_secret" not in registered  # public client: PKCE is the proof

        described = await c.get(
            "/api/v1/oauth/consent",
            params={
                "client_id": registered["client_id"],
                "redirect_uri": registered["redirect_uris"][0],
                "scope": "mcp:read",
            },
            headers=headers,
        )
        assert described.status_code == 200, described.text
        offered = [s["value"] for s in described.json()["scopes"]]
        # `mcp:read` resolved against the catalog, capped by the owner: reads only, and every
        # scoped one suffixed (§15 — a bare key stored for a scoped permission is an escalation).
        assert "companies.company.read" in offered
        assert not [s for s in offered if s.endswith(".write")]

        code = await _consent(c, headers, registered, scopes=["companies.company.read"])
        exchanged = await c.post(
            "/api/v1/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": registered["redirect_uris"][0],
                "code_verifier": _VERIFIER,
                "client_id": registered["client_id"],
            },
        )
        assert exchanged.status_code == 200, exchanged.text
        tokens = exchanged.json()
        assert tokens["token_type"] == "Bearer"
        assert tokens["access_token"].startswith("schakl_")
        # A different token prefix, so a refresh token presented as a credential is not a key
        # that nearly works — it is not one of ours at all.
        assert tokens["refresh_token"].startswith("schakr_")
        assert exchanged.headers["cache-control"] == "no-store"

        auth = {"Authorization": f"Bearer {tokens['access_token']}"}
        listed = await _rpc(c, "tools/list", auth=auth, url="/mcp/companies")
        assert "list_companies" in {tool["name"] for tool in listed["result"]["tools"]}

        called = await _rpc(c, "tools/call", {"name": "list_companies", "arguments": {}}, auth=auth)
        assert called["result"].get("isError") is not True, called

        # …and it is an ordinary API key: the same secret works on the REST surface.
        direct = await c.get("/api/v1/companies", headers={"X-API-Key": tokens["access_token"]})
        assert direct.status_code == 200, direct.text
        assert "OAuth BV" in direct.text


async def test_a_code_is_single_use_and_pkce_is_checked(client_for) -> None:
    """The second redemption of a code fails, and so does a wrong verifier.

    Single use is the database's answer (``UPDATE … WHERE redeemed_at IS NULL``), not a read
    followed by a write: a retried token request runs against two replicas that share no memory,
    and the check-then-act version has a window every retry enters (docs/PAYMENTS.md, one
    protocol over).
    """
    t = await make_tenant("oauth-replay")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        registered = await _register(c)

        code = await _consent(c, headers, registered, scopes=["companies.company.read"])
        body = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": registered["redirect_uris"][0],
            "code_verifier": _VERIFIER,
            "client_id": registered["client_id"],
        }
        assert (await c.post("/api/v1/oauth/token", data=body)).status_code == 200
        replayed = await c.post("/api/v1/oauth/token", data=body)
        assert replayed.status_code == 400
        # RFC 6749's error shape, not the house envelope: the reader is somebody else's client
        # branching on this string, and it cannot translate an i18n key.
        assert replayed.json()["error"] == "invalid_grant"

        # A fresh code with the wrong verifier fails identically — every way of being wrong
        # answers the same, so a holder of a stolen code learns nothing about what is missing.
        second = await _consent(c, headers, registered, scopes=["companies.company.read"])
        wrong = await c.post(
            "/api/v1/oauth/token", data={**body, "code": second, "code_verifier": "wrong-verifier"}
        )
        assert wrong.status_code == 400
        assert wrong.json()["error"] == "invalid_grant"


async def test_consent_cannot_exceed_what_the_person_holds(client_for) -> None:
    """A consented scope is re-derived against the catalog *and* the consenting user.

    The consent form is a browser form, so the narrowing a person did on screen is a request,
    not a fact — and a client asking for everything must not be able to talk a member into
    granting what the member does not have.
    """
    t = await make_tenant("oauth-cap")
    member = await make_tenant("oauth-cap-m", email="member-oauth-cap@example.com")
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        await add_membership(session, t.org.id, member.user.id, role="member")
        await session.commit()
    # ``member`` was conjured with its own tenant, so it holds two memberships; the session
    # under test is the one in ``t`` (a session names its org — CLAUDE.md §5).
    headers = await auth_cookie(member.user, org_id=t.org.id)
    async with client_for(t.host) as c:
        registered = await _register(c)
        offered = (
            await c.get(
                "/api/v1/oauth/consent",
                params={
                    "client_id": registered["client_id"],
                    "redirect_uri": registered["redirect_uris"][0],
                    "scope": "mcp:full",
                },
                headers=headers,
            )
        ).json()["scopes"]
        # Even asking for everything, a member is offered only what a member holds.
        assert "settings.roles.manage" not in {s["value"] for s in offered}

        refused = await c.post(
            "/api/v1/oauth/consent",
            json={
                "client_id": registered["client_id"],
                "redirect_uri": registered["redirect_uris"][0],
                "code_challenge": _CHALLENGE,
                "code_challenge_method": "S256",
                "scopes": ["settings.roles.manage"],
            },
            headers=headers,
        )
        assert refused.status_code == 403, refused.text


async def test_an_unregistered_redirect_is_refused_without_redirecting(client_for) -> None:
    """The one error class that must never be reported *by redirecting*.

    Bouncing "unknown redirect_uri" to the URI in question is the open redirector the exact-match
    list exists to prevent — and it would hand an attacker the ``state`` as well.
    """
    t = await make_tenant("oauth-redirect")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        registered = await _register(c)
        response = await c.get(
            "/api/v1/oauth/consent",
            params={
                "client_id": registered["client_id"],
                "redirect_uri": "https://evil.example/steal",
                "scope": "mcp:read",
            },
            headers=headers,
        )
        assert response.status_code == 400
        assert "location" not in response.headers

        # A prefix of a registered URI is not a registered URI: matching is equality.
        prefixed = await c.get(
            "/api/v1/oauth/consent",
            params={
                "client_id": registered["client_id"],
                "redirect_uri": registered["redirect_uris"][0] + ".evil.example",
                "scope": "mcp:read",
            },
            headers=headers,
        )
        assert prefixed.status_code == 400

        # And an http target that is not loopback is refused at registration time.
        plaintext = await c.post(
            "/api/v1/oauth/register",
            json={"client_name": "x", "redirect_uris": ["http://evil.example/cb"]},
        )
        assert plaintext.status_code == 422, plaintext.text


async def test_a_client_is_scoped_to_the_tenant_it_registered_on(client_for) -> None:
    """A client registered on one host does not exist on another (Golden Rule 1)."""
    a = await make_tenant("oauth-iso-a")
    b = await make_tenant("oauth-iso-b")
    async with client_for(a.host) as ca:
        registered = await _register(ca)
    async with client_for(b.host) as cb:
        crossed = await cb.post(
            "/api/v1/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": "anything",
                "redirect_uri": registered["redirect_uris"][0],
                "code_verifier": _VERIFIER,
                "client_id": registered["client_id"],
            },
        )
        assert crossed.status_code == 401
        assert crossed.json()["error"] == "invalid_client"


async def test_refresh_rotates_the_access_token_and_disconnect_kills_both(client_for) -> None:
    """A refresh mints a new access token on the same grant; disconnecting revokes the lot.

    Revoking the *client* rather than this user's keys is the honest kill switch: a connector
    that has been disconnected must not be able to refresh its way back in.
    """
    t = await make_tenant("oauth-refresh")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        registered = await _register(c)
        code = await _consent(c, headers, registered, scopes=["companies.company.read"])
        first = (
            await c.post(
                "/api/v1/oauth/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": registered["redirect_uris"][0],
                    "code_verifier": _VERIFIER,
                    "client_id": registered["client_id"],
                },
            )
        ).json()

        refreshed = await c.post(
            "/api/v1/oauth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": first["refresh_token"],
                "client_id": registered["client_id"],
            },
        )
        assert refreshed.status_code == 200, refreshed.text
        rotated = refreshed.json()
        assert rotated["access_token"] != first["access_token"]
        assert (
            await c.get("/api/v1/companies", headers={"X-API-Key": rotated["access_token"]})
        ).status_code == 200
        # The superseded access token is gone — the row's secret was rotated, not duplicated.
        assert (
            await c.get("/api/v1/companies", headers={"X-API-Key": first["access_token"]})
        ).status_code == 401

        connections = (await c.get("/api/v1/oauth/connections", headers=headers)).json()
        assert [row["client_name"] for row in connections] == ["Claude"]
        assert connections[0]["sessions"] == 1

        dropped = await c.delete(
            f"/api/v1/oauth/connections/{connections[0]['id']}", headers=headers
        )
        assert dropped.status_code == 204
        assert (
            await c.get("/api/v1/companies", headers={"X-API-Key": rotated["access_token"]})
        ).status_code == 401
        # …and it cannot refresh its way back in.
        denied = await c.post(
            "/api/v1/oauth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": rotated["refresh_token"],
                "client_id": registered["client_id"],
            },
        )
        assert denied.status_code == 401
