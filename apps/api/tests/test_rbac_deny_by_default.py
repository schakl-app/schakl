"""Deny-by-default: an endpoint with no declared permission is a build break (issue #19, #50).

Three tests, in the order they must be trusted:

1. **Anti-vacuum.** ``include_router`` is lazy on this FastAPI, so ``app.routes`` holds two
   health probes and one ``_IncludedRouter`` — the 154 real routes sit two wrapper levels down.
   A guardrail that iterates ``app.routes`` finds nothing to check and is permanently green.
   Asserting *leaf count == OpenAPI operation count* makes a future Starlette refactor fail loud.
2. **Introspection.** Every ``/api/v1`` leaf carries a permission marker, an exemption marker,
   or an entry on the shrinking allowlist below. Fast, and it fails with the route's name.
3. **Behaviour.** Every ``/api/v1`` operation, called by a member holding zero permissions,
   returns ``403``. Behaviour cannot go vacuous across a refactor; introspection just did.
"""

from __future__ import annotations

import uuid

from fastapi.routing import APIRoute
from sqlalchemy import text

from app.core.permissions.deps import iter_route_leaves, route_markers
from app.db import async_session_maker, set_current_org
from app.main import app
from tests.conftest import auth_cookie, make_tenant

#: Infra routes: not under ``/api/v1``, not authenticated, deliberately dependency-free.
_INFRA_ROUTE_NAMES = frozenset({"health", "health_ready"})

#: Empty, and it stays empty. It existed only so ``dev`` stayed green across the two commits
#: between the dependency landing (#50) and the last module converting (#52). A new module
#: declares its permissions on its ``ModuleDescriptor``; it does not get an exception here.
_UNDECLARED_ROUTERS: frozenset[str] = frozenset()

