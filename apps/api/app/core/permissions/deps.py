"""``require_permission`` — the one authorization dependency (issue #19).

Deny-by-default: an ``/api/v1`` endpoint that declares neither a permission nor an explicit
exemption is a **build break**, not an open door. Two tests enforce that
(``tests/test_rbac_deny_by_default.py``): a fast introspection lint, and a behavioural sweep
that calls every operation as a member holding zero permissions and demands a ``403``.

The route declares the base key; the service refines it with ``:own`` / ``:any`` where the rule
is data-dependent (whose entry is it, is it approved, does the query name a project). Neither
layer alone is enough — a decorator cannot see the row, and a service check cannot be enumerated.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from fastapi import Depends
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from app.core.tenancy import RequestContext, require_context

#: Attribute names the introspection test looks for. Never read them by string elsewhere.
PERMISSION_MARKER = "__schakl_permission__"
EXEMPTION_MARKER = "__schakl_no_permission__"

#: The methods an operation can be. ``head``/``options``/``trace`` are answered by Starlette,
#: never declared by a route here, and would each be a phantom operation in the index below.
HTTP_METHODS = ("get", "post", "put", "patch", "delete")


def require_permission(permission: str, scope: str | None = None) -> Any:
    """A route dependency asserting the caller holds ``permission``.

    ``scope=None`` — the usual case — is the *floor*: it admits a holder at any scope, and the
    service then decides whether this particular row is theirs. Pass ``scope="any"`` only where
    the route itself is the manager surface.

    ``require_context`` is cached per request, so declaring this alongside the handler's own
    ``Depends(require_context)`` costs no extra query.
    """

    async def guard(ctx: RequestContext = Depends(require_context)) -> RequestContext:
        ctx.require(permission, scope)
        return ctx

    guard.__name__ = f"require_{permission}{':' + scope if scope else ''}"
    setattr(guard, PERMISSION_MARKER, (permission, scope))
    return Depends(guard)


def no_permission_required(reason: str) -> Any:
    """Mark a route that legitimately carries no permission, and say why.

    The only sanctioned reasons: it runs before a membership exists (auth, the first-run
    wizard), it is public tenant branding, it is the caller's own identity or preferences, or it
    is gated on a different axis entirely (``users.is_superuser`` for ``/instance``).
    """

    async def open_route() -> None:
        return None

    open_route.__name__ = "no_permission_required"
    setattr(open_route, EXEMPTION_MARKER, reason)
    return Depends(open_route)


def exempt_routes(router: Any, reason: str) -> None:
    """Mark every route of a router we did not write (fastapi-users) as deliberately open.

    Their route objects are built inside the library, so a router-level
    ``dependencies=[no_permission_required(...)]`` never reaches them: ``include_router`` is lazy
    here, and the leaves we introspect are the *originals*. Stamping the endpoint works because
    the endpoint is the root dependant's ``call``.
    """
    for route in iter_route_leaves(router.routes):
        setattr(route.endpoint, EXEMPTION_MARKER, reason)


# --------------------------------------------------------------------------- #
# Introspection — used by the deny-by-default tests
# --------------------------------------------------------------------------- #
def iter_route_leaves(routes: list[Any]) -> Iterator[APIRoute]:
    """Every real ``APIRoute``, however deeply ``include_router`` nested it.

    ``app.routes`` does **not** hold flattened routes on this FastAPI: it holds two health
    probes and one ``_IncludedRouter``, with all 150-odd real routes two wrapper levels down.
    A test that iterates ``app.routes`` looking for ``APIRoute``s therefore finds almost nothing
    and stays permanently green while enforcing nothing — the worst possible failure mode for a
    security guardrail. Hence this, and the anti-vacuum assertion that guards it.

    Leaf ``.path`` is **relative** (``/login``, not ``/api/v1/auth/login``): ancestor prefixes
    resolve at match time. Build full paths from ``app.openapi()``, never from here — which is
    what ``operation_index`` below does, once, so nobody has to do it again.
    """
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        elif hasattr(route, "original_router"):
            yield from iter_route_leaves(route.original_router.routes)
        elif hasattr(route, "routes"):
            yield from iter_route_leaves(route.routes)


@dataclass(frozen=True)
class Operation:
    """One operation in the OpenAPI document, joined to the route that serves it."""

    operation_id: str
    method: str
    #: The **full** path, as a caller would request it (``/api/v1/auth/login``).
    path: str
    route: APIRoute


def _slug(name: str) -> str:
    """FastAPI sanitises the whole operationId, so a route named ``auth:cookie.login`` appears
    as ``auth_cookie_login…``. Comparing the raw name misses every fastapi-users route."""
    return re.sub(r"\W", "_", name)


def operation_index(application: Any) -> tuple[list[Operation], list[str]]:
    """``(operations, unclaimed operationIds)`` — the route table joined to the document.

    Both halves are needed by anything reasoning about routes *and* the URLs that reach them:
    the published API reference (``app/openapi_docs_export.py``) and the authentication sweep
    (``tests/test_anonymous_denied.py``). It lives here rather than inside either of them
    because a second copy of this join is a copy that will drop routes quietly.

    **Why it is fiddly.** ``route.unique_id`` looks like the join key and is not: ``include_router``
    is lazy on this app, so a leaf's ``path`` is relative and its ``unique_id`` is built from the
    relative path, while the document's ``operationId`` is built from the full one. Two conditions
    settle it and **both** are needed. FastAPI's own formula (route name + full path, sanitised,
    plus the method) is self-fulfilling on its own — all four routers with a ``create_account``
    handler satisfy it for their own path, so it matches four operations and the route is dropped.
    The leaf's relative path is the missing half: ``/cloudflare/accounts`` is a suffix of exactly
    one of those four. Where the leaf path is itself a suffix of a sibling's (``/prefs`` under
    ``/nav/prefs`` and ``/dashboard/prefs``, all three named ``get_prefs``), the shortest full
    path is the one whose own router added no further prefix.

    ``unclaimed`` is the honesty check, and a caller that ignores it silently reports on a
    subset of the API: an operation nothing matched means the join needs widening.
    """
    spec = application.openapi()
    operations: dict[str, tuple[str, str]] = {
        op["operationId"]: (method, path)
        for path, ops in spec["paths"].items()
        for method, op in ops.items()
        if method in HTTP_METHODS
    }

    matched: list[Operation] = []
    claimed: set[str] = set()
    for route in iter_route_leaves(application.routes):
        if not route.include_in_schema:
            continue
        for method in sorted(m.lower() for m in (route.methods or ())):
            if method not in HTTP_METHODS:
                continue
            candidates = [
                (len(path), op_id)
                for op_id, (m, path) in operations.items()
                if m == method
                and path.endswith(route.path)
                and op_id == f"{_slug(route.name + path)}_{method}"
            ]
            if len(candidates) > 1:
                shortest = min(length for length, _ in candidates)
                candidates = [c for c in candidates if c[0] == shortest]
            if len(candidates) != 1:
                continue
            op_id = candidates[0][1]
            claimed.add(op_id)
            matched.append(Operation(op_id, method, operations[op_id][1], route))

    return matched, sorted(set(operations) - claimed)


def _walk(dependant: Dependant) -> Iterator[Dependant]:
    yield dependant
    for sub in dependant.dependencies:
        yield from _walk(sub)


def route_markers(route: APIRoute) -> tuple[list[tuple[str, str | None]], list[str]]:
    """``(permissions, exemption reasons)`` declared anywhere in this route's dependant tree."""
    permissions: list[tuple[str, str | None]] = []
    exemptions: list[str] = []
    for dependant in _walk(route.dependant):
        call = dependant.call
        if call is None:
            continue
        declared = getattr(call, PERMISSION_MARKER, None)
        if declared is not None:
            permissions.append(declared)
        reason = getattr(call, EXEMPTION_MARKER, None)
        if reason is not None:
            exemptions.append(reason)
    return permissions, exemptions
