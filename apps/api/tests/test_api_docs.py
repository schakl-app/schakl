"""The interactive API documentation is served where the edge can actually reach it.

FastAPI's defaults put Swagger UI, ReDoc and the OpenAPI document at ``/docs``, ``/redoc``
and ``/openapi.json`` — all at the root. Every deployment routes exactly two prefixes to this
service, ``/api/`` and ``/mcp`` (infra/traefik/dynamic*.yml), and sends everything else to the
SSR web app, which has no ``/docs`` route. So the docs were not disabled anywhere; they were
unroutable, and the symptom was a web-app 404 where the API reference should have been.

These tests pin the paths rather than the fact that *some* docs exist, because the bug is
entirely about which path is used: a suite that asserted ``app.openapi()`` returns a document
passed the whole time the docs were unreachable.
"""

from __future__ import annotations

import pytest

from app.main import app, create_app


async def test_docs_are_served_under_the_routed_api_prefix(client_for) -> None:
    async with client_for("api.localhost") as c:
        spec = await c.get("/api/openapi.json")
        assert spec.status_code == 200, spec.text
        assert spec.json()["info"]["title"] == "schakl API"

        swagger = await c.get("/api/docs")
        assert swagger.status_code == 200
        # Swagger UI points the browser at the same relocated document; a page rendered
        # against the default root URL would load an empty spec at the edge.
        assert "/api/openapi.json" in swagger.text

        redoc = await c.get("/api/redoc")
        assert redoc.status_code == 200


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

    assert bare.openapi_url is None
    assert bare.openapi()["paths"], "the spec must still generate in-process"
    assert not [r for r in bare.routes if getattr(r, "path", None) in {"/api/docs", "/api/redoc"}]

    # The module-level app was built with the docs on and must be unaffected.
    assert app.openapi_url == "/api/openapi.json"
