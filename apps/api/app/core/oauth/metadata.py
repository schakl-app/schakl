"""The two discovery documents, and the one routing fact that shapes where they are served.

RFC 9728 (protected resource) and RFC 8414 (authorization server) both put their documents at
``/.well-known/…`` on the **root of the host**. The edge here routes exactly ``/api/`` and
``/mcp`` to this service and everything else to the SSR web app (CLAUDE.md §12: *a route the
edge does not forward is a route nobody has*), so the root is not ours to answer on — and the
API reference already learned that lesson the expensive way, by being unreachable in every
deployment for months.

Two ways out, and only one of them works on an instance that already exists. Adding an edge rule
for ``/.well-known/oauth-*`` would need every self-hosted install to update its Traefik config
before their connector worked, with the failure mode "Add connector does nothing" and no screen
able to explain it. So instead the **web app serves the documents and the API authors them**:
two thin SvelteKit routes proxy the JSON below, and nothing at the edge changes.

The endpoints the documents *advertise* are then free to live where they belong — the token and
registration endpoints under ``/api/v1/oauth`` because they are machine-to-machine, and the
authorization endpoint at ``/oauth/authorize`` because it is a page a person reads and consents
on. A metadata document is exactly the mechanism for saying so.
"""

from __future__ import annotations

from typing import Any

#: What ``expand_scopes`` understands as a coarse request. Advertised so a client that reads
#: metadata (rather than guessing) asks for something this server actually resolves.
ADVERTISED_SCOPES = ["mcp:read", "mcp:full"]


def protected_resource_document(origin: str, resource_path: str) -> dict[str, Any]:
    """RFC 9728 for one ``/mcp`` URL — the section segment included.

    ``resource`` names the exact URL the client was refused on, because that is what the token
    is audience-bound to (RFC 8707) and "near enough" is not a thing an audience check does.
    One document shape serves every section: the web route is a catch-all, so a section added
    tomorrow is discoverable tomorrow with no route to add.
    """
    return {
        "resource": f"{origin}{resource_path}",
        "authorization_servers": [origin],
        "scopes_supported": ADVERTISED_SCOPES,
        "bearer_methods_supported": ["header"],
        "resource_documentation": f"{origin}/api/docs",
    }


def authorization_server_document(origin: str) -> dict[str, Any]:
    """RFC 8414. The issuer is the tenant's own origin, and that is the tenant boundary.

    A session belongs to one org and the token says which (§5). Here the *hostname* is what says
    which: a client that discovered this document on ``klant-a.schakl.app`` gets an authorization
    endpoint, a token endpoint and ultimately an ``api_keys`` row that all belong to that org and
    are simply not found on any other. There is no cross-tenant issuer to get wrong because
    there is no cross-tenant issuer.
    """
    return {
        "issuer": origin,
        # A page, not an API route: consent runs on the browser session the web app already
        # holds (local password + 2FA, or this org's OIDC), so it is served by the web app.
        "authorization_endpoint": f"{origin}/oauth/authorize",
        "token_endpoint": f"{origin}/api/v1/oauth/token",
        "registration_endpoint": f"{origin}/api/v1/oauth/register",
        "revocation_endpoint": f"{origin}/api/v1/oauth/revoke",
        "scopes_supported": ADVERTISED_SCOPES,
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        # S256 only. OAuth 2.1 drops `plain`, and advertising it would invite a client to send a
        # verifier that anyone who saw the authorization request already holds.
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
        "service_documentation": f"{origin}/api/docs",
    }
