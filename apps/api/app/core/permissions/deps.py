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

from collections.abc import Iterator
from typing import Any

from fastapi import Depends
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from app.core.tenancy import RequestContext, require_context

#: Attribute names the introspection test looks for. Never read them by string elsewhere.
PERMISSION_MARKER = "__schakl_permission__"
EXEMPTION_MARKER = "__schakl_no_permission__"


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
    resolve at match time. Build full paths from ``app.openapi()``, never from here.
    """
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        elif hasattr(route, "original_router"):
            yield from iter_route_leaves(route.original_router.routes)
        elif hasattr(route, "routes"):
            yield from iter_route_leaves(route.routes)


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
