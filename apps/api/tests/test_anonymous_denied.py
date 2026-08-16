"""Authentication is deny-by-default too: a caller with no credential reaches nothing.

``tests/test_rbac_deny_by_default.py`` is the *authorization* sweep — it signs a member in,
strips every permission, and demands a 403 everywhere. That left the layer underneath it
untested: nothing asserted what happens with **no credential at all**, and the two questions
have different answers behind different gates (``require_context`` answers 401 long before a
permission is looked up). The gap was not theoretical. It is exactly how the interactive API
reference came to serve 583 paths and 817 schemas to anyone who could resolve the hostname, on
every instance ever shipped, in a codebase that already swept every route for a declared
permission — the sweep asked whether the caller *may*, never whether there was a caller.

So, mirroring that file's argument for why one layer is never enough:

1. **Introspection.** Every operation resolves one of four recognised authenticators, or its
   ``(method, path)`` is named below with the reason it answers without one. Fast, and it fails
   with the route's name.
2. **Behaviour.** Every operation, called with no cookie and no key, must refuse — 401, 403 or
   404. Anything that *processes* the request (a 2xx, or a 422 proving the body was validated
   and the handler reached) is either a hole or belongs on the list. Introspection can go
   vacuous across a refactor; a 200 cannot.
3. **The surfaces the document cannot see.** The reference and the MCP server are not
   operations in the spec, so neither sweep reaches them — and being invisible to the sweep is
   precisely how the reference stayed open.

The authenticators are matched **by identity, never by name**, and the trap is worth stating:
fastapi-users builds both ``current_active_user`` and ``current_active_user_optional`` from one
factory, so both callables are named ``current_user_dependency``. Matching that name would
accept the optional one — which authenticates nobody, and which ``require_context`` uses
precisely so that it can decide for itself whether a session was required.
"""

from __future__ import annotations

import uuid

from fastapi.routing import APIRoute

from app.core.auth.users import current_active_user
from app.core.permissions.deps import iter_route_leaves, operation_index
from app.core.tenancy import require_context
from app.main import app
from tests.conftest import auth_cookie, make_tenant

#: Statuses that mean "refused before anything happened". A 404 counts: an unknown host, a
#: surface this deployment does not serve, and a capability token that matched nothing are all
#: ways of saying no without confirming what exists.
_REFUSALS = frozenset({401, 403, 404})

#: Routes deliberately kept out of the OpenAPI document (they are not product API, so they must
#: not become MCP tools or methods on the generated client). Pinned because the exclusion also
#: hides them from both sweeps above, so a new one is a decision, never a side effect.
_NOT_IN_THE_DOCUMENT = frozenset(
    {
        # Renders in the situation where the SSR web app is unreachable, so there is no session
        # to read; it serves the same public branding /meta/tenant does (app/core/errorpage.py).
        "edge_error_page",
        # The interactive reference (app/core/apidocs.py), asserted by status below and in
        # tests/test_api_docs.py.
        "openapi_document",
        "swagger_ui",
        "redoc",
    }
)


def _authenticators() -> set[object]:
    """The four callables that can establish who is calling. Imported for identity."""
    from app.core.cloud.provisioning import require_provisioning_key
    from app.modules.invoicing.public import require_public_invoice

    return {
        # A session or an API key, for *this* org: 401 with neither, 403 with no membership.
        require_context,
        # A session only, with no tenant resolved — own-account routes, 2FA enrolment, and the
        # instance surface, which is gated on ``users.is_superuser``, a third axis (CLAUDE.md §5).
        current_active_user,
        # An instance API key (cloud provisioning, epic #199).
        require_provisioning_key,
        # A capability token in the URL, which builds a client-portal context scoped to the one
        # company it names (#304).
        require_public_invoice,
    }


def _dependency_calls(dependant, out: list | None = None) -> list:
    if out is None:
        out = []
    for sub in dependant.dependencies:
        if sub.call is not None:
            out.append(sub.call)
        _dependency_calls(sub, out)
    return out