#: Operations that legitimately answer before a permission exists. Kept as (method, path) so the
#: behavioural sweep and the marker on the route can drift apart loudly rather than quietly.
_EXEMPT_PREFIXES = ("/api/v1/auth", "/api/v1/users", "/api/v1/setup", "/api/v1/instance")
_EXEMPT_OPERATIONS = frozenset(
    {
        ("get", "/api/v1/meta/tenant"),
        ("get", "/api/v1/meta/modules"),
        # The custom-domain routing proof (#291 follow-up): fetched over the public internet
        # from a hostname that has no session at all — that is the whole point of it. Says
        # which org a hostname reaches and echoes the caller's nonce; nothing else.
        ("get", "/api/v1/meta/domain-probe"),
        # Instance posture (epic #199): like /meta/tenant, the web shell needs it before any
        # session exists (it routes the cloud apex host to the console on it). No tenant data.
        ("get", "/api/v1/meta/instance"),
        ("get", "/api/v1/meta/me"),
        ("patch", "/api/v1/meta/me"),
        ("get", "/api/v1/prefs"),
        ("put", "/api/v1/prefs"),
        ("get", "/api/v1/members/lookup"),
        # The code-defined registry. Holds no tenant data — it ships in the open-source repo.
        ("get", "/api/v1/permissions/catalog"),
        # Any signed-in member may fetch their tenant's files (#123); the row is RLS-scoped,
        # so a random id 404s here rather than 403s.
        ("get", "/api/v1/files/{file_id}"),
        # Listing is the same exposure as fetching each one; filtered to one entity, RLS-scoped.
        ("get", "/api/v1/files"),
        # The code-defined impex registry (which entities support CSV) — no tenant data; each
        # entity's actual export/import route declares its own permission.
        ("get", "/api/v1/impex/entities"),
        # Branding assets render on the login screen before a session exists; only rows
        # tagged with a public entity type are reachable, anything else 404s.
        ("get", "/api/v1/files/{file_id}/public"),
        # Ending your own portal impersonation (#296). It runs *as the impersonated client*, who
        # holds none of the agency's permissions — gating the only way out behind a permission
        # the impersonated account cannot have would trap someone inside the session. With no
        # grant on the request it mutates nothing and answers 204, which is what both sweeps see
        # here; with one, it records the stop on the contact's trail and clears the cookie.
        ("post", "/api/v1/portal/impersonation/stop"),
        # Google Calendar push notifications carry no user session at all; the route
        # authenticates with our own per-channel token and 404s anything that doesn't match
        # (docs/GOOGLE.md — webhooks map back to org + connection via our own channel token).
        ("post", "/api/v1/google/calendar/webhook"),
        # Payment-provider callbacks, same shape and the same reasons (epic #269): no session,
        # no tenant hostname — the org rides in a token we minted, the secret is compared in
        # constant time, and the status is taken from an authenticated re-fetch rather than
        # from the body. Everything unrecognised answers a bare 404, which is what this sweep
        # sees. docs/PAYMENTS.md holds the five gates in order.
        ("post", "/api/v1/invoicing/payments/webhook/{provider}/{token}"),
        # The public invoice link (#304). The same shape a third time, and deliberately so: no
        # session, a capability token in the URL, everything unrecognised a bare 404 — which is
        # what this sweep sees, because it fills path params with a random UUID.
        #
        # What keeps it honest is *not* an exemption here, it is that the reader context these
        # routes build is a client-portal session scoped to one company holding two ``:own``
        # permissions (``invoicing/public.py``). ``tests/test_invoicing_public.py`` is where
        # that is actually asserted: a valid token reaches its own invoice and nothing else,
        # and a member with no permissions is still refused every *signed-in* invoicing route.
        ("get", "/api/v1/invoicing/public/invoices/{token}"),
        ("get", "/api/v1/invoicing/public/invoices/{token}/preview"),
        ("get", "/api/v1/invoicing/public/invoices/{token}/pdf"),
        ("post", "/api/v1/invoicing/public/invoices/{token}/payment-intents"),
        ("post", "/api/v1/invoicing/public/invoices/{token}/refresh"),
        # Uptime Kuma posting a heartbeat (docs/UPTIME.md §11). The same shape a fourth time:
        # no session, a capability token in the URL, and everything unrecognised a bare 404 —
        # which is what this sweep sees, because it fills path params with a random UUID.
        #
        # What keeps it honest is not this exemption. The route's whole write surface is one
        # heartbeat row on a monitor it already holds and one notification event: it never
        # creates, never writes configuration, and is capped before the body is parsed.
        # `tests/test_uptime_webhook.py` asserts each of those, plus that a wrong secret, an
        # unknown instance and another tenant's instance are indistinguishable.
        ("post", "/api/v1/uptime/hook/{token}"),
        # OAuth 2.1 for MCP (docs/MCP.md). Every one of these is reached *before* a credential
        # exists — that is what an authorization server is for — so a 403 here would mean no
        # client could ever obtain one. They are exempt from this sweep and gated instead by
        # what they can actually do, which `tests/test_mcp_oauth.py` asserts one by one:
        #
        #   • the two metadata documents are RFC 8414/9728 discovery: code-defined URLs and
        #     nothing else, the same three constants for every caller, no tenant data.
        #   • `register` is RFC 7591 dynamic registration — the one unauthenticated *write* a
        #     stranger can repeat. Rate-limited by IP, capped per org, and it grants nothing:
        #     the row can read no byte of tenant data until a person signs in and consents.
        #   • `token` and `revoke` carry their own credential — the authorization code plus its
        #     PKCE verifier, or the token being revoked. A code is single-use (enforced by a
        #     conditional UPDATE, not a read-then-write), and every way of being wrong answers
        #     the same `invalid_grant`, so a holder of a stolen one learns nothing.
        #
        # The routes that *do* have a session are deliberately absent: `POST /oauth/consent`
        # declares `apikeys.personal.manage` — consenting is minting a personal key, with a
        # redirect instead of a copy button — and so do the connection-management routes.
        ("get", "/api/v1/oauth/metadata/authorization-server"),
        ("get", "/api/v1/oauth/metadata/protected-resource"),
        ("post", "/api/v1/oauth/register"),
        ("post", "/api/v1/oauth/token"),
        ("post", "/api/v1/oauth/revoke"),
        # Describes the client asking for consent, so the screen can render before anything is
        # written. It reveals no tenant row: the scope list is the code-defined permission
        # catalog intersected with what *this caller already holds*, which is the one set they
        # cannot learn anything new from.
        ("get", "/api/v1/oauth/consent"),
        # Which MCP tool sections this instance serves (docs/MCP.md): module route prefixes and
        # tool counts, code-defined. The sibling of `/meta/modules`, exempt above and already
        # public. Every route behind those URLs still declares its own permission — a section
        # narrows a *listing*, never an authorization.
        ("get", "/api/v1/meta/mcp"),
    }
)

