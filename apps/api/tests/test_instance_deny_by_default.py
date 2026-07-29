"""Deny-by-default for the **instance** surface (issue #26, epic #199).

``test_rbac_deny_by_default.py`` deliberately exempts ``/api/v1/instance``: those routes
answer **404** when the surface is switched off, so they can never satisfy an "everything
403s" assertion built for tenant routes. That exemption is correct, and it left the one
surface that can list, export, impersonate and purge *any org on the box* with no enumerable
guard at all — the check existed for every tenant route and not for the dangerous one. This
is its sibling.

Three layers, in the order they must be trusted, mirroring the tenant sweep:

1. **Anti-vacuum.** Two independent selectors (route tag, and OpenAPI path prefix) find the
   same set. A guardrail that silently selects nothing is worse than no guardrail.
2. **Introspection.** Every instance route carries ``require_instance_admin`` — or
   ``require_provisioning_key``, which is the instance API key's own gate — or sits on a
   short, reasoned exception list.
3. **Behaviour.** Called by an authenticated **non-superuser** with the surface fully
   enabled, every instance operation refuses. Introspection can go vacuous across a
   refactor; a 200 cannot.

The surface is sweept in **cloud posture**, because the cloud additions
(``app/core/cloud/router.py``) answer 404 on self-host via ``require_cloud`` and would
otherwise pass this vacuously.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.routing import APIRoute

from app.config import settings
from app.core.cloud.deps import require_cloud
from app.core.cloud.provisioning import require_provisioning_key
from app.core.entitlements.router import require_instance_owner
from app.core.instance.capabilities import CAPABILITY_KEYS
from app.core.instance.guard import (
    CAPABILITY_EXEMPTION_MARKER,
    CAPABILITY_MARKER,
    require_instance_admin,
    require_instance_owner_principal,
)
from app.core.permissions.deps import iter_route_leaves
from app.main import app
from tests.conftest import auth_cookie, make_tenant

_INSTANCE_PREFIX = "/api/v1/instance"
_PROVISIONING_PREFIX = "/api/v1/instance/provisioning"
_LICENSE_PREFIX = "/api/v1/instance/license"
_HTTP_METHODS = ("get", "post", "put", "patch", "delete")

#: The gates that count as "this route is instance-guarded". Three, not one, and each means
#: something different:
#:   require_instance_admin    — superuser AND the surface switched on (404 when off)
#:   require_instance_owner    — superuser only; /instance/license is deliberately NOT behind
#:                               the flag, because installing a license must work on every
#:                               self-hosted box while the cross-tenant surface stays opt-in
#:   require_provisioning_key  — an instance API key, not a session at all
_GUARDS = frozenset(
    {require_instance_admin, require_instance_owner, require_provisioning_key}
)

#: Operations that legitimately answer without being an instance owner. Each needs a reason,
#: and the reason has to be about *what the route returns*, not about convenience.
_EXEMPT_OPERATIONS: dict[tuple[str, str], str] = {
    ("get", f"{_INSTANCE_PREFIX}/me"): (
        "returns only the caller's OWN id/email/flags, so the console can decide whether to "
        "render the login screen — it cannot report someone else's, and holds no tenant data. "
        "Still gated on require_cloud (404 on self-host)."
    ),
}


def _walk(dependant) -> Iterator:  # noqa: ANN001 — fastapi.dependencies.models.Dependant
    yield dependant
    for sub in dependant.dependencies:
        yield from _walk(sub)


def _route_callables(route: APIRoute) -> set:
    return {d.call for d in _walk(route.dependant) if d.call is not None}


def _instance_leaves() -> list[APIRoute]:
    """Selector A: by router tag. Both instance routers declare ``tags=["instance"]``."""
    return [r for r in iter_route_leaves(app.routes) if "instance" in r.tags]


def _provisioning_leaves() -> list[APIRoute]:
    return [r for r in iter_route_leaves(app.routes) if "provisioning" in r.tags]


def _instance_operations() -> list[tuple[str, str]]:
    """Selector B: by OpenAPI path. Cloud routes are in the spec in either posture."""
    return [
        (method, path)
        for path, operations in app.openapi()["paths"].items()
        if path.startswith(_INSTANCE_PREFIX)
        for method in operations
        if method in _HTTP_METHODS
    ]


def _url(path: str) -> str:
    """Fill path parameters. Dependencies are solved before the handler runs, so any
    syntactically valid value does — the request never reaches the body."""
    filled = path
    while "{" in filled:
        start = filled.index("{")
        end = filled.index("}", start)
        filled = filled[:start] + str(uuid.uuid4()) + filled[end + 1 :]
    return filled


@pytest.fixture
def cloud_surface(monkeypatch) -> None:
    """Cloud posture with the surface on — every instance route live and reachable."""
    monkeypatch.setattr(settings, "deployment", "cloud")
    monkeypatch.setattr(settings, "instance_admin_enabled", True)


# --------------------------------------------------------------------------- #
# 1. Anti-vacuum
# --------------------------------------------------------------------------- #
def test_the_sweep_actually_finds_the_instance_surface() -> None:
    """If either selector silently returns nothing, everything below is permanently green."""
    tagged = _instance_leaves() + _provisioning_leaves()
    operations = _instance_operations()
    assert tagged, "no routes carry the instance/provisioning tag — the selector broke"
    assert operations, "no /api/v1/instance operations in the OpenAPI document"
    # Two independent selectors, so a route added under the prefix without the tag (or the
    # reverse) surfaces here rather than quietly escaping one of the two layers below.
    assert len(tagged) == len(operations), (
        f"tag-selected ({len(tagged)}) and path-selected ({len(operations)}) instance routes "
        "disagree — one layer below is not seeing the whole surface"
    )


# --------------------------------------------------------------------------- #
# 2. Introspection
# --------------------------------------------------------------------------- #
def test_every_instance_route_declares_a_guard() -> None:
    paths = {path for _method, path in _instance_operations()}
    exempt_names = {
        # Map the exception list onto route names once, so a rename fails loudly here rather
        # than silently widening the exemption.
        "instance_me"
    }
    del paths  # selector B is asserted in the anti-vacuum test; this layer walks the routes
    offenders: list[str] = []
    for route in _instance_leaves() + _provisioning_leaves():
        if route.name in exempt_names:
            continue
        if not _route_callables(route) & _GUARDS:
            offenders.append(f"{sorted(route.methods)} {route.name}")
    assert not offenders, (
        "these instance routes carry none of the three instance guards — the cross-tenant "
        "surface is deny-by-default too (CLAUDE.md §5, issue #26):\n  " + "\n  ".join(offenders)
    )


def test_the_exemption_list_is_short_and_reasoned() -> None:
    """An exemption is a claim about what the route returns. Keep it that way."""
    assert len(_EXEMPT_OPERATIONS) <= 2, (
        "the instance exemption list is growing; each entry is a route that answers without "
        "being an instance owner, on the surface that can read every org on the box"
    )
    for (method, path), reason in _EXEMPT_OPERATIONS.items():
        assert method in _HTTP_METHODS and path.startswith(_INSTANCE_PREFIX)
        assert len(reason) > 40, f"{method} {path}: an exemption needs a real reason"


def test_every_instance_route_declares_a_capability() -> None:
    """The fourth layer (issue #26 — delegated access).

    Reaching the surface is no longer the whole answer: a delegated admin holds only what was
    granted, so each route must say *which* capability it needs. A route that declares neither
    a capability nor an explicit exemption would be reachable by any admin holding anything at
    all — the exact deny-by-default hole §15 closed for tenant routes.
    """
    offenders: list[str] = []
    for route in _instance_leaves():
        calls = _route_callables(route)
        declared = any(hasattr(c, CAPABILITY_MARKER) for c in calls)
        exempt = any(hasattr(c, CAPABILITY_EXEMPTION_MARKER) for c in calls)
        if not declared and not exempt:
            offenders.append(f"{sorted(route.methods)} {route.name}")
    assert not offenders, (
        "these instance routes declare neither a capability nor an exemption — a delegated "
        "admin holding any capability at all would reach them (issue #26):\n  "
        + "\n  ".join(offenders)
    )


def test_declared_capabilities_exist_in_the_catalog() -> None:
    for route in _instance_leaves():
        for call in _route_callables(route):
            declared = getattr(call, CAPABILITY_MARKER, None)
            if declared is not None:
                assert declared in CAPABILITY_KEYS, (
                    f"{route.name} declares unknown capability {declared!r}"
                )


#: Exempt *and* not owner-gated: the only routes on the cross-tenant surface that any admin
#: can reach whatever they hold. Both report or act on the caller's own session and nothing
#: else. This list is the thing to argue about in review; everything else that is exempt is so
#: because a **stricter** gate (owner-only) applies, which is not a hole.
_UNGATED_BY_DESIGN = frozenset({"instance_me", "stop_impersonation"})


def test_capability_exemptions_are_owner_gated_or_explicitly_harmless() -> None:
    """An exemption is only safe for one of two reasons, and they are not interchangeable.

    Either a *stricter* gate applies — the route demands ``users.is_superuser``, so no
    delegated admin reaches it at all — or the route genuinely needs no gate because it acts
    only on the caller's own session. Counting exemptions would not distinguish those; this
    does, and it keeps the second, riskier category down to a named list.
    """
    owner_gates = {require_instance_owner, require_instance_owner_principal}
    ungated: list[str] = []
    for route in _instance_leaves():
        calls = _route_callables(route)
        reasons = [
            r for r in (getattr(c, CAPABILITY_EXEMPTION_MARKER, None) for c in calls) if r
        ]
        if not reasons:
            continue
        for reason in reasons:
            assert len(reason) > 40, (
                f"{route.name}: an exemption needs a real reason, not a label"
            )
        if not calls & owner_gates:
            ungated.append(route.name)

    assert set(ungated) <= _UNGATED_BY_DESIGN, (
        "these instance routes are exempt from a capability AND not owner-gated, so any "
        "delegated admin reaches them whatever they hold:\n  "
        + "\n  ".join(sorted(set(ungated) - _UNGATED_BY_DESIGN))
    )


def test_cloud_routes_are_posture_gated() -> None:
    """The cloud additions must also carry require_cloud, or a self-hosted box would expose
    the provisioning/service-PIN surface it has no use for."""
    from app.core.cloud.router import instance_router

    for route in iter_route_leaves(instance_router.routes):
        assert require_cloud in _route_callables(route), (
            f"cloud instance route {route.name} is missing require_cloud"
        )


# --------------------------------------------------------------------------- #
# 3. Behaviour — the layer that cannot go vacuous
# --------------------------------------------------------------------------- #
async def test_a_signed_in_non_superuser_is_refused_everywhere(
    client_for, cloud_surface
) -> None:
    """An ordinary org **owner** — the most privileged principal inside a tenant — still holds
    nothing on the instance surface. That is the whole point of ``is_superuser`` being a
    separate axis from the role system (CLAUDE.md §5, §15)."""
    tenant = await make_tenant("inst-deny")  # org owner, NOT an instance owner
    headers = await auth_cookie(tenant.user)

    reached: list[str] = []
    async with client_for(tenant.host) as client:
        for method, path in _instance_operations():
            if (method, path) in _EXEMPT_OPERATIONS:
                continue
            response = await client.request(
                method.upper(), _url(path), headers=headers, json={}
            )
            # Provisioning authenticates with an instance API key, not a session, so a
            # cookie-only caller is unauthenticated there rather than forbidden. Both are
            # refusals; pinning which one keeps a route from quietly changing auth model.
            expected = 401 if path.startswith(_PROVISIONING_PREFIX) else 403
            if response.status_code != expected:
                reached.append(
                    f"{method.upper()} {path} -> {response.status_code} (expected {expected})"
                )

    assert not reached, (
        "a signed-in non-superuser reached the cross-tenant instance surface:\n  "
        + "\n  ".join(reached)
    )


async def test_the_exempt_route_really_is_harmless(client_for, cloud_surface) -> None:
    """Non-vacuous companion to the exemption list: /instance/me answers, and what it answers
    is the caller's own record with both instance flags false."""
    tenant = await make_tenant("inst-me")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        response = await client.get(f"{_INSTANCE_PREFIX}/me", headers=headers)
        assert response.status_code == 200
        body = response.json()
        assert body["email"] == tenant.user.email
        assert body["is_instance_admin"] is False
        assert body["is_instance_owner"] is False


async def test_anonymous_callers_never_reach_the_surface(client_for, cloud_surface) -> None:
    """No session at all: every instance operation refuses before any handler runs."""
    tenant = await make_tenant("inst-anon")
    reached: list[str] = []
    async with client_for(tenant.host) as client:
        for method, path in _instance_operations():
            response = await client.request(method.upper(), _url(path), json={})
            if response.status_code not in (401, 403):
                reached.append(f"{method.upper()} {path} -> {response.status_code}")
    assert not reached, (
        "an anonymous caller was not refused on the instance surface:\n  " + "\n  ".join(reached)
    )


async def test_the_surface_does_not_advertise_itself_when_disabled(
    client_for, monkeypatch
) -> None:
    """Self-host with the flag off: **404**, not 403. A single-tenant box should not reveal
    that a cross-tenant surface exists at all (issue #26).

    ``/instance/license`` is the one documented exception and answers 403: installing a
    license key has to work on every self-hosted box, so it is gated on ``is_superuser``
    alone and never on ``SCHAKL_INSTANCE_ADMIN_ENABLED``. It is asserted here rather than
    skipped, so it cannot drift into 200 unnoticed.
    """
    monkeypatch.setattr(settings, "deployment", "self_hosted")
    monkeypatch.setattr(settings, "instance_admin_enabled", False)
    admin = await make_tenant("inst-off")
    headers = await auth_cookie(admin.user)

    leaked: list[str] = []
    async with client_for(admin.host) as client:
        for method, path in _instance_operations():
            if path.startswith(_PROVISIONING_PREFIX) or (method, path) in _EXEMPT_OPERATIONS:
                continue  # key-authenticated / posture-gated: 401 and 404 respectively
            # The license surface is deliberately not flag-gated; everything else is.
            expected = 403 if path.startswith(_LICENSE_PREFIX) else 404
            response = await client.request(
                method.upper(), _url(path), headers=headers, json={}
            )
            if response.status_code != expected:
                leaked.append(
                    f"{method.upper()} {path} -> {response.status_code} (expected {expected})"
                )

    assert not leaked, (
        "the disabled instance surface answered the wrong thing — it must 404 (not even "
        "advertise itself), except the license routes which refuse with 403:\n  "
        + "\n  ".join(leaked)
    )