def _authenticated(route: APIRoute, known: set[object]) -> bool:
    return any(call in known for call in _dependency_calls(route.dependant))


#: Every operation that legitimately answers without a credential, and why. Each entry is a
#: standing claim that the route carries its own proof of who is calling, or genuinely needs
#: none. Adding one is a security decision; the reason is where it gets reviewed.
_UNAUTHENTICATED: dict[tuple[str, str], str] = {
    # --- Before a session can exist -------------------------------------------------------- #
    # The login surface, password reset, e-mail verification and the OIDC round trip. All of it
    # precedes authentication by definition; the account lookup is narrowed to the request's org
    # so none of it enumerates across tenants (CLAUDE.md §5), and login is rate-limited per IP.
    ("post", "/api/v1/auth/login"): "the login route itself",
    ("post", "/api/v1/auth/logout"): "clearing a cookie needs no proof that it was valid",
    ("post", "/api/v1/auth/register"): "account creation, where the org allows it at all",
    ("post", "/api/v1/auth/forgot-password"): "answers the same whether or not the address exists",
    ("post", "/api/v1/auth/reset-password"): "the token in the body is the credential",
    ("post", "/api/v1/auth/verify"): "the token in the body is the credential",
    ("post", "/api/v1/auth/request-verify-token"): "same shape as forgot-password",
    ("get", "/api/v1/auth/oidc/login"): "starts the OIDC round trip; there is no session yet",
    ("get", "/api/v1/auth/oidc/callback"): (
        "the provider's code plus our own state is the credential"
    ),
    # The 2FA step sits *between* a correct password and a session, so it cannot require one:
    # the challenge token is the credential, single-use, and it names the org to mint for.
    ("post", "/api/v1/auth/2fa/verify"): "redeems the login challenge; no session exists yet",
    ("post", "/api/v1/auth/2fa/challenge/sms"): "sends the code for a challenge already in hand",
    # --- The first-run wizard ---------------------------------------------------------------- #
    # Open only while the instance has zero orgs: ``run_setup`` takes an advisory lock and 409s
    # the second caller, so the window is "before anyone installed this", not "forever".
    ("post", "/api/v1/setup"): "creates the first org, and refuses once one exists",
    ("get", "/api/v1/setup/status"): "whether the wizard is needed; one boolean",
    # --- Read before a login screen can render ------------------------------------------------ #
    # Public by design and already so before this file existed. Worth being honest about what
    # that costs: /meta/tenant and /meta/modules disclose the tenant's brand and its enabled
    # module set to anyone who resolves the hostname. That is the price of a white-label login
    # screen rendering the agency's own logo before anybody has typed a password.
    ("get", "/api/v1/meta/tenant"): "the tenant's own logo and colours; the login screen needs it",
    ("get", "/api/v1/meta/modules"): "instance capabilities; the login screen renders from it",
    ("get", "/api/v1/meta/instance"): (
        "deployment posture; the web shell routes the apex host on it"
    ),
    ("get", "/api/v1/meta/domain-probe"): "routing proof: which org a hostname reaches, if any",
    ("get", "/api/v1/files/{file_id}/public"): "branding assets only; every other row 404s",
    # --- Somebody else's server calling us ----------------------------------------------------- #
    # Four callbacks. None has a session and none could: the org rides in a token *we* minted,
    # compared in constant time, and every way of being wrong is one indistinguishable refusal.
    # docs/PAYMENTS.md holds the five gates in order; the shape is reused verbatim by the rest.
    ("post", "/api/v1/google/calendar/webhook"): "our own per-channel token names the connection",
    ("post", "/api/v1/invoicing/payments/webhook/{provider}/{token}"): (
        "our own per-account token names the tenant, and the payment's status comes from an "
        "authenticated re-fetch rather than from the body"
    ),
    ("post", "/api/v1/uptime/hook/{token}"): "our own token names the tenant; one heartbeat row",
    ("post", "/api/v1/snelstart/coupling/callback"): (
        "SnelStart posts every partner's couplings to one URL, so the tenant rides in the "
        "referenceKey's secret half rather than in the hostname"
    ),
    # --- Handing a credential to someone who has none yet -------------------------------------- #
    # OAuth 2.1 for MCP (docs/MCP.md). An authorization server that demanded a credential before
    # issuing one would issue none; each of these carries its own proof instead.
    ("get", "/api/v1/oauth/metadata/authorization-server"): "RFC 8414 discovery; code-defined URLs",
    ("get", "/api/v1/oauth/metadata/protected-resource"): "RFC 9728 discovery; code-defined URLs",
    ("post", "/api/v1/oauth/register"): "RFC 7591; grants nothing until a person consents",
    ("post", "/api/v1/oauth/token"): (
        "the authorization code and its PKCE verifier are the credential"
    ),
    ("post", "/api/v1/oauth/revoke"): "the token being revoked is the credential",
    # --- The instance impersonation handoff ----------------------------------------------------- #
    ("post", "/api/v1/instance/impersonation/claim"): (
        "redeems a single-use ticket bound to host, org, impersonator and target (#288). It "
        "cannot be reached with a session — the administrator has none on this hostname yet — so "
        "the ticket is the credential, and every authorization decision was already made when it "
        "was issued, behind instance.impersonate"
    ),
    # --- Infrastructure ------------------------------------------------------------------------ #
    ("get", "/health"): "liveness; must stay cheap and dependency-free for orchestrators",
    ("get", "/health/ready"): "readiness; reports its own status and no tenant data",
}