_HTTP_METHODS = ("get", "post", "put", "patch", "delete")


def _leaves() -> list[APIRoute]:
    return list(iter_route_leaves(app.routes))


def _operations() -> list[tuple[str, str, list[str]]]:
    """``(method, path, tags)`` for every operation in the OpenAPI document."""
    return [
        (method, path, operation.get("tags", []))
        for path, operations in app.openapi()["paths"].items()
        for method, operation in operations.items()
        if method in _HTTP_METHODS
    ]


def test_leaf_traversal_sees_every_operation() -> None:
    """If this fails, the two tests below have quietly stopped checking anything."""
    assert len(_leaves()) == len(_operations())


def test_every_route_declares_a_permission_or_an_exemption() -> None:
    offenders: list[str] = []
    for route in _leaves():
        if route.name in _INFRA_ROUTE_NAMES or not route.include_in_schema:
            continue
        if set(route.tags) & _UNDECLARED_ROUTERS:
            continue
        permissions, exemptions = route_markers(route)
        if not permissions and not exemptions:
            offenders.append(f"{sorted(route.methods)} {route.name} (tags={route.tags})")
    assert not offenders, (
        "these routes declare neither a permission nor an exemption — deny-by-default "
        "(CLAUDE.md §9, issue #19):\n  " + "\n  ".join(offenders)
    )


def test_declared_permissions_exist_in_the_catalog() -> None:
    from app.core.permissions.catalog import permission_keys

    known = set(permission_keys())
    for route in _leaves():
        for permission, scope in route_markers(route)[0]:
            assert permission in known, f"{route.name} declares unknown permission {permission!r}"
            assert scope in (None, "own", "any"), f"{route.name}: bad scope {scope!r}"


def _url(path: str) -> str:
    """Fill path parameters with a UUID. Dependencies run before path validation, so any
    syntactically valid value does; the request never reaches the handler."""
    filled = path
    while "{" in filled:
        start = filled.index("{")
        end = filled.index("}", start)
        filled = filled[:start] + str(uuid.uuid4()) + filled[end + 1 :]
    return filled


async def test_a_member_with_no_permissions_is_refused_everywhere(client_for) -> None:
    """The real gate. Introspection can go vacuous; a 200 cannot."""
    tenant = await make_tenant("deny-default", role="member")
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        await session.execute(text("DELETE FROM membership_roles"))
        await session.commit()

    headers = await auth_cookie(tenant.user)
    allowed: list[str] = []
    async with client_for(tenant.host) as client:
        for method, path, tags in _operations():
            if not path.startswith("/api/v1") or path.startswith(_EXEMPT_PREFIXES):
                continue
            if (method, path) in _EXEMPT_OPERATIONS:
                continue
            if set(tags) & _UNDECLARED_ROUTERS:
                continue
            response = await client.request(
                method.upper(), _url(path), headers=headers, json={}
            )
            if response.status_code != 403:
                allowed.append(f"{method.upper()} {path} -> {response.status_code}")

    assert not allowed, (
        "a member holding zero permissions reached these operations:\n  " + "\n  ".join(allowed)
    )


