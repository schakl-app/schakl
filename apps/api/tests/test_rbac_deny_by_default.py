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

#: Routers whose permissions land in a later sub-issue of #19 (#51 then #52). **It only ever
#: shrinks**, and #52 empties it and adds the test that keeps it empty. Without it, `dev` would
#: be red for the two commits in between.
_UNDECLARED_ROUTERS: frozenset[str] = frozenset(
    {"tasks", "time", "leave", "notifications"}
)

#: Operations that legitimately answer before a permission exists. Kept as (method, path) so the
#: behavioural sweep and the marker on the route can drift apart loudly rather than quietly.
_EXEMPT_PREFIXES = ("/api/v1/auth", "/api/v1/users", "/api/v1/setup", "/api/v1/instance")
_EXEMPT_OPERATIONS = frozenset(
    {
        ("get", "/api/v1/meta/tenant"),
        ("get", "/api/v1/meta/modules"),
        ("get", "/api/v1/meta/me"),
        ("patch", "/api/v1/meta/me"),
        ("get", "/api/v1/prefs"),
        ("put", "/api/v1/prefs"),
        ("get", "/api/v1/members/lookup"),
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
