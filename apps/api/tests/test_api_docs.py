"""The interactive API documentation: reachable where the edge routes, and only to a member.

Two bugs, one file, and they are the same shape at two different layers.

**Where.** FastAPI's defaults put Swagger UI, ReDoc and the OpenAPI document at ``/docs``,
``/redoc`` and ``/openapi.json`` — all at the root. Every deployment routes exactly two prefixes
to this service, ``/api/`` and ``/mcp`` (infra/traefik/dynamic*.yml), and sends everything else
to the SSR web app, which has no ``/docs`` route. So the docs were not disabled anywhere; they
were unroutable, and the symptom was a web-app 404 where the API reference should have been.

**Who.** Once reachable, they were reachable by *anyone*: a route FastAPI builds for its own
docs carries no dependency and cannot be given one, so a stranger who could resolve the
hostname collected the full route table, every request and response schema, and the tenant's
enabled module set. Not an authorization hole — every operation behind those URLs still travels
``require_context`` — but a map of the surface, handed out for free. ``app/core/apidocs.py``
serves the three paths now, behind the gate the API itself uses.

These tests pin the paths *and* the gate, because both bugs were invisible from inside: a suite
that asserted ``app.openapi()`` returns a document passed the whole time the docs were
unreachable, and one that fetched them without credentials passed the whole time they were open.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI

from app.core.permissions.deps import iter_route_leaves
from app.main import app, create_app
from tests.conftest import auth_cookie, make_tenant

_DOCS_PATHS = frozenset({"/api/docs", "/api/redoc", "/api/openapi.json"})


def _served_paths(application: FastAPI) -> set[str]:
    return {route.path for route in iter_route_leaves(application.routes)}


async def test_docs_are_served_under_the_routed_api_prefix(client_for) -> None:
    tenant = await make_tenant("api-docs", role="owner")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as c:
        spec = await c.get("/api/openapi.json", headers=headers)
        assert spec.status_code == 200, spec.text
        assert spec.json()["info"]["title"] == "schakl API"
        # A response that exists only because a credential was presented must not be held by
        # anything between us and the reader.
        assert spec.headers["cache-control"] == "no-store"

        swagger = await c.get("/api/docs", headers=headers)
        assert swagger.status_code == 200
        # Swagger UI points the browser at the same relocated document; a page rendered
        # against the default root URL would load an empty spec at the edge.
        assert "/api/openapi.json" in swagger.text

        redoc = await c.get("/api/redoc", headers=headers)
        assert redoc.status_code == 200
        assert "/api/openapi.json" in redoc.text


@pytest.mark.parametrize("path", ["/api/openapi.json", "/api/docs", "/api/redoc"])
async def test_the_reference_is_not_public(client_for, path: str) -> None:
    """The whole point. Before ``core/apidocs.py`` all three answered 200 to no credential at
    all — 583 paths and 817 schemas of it — on every instance ever shipped."""
    tenant = await make_tenant("api-docs-anon", role="owner")
    async with client_for(tenant.host) as c:
        assert (await c.get(path)).status_code == 401


@pytest.mark.parametrize("path", ["/api/openapi.json", "/api/docs", "/api/redoc"])
async def test_a_client_login_is_refused(client_for, path: str) -> None:
    """Externality is its own axis (CLAUDE.md §15, #274). A client signing in to look at their
    own invoices holds a real session and a real membership, so ``require_context`` is happy;
    the agency's internal route table is still none of their business."""
    tenant = await make_tenant("api-docs-portal", role="client")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as c:
        assert (await c.get(path, headers=headers)).status_code == 403


@pytest.mark.parametrize("path", ["/openapi.json", "/docs", "/redoc"])
async def test_the_unroutable_root_paths_are_not_used(client_for, path: str) -> None:
    """The root paths stay empty on purpose: the edge never forwards them here, so a route
    living there is a route nobody can call — and one that silently disagrees with the URL
    the docs page and scripts/gen-client.sh advertise."""
    async with client_for("api.localhost") as c:
        assert (await c.get(path)).status_code == 404


def test_disabling_the_docs_keeps_the_in_process_spec(monkeypatch) -> None:
    """SCHAKL_API_DOCS_ENABLED=false removes the HTTP surface only.

    ``app.openapi()`` builds the document from the route table and never reads ``openapi_url``,
    which is what lets the MCP tool builder and the typed-client generator keep working on an
    instance that serves no docs at all."""
    from app.config import settings

    monkeypatch.setattr(settings, "api_docs_enabled", False)
    bare = create_app()

    assert bare.openapi()["paths"], "the spec must still generate in-process"
    # ``include_router`` is lazy here, so the real routes sit two wrapper levels below
    # ``app.routes`` — reading that list directly would find nothing and pass either way
    # (tests/test_rbac_deny_by_default.py makes the same point about its own sweep).
    assert not _DOCS_PATHS & _served_paths(bare)

    # The module-level app was built with the docs on and must be unaffected.
    assert _DOCS_PATHS <= _served_paths(app)


def test_fastapis_own_docs_stay_off() -> None:
    """A route FastAPI builds for its own docs carries no dependency and cannot be given one,
    so the only way to gate the reference is to not let it build them. Setting ``openapi_url``
    back — even to the right path — silently republishes the document to the internet."""
    assert app.openapi_url is None
    assert app.docs_url is None
    assert app.redoc_url is None