def test_the_undeclared_router_allowlist_is_empty() -> None:
    assert _UNDECLARED_ROUTERS == frozenset(), (
        "the allowlist is a migration aid, not an escape hatch: it was emptied by #52 and a "
        "new module declares its permissions on its ModuleDescriptor."
    )


# --------------------------------------------------------------------------- #
# The client role is read-only + own comments (issue #244)
# --------------------------------------------------------------------------- #
#: The domain modules a portal ``client`` can reach — its read horizon (#193). The role holds the
#: read on each and must hold no write on any of them.
_CLIENT_MODULE_PREFIXES = (
    "/api/v1/companies",
    "/api/v1/contacts",
    "/api/v1/domains",
    "/api/v1/hosting",
    "/api/v1/websites",
    "/api/v1/projects",
    "/api/v1/tasks",
)

#: The ONLY write a ``client`` legitimately holds on those prefixes: its own comment on a
#: client-visible task (``tasks.comment.write:own``). Its permission passes, so a random id answers
#: 404/422 — never 403 — which is exactly why it must be excluded from the "everything else 403s"
#: sweep rather than asserted 403.
_CLIENT_ALLOWED_WRITES = frozenset(
    {
        ("post", "/api/v1/tasks/{task_id}/comments"),
        ("patch", "/api/v1/tasks/{task_id}/comments/{comment_id}"),
        ("delete", "/api/v1/tasks/{task_id}/comments/{comment_id}"),
    }
)

#: Canonical list reads that prove the client genuinely holds the module reads, so the write sweep
#: below cannot pass vacuously by 403-ing a role that turned out to hold nothing.
_CLIENT_CANONICAL_READS = (
    "/api/v1/companies",
    "/api/v1/contacts",
    "/api/v1/tasks",
)


async def test_client_role_is_read_only_except_own_comments(client_for) -> None:
    """The portal is read-only + own task comments (issue #244).

    A ``client``-role membership holds every client-reachable module's *read* and no *write* but
    its own comment. This walks the live route table, so a new write route on a client-reachable
    module ships covered. It is distinct from the zero-permission sweep above: a client is *not* an
    empty-handed member — it holds the reads — so a write route that lost its ``require_permission``
    or carried a too-loose ``:own`` scope would still 403 the empty-handed member (the row check
    404s) yet leak to a client here. A failure is a real deny-by-default gap to escalate (issue
    #244, step 4), not merely a UI affordance that renders a button the API refuses.
    """
    tenant = await make_tenant("client-readonly", role="client")
    headers = await auth_cookie(tenant.user)

    leaked_writes: list[str] = []
    async with client_for(tenant.host) as client:
        # Non-vacuous: the client can read. A directly-invited client is not a portal login, so no
        # horizon narrows it — it reads the tenant's own rows (RLS only).
        for path in _CLIENT_CANONICAL_READS:
            read = await client.get(path, headers=headers)
            assert read.status_code == 200, (
                f"a client-role membership was refused a read it must hold: GET {path} "
                f"-> {read.status_code}"
            )

        for method, path, _tags in _operations():
            if method == "get" or not path.startswith(_CLIENT_MODULE_PREFIXES):
                continue
            if (method, path) in _EXEMPT_OPERATIONS or path.startswith(_EXEMPT_PREFIXES):
                continue
            response = await client.request(
                method.upper(), _url(path), headers=headers, json={}
            )
            if (method, path) in _CLIENT_ALLOWED_WRITES:
                # The comment permission passes, so this answers 404/422 on a random id, never 403.
                # Pinning it keeps the allow-list honest: if this route ever starts 403-ing a
                # client, the grant regressed and this assert catches it.
                assert response.status_code != 403, (
                    f"the client's own-comment write was refused: {method.upper()} {path}"
                )
                continue
            if response.status_code != 403:
                leaked_writes.append(f"{method.upper()} {path} -> {response.status_code}")

    assert not leaked_writes, (
        "a client-role membership reached a write outside its own comments — the portal must be "
        "read-only (issue #244, step 4 — a deny-by-default gap, escalate):\n  "
        + "\n  ".join(leaked_writes)
    )
