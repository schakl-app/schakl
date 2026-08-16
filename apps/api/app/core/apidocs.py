"""The interactive API reference, served behind the session it describes.

FastAPI's own ``docs_url`` / ``redoc_url`` / ``openapi_url`` are plain routes with no
dependency, so they carry no authentication and cannot be given one. On every instance that
meant Swagger UI, ReDoc and a 1.2 MB OpenAPI document — 583 paths, 817 schemas, and the
tenant's enabled module set — were readable by anyone who could reach the hostname.

That was never an authorization hole: every operation behind those URLs still travels
``require_context`` (tenant → RLS → permission), and Swagger UI's "Try it out" is a browser
making the same call ``curl`` would, so an anonymous visitor collected 401s. What it leaked
was the *map*: which integrations this agency runs, which modules it licences, the shape of
every request body worth attacking, and the exact spelling of every field. A reference is for
the people holding a key, not for whoever finds the host.

So the three paths are ours, and they carry the same gate as the API they describe:

* **A session or an API key for this org.** ``require_context`` answers 401 with neither and
  403 to a valid session with no membership here, which is the same sentence the rest of the
  API already speaks. The browser reaches it on the cookie it already has (same origin, so
  Swagger UI's own fetch of the document is authenticated too); a script reaches it with the
  key it is being written for.
* **Never a client-portal login** (CLAUDE.md §15, #274). Externality is its own axis: a client
  signing in to see their invoices is not the audience for the agency's internal route table,
  and ``is_portal`` is the one fact that says so.
* **Out of the schema.** The reference is not product API — it must not become an MCP tool or
  a method on the generated client, for the same reason the edge's error page is not one.
* **``no-store``.** A response that exists only because a credential was presented must not be
  held by anything between us and the reader.

``SCHAKL_API_DOCS_ENABLED=false`` still removes the HTTP surface entirely, and still leaves
``app.openapi()`` alone: the document is built from the route table in process, which is what
keeps the MCP tool builder and ``scripts/gen-client.sh`` working on an instance that serves no
docs at all.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse

from app.core.permissions.deps import no_permission_required
from app.core.tenancy import RequestContext, require_context
from app.errors import AppError

#: Where the document lives. The edge routes exactly ``/api/`` and ``/mcp`` to this service
#: (infra/traefik/dynamic*.yml), so FastAPI's root-level defaults were never reachable in any
#: deployment — the paths stay where they were made reachable, only the gate is new.
OPENAPI_PATH = "/api/openapi.json"
DOCS_PATH = "/api/docs"
REDOC_PATH = "/api/redoc"

#: A reader that has proved it belongs here. Kept separate from the route bodies so the three
#: entry points cannot drift apart on who may read them.
_READER_REASON = (
    "the API reference: a signed-in member of this org (or an API key), never a portal login"
)


async def _reader(ctx: RequestContext = Depends(require_context)) -> RequestContext:
    if ctx.is_portal:
        raise AppError("forbidden", "errors.forbidden", status_code=403)
    return ctx


def _no_store(response: Any) -> Any:
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


def build_docs_router(title: str) -> APIRouter:
    """The three reference routes, or nothing at all when the docs are switched off."""
    router = APIRouter(
        include_in_schema=False,
        dependencies=[no_permission_required(_READER_REASON)],
    )

    @router.get(OPENAPI_PATH)
    async def openapi_document(
        request: Request, _: RequestContext = Depends(_reader)
    ) -> JSONResponse:
        return _no_store(JSONResponse(request.app.openapi()))

    @router.get(DOCS_PATH)
    async def swagger_ui(_: RequestContext = Depends(_reader)) -> HTMLResponse:
        # ``oauth2_redirect_url=None``: the reference authenticates with the session that
        # opened it or with an API key pasted into Authorize, never with a second OAuth dance.
        return _no_store(
            get_swagger_ui_html(
                openapi_url=OPENAPI_PATH,
                title=f"{title} — Swagger UI",
                oauth2_redirect_url=None,
            )
        )

    @router.get(REDOC_PATH)
    async def redoc(_: RequestContext = Depends(_reader)) -> HTMLResponse:
        return _no_store(
            get_redoc_html(openapi_url=OPENAPI_PATH, title=f"{title} — ReDoc")
        )

    return router