def _operations():
    operations, unclaimed = operation_index(app)
    assert not unclaimed, (
        "these operations matched no route, so neither sweep below covers them — the join in "
        f"app.core.permissions.deps.operation_index needs widening: {unclaimed[:5]}"
    )
    # Anti-vacuum: a join that quietly stopped matching would make both tests permanently green.
    assert len(operations) > 500, len(operations)
    return operations


#: The account routes fastapi-users builds for us. They *are* authenticated — each carries a
#: ``current_user`` dependency — but the library constructs those callables inside
#: ``get_users_router``, so there is no object here to compare identity against, and the factory
#: returns a fresh function on every call. Rather than match on a name (which is exactly the trap
#: this file's docstring warns about, since the optional variant shares it), they are named here
#: and pinned by ``test_the_account_routes_are_gated`` below, which asserts the two things that
#: matter: no credential is refused, and the by-id half is instance-owner-only.
_FASTAPI_USERS_ACCOUNT_ROUTES = frozenset(
    {
        ("get", "/api/v1/users/me"),
        ("patch", "/api/v1/users/me"),
        ("get", "/api/v1/users/{id}"),
        ("patch", "/api/v1/users/{id}"),
        ("delete", "/api/v1/users/{id}"),
    }
)


def test_every_operation_authenticates_or_is_named_here() -> None:
    known = _authenticators()
    offenders: list[str] = []
    for operation in _operations():
        if _authenticated(operation.route, known):
            continue
        if (operation.method, operation.path) in _UNAUTHENTICATED:
            continue
        if (operation.method, operation.path) in _FASTAPI_USERS_ACCOUNT_ROUTES:
            continue
        offenders.append(
            f"{operation.method.upper()} {operation.path} ({operation.route.name})"
        )
    assert not offenders, (
        "these operations resolve no authenticator and are not named in _UNAUTHENTICATED — say "
        "what proves who is calling, or add the gate:\n  " + "\n  ".join(sorted(offenders))
    )


def test_the_unauthenticated_list_has_no_dead_entries() -> None:
    """An entry for an operation that no longer exists is a reason nobody is applying, and it
    would silently cover the day that path comes back under different gating."""
    live = {(operation.method, operation.path) for operation in _operations()}
    assert not set(_UNAUTHENTICATED) - live, sorted(set(_UNAUTHENTICATED) - live)


def test_routes_kept_out_of_the_document_are_named_here() -> None:
    """``include_in_schema=False`` hides a route from both sweeps in this file, so the set of
    routes using it is pinned rather than trusted."""
    hidden = {route.name for route in iter_route_leaves(app.routes) if not route.include_in_schema}
    assert hidden == _NOT_IN_THE_DOCUMENT


def _url(path: str) -> str:
    """Fill path parameters with a UUID. Dependencies run before path validation, so any
    syntactically valid value does; the request never reaches the handler."""
    filled = path
    while "{" in filled:
        start = filled.index("{")
        end = filled.index("}", start)
        filled = filled[:start] + str(uuid.uuid4()) + filled[end + 1 :]
    return filled


async def test_no_credential_reaches_nothing(client_for) -> None:
    """The real gate. Every operation in the document, called by nobody."""
    tenant = await make_tenant("anon-sweep", role="owner")
    reached: list[str] = []
    async with client_for(tenant.host) as client:
        for operation in _operations():
            if (operation.method, operation.path) in _UNAUTHENTICATED:
                continue
            response = await client.request(
                operation.method.upper(), _url(operation.path), json={}
            )
            if response.status_code not in _REFUSALS:
                reached.append(
                    f"{operation.method.upper()} {operation.path} -> {response.status_code}"
                )
    assert not reached, (
        "a caller with no credential was served by these operations:\n  " + "\n  ".join(reached)
    )


async def test_the_account_routes_are_gated(client_for) -> None:
    """``/api/v1/users`` is the one prefix both sweeps in this repo skipped.

    ``tests/test_rbac_deny_by_default.py`` exempts it by prefix (correctly — these routes have no
    permission to declare, because ``users`` is instance-level and precedes any membership), and
    until this file existed nothing replaced that coverage. It is worth having: the by-id half is
    CRUD over the **instance's** user table, so a gate that slipped here would not be scoped by
    RLS, by an org, or by a company horizon. fastapi-users gates it on ``is_superuser``; an org
    owner is not one (CLAUDE.md §5 — a different axis), so 403 is the right answer for the most
    privileged person the tenant has.
    """
    tenant = await make_tenant("account-routes", role="owner")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        for method, path in sorted(_FASTAPI_USERS_ACCOUNT_ROUTES):
            url = path.replace("{id}", str(uuid.uuid4()))
            anonymous = await client.request(method.upper(), url, json={})
            assert anonymous.status_code == 401, f"anonymous {method} {path}"
            if path.endswith("/me"):
                continue
            owner = await client.request(method.upper(), url, headers=headers, json={})
            assert owner.status_code == 403, f"org owner {method} {path} -> {owner.status_code}"


async def test_the_interactive_reference_is_not_public(client_for) -> None:
    """Out of the document, so the sweep above cannot see it — and being invisible to the sweep
    is exactly how it stayed open. tests/test_api_docs.py holds the rest of its gate."""
    tenant = await make_tenant("anon-docs", role="owner")
    async with client_for(tenant.host) as client:
        for path in ("/api/openapi.json", "/api/docs", "/api/redoc"):
            assert (await client.get(path)).status_code == 401, path


async def test_the_mcp_surface_is_not_public(client_for) -> None:
    """Also outside the document: ``/mcp`` is a mounted ASGI app, not a route. An anonymous
    ``tools/list`` once disclosed the tenant's whole module set (docs/MCP.md); it answers the
    401 challenge an OAuth client discovers the server by now, on every section."""
    tenant = await make_tenant("anon-mcp", role="owner")
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    headers = {"Accept": "application/json, text/event-stream"}
    async with client_for(tenant.host) as client:
        for path in ("/mcp/", "/mcp/compact", "/mcp/companies"):
            response = await client.post(path, json=body, headers=headers)
            assert response.status_code == 401, f"{path} -> {response.status_code}"
            assert "tools" not in response.text
