"""MCP server over Streamable HTTP at ``/mcp`` (CLAUDE.md §12).

Every ``/api/v1`` operation becomes an MCP tool, generated from the app's own OpenAPI spec
(FastMCP's OpenAPI integration) and proxied **in-process** back to the REST API. No second
data path exists: a tool call travels through ``require_context`` — hostname → org, RLS
bound, permissions resolved — exactly like the HTTP request it is.

**Auth: API keys, not (yet) OAuth.** The platform's keys (#20) already carry per-key
permission scopes, are tenant-scoped, revocable and optionally non-expiring — precisely the
per-MCP-key permission model wanted here. A client configures
``Authorization: Bearer schakl_…`` (or ``X-API-Key``) on the connection; the proxy forwards
the credential plus the tenant host on every internal call, so deny-by-default route
permissions and the key's scopes govern MCP exactly as they govern HTTP, and an unauthorized
tool call reads as the API's own 401/403 envelope. An OAuth 2.1 resource-server layer
(RFC 9728) can be added later for clients that require it, without touching the tool surface.

Excluded from the tool surface: the session flows (``/auth``, ``/setup``) and the
instance-operator surface (``/instance``) — none make sense for a headless key.

**Two profiles, one server.** ``/mcp`` is the whole surface: every operation, ~620 tools,
about two megabytes of ``tools/list``. That is the right answer for a coding agent, which
reads the list once and tolerates it. It is the wrong answer for a chat client that loads
every tool into the model's context on every turn, and ChatGPT refuses it outright — its
documented ceiling is **5,000 tokens for all tools together**, name, description and input
schema included, which the full surface passes by two orders of magnitude. ``/mcp/compact``
serves the same server through :class:`CompactProfile`: a hand-picked read-only set that fits
inside that budget. Nothing about the full surface changes, so an existing client keeps the
tools it already had.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Generator
from typing import Any

import httpx
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers, get_http_request
from fastmcp.server.middleware import Middleware as MCPMiddleware
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.server.openapi import MCPType, RouteMap
from starlette.middleware import Middleware

#: Credential headers copied from the incoming MCP request onto the proxied API call.
_FORWARDED_HEADERS = ("authorization", "x-api-key")


class ForwardCallerAuth(httpx.Auth):
    """Per-request header forwarding: the MCP caller's credentials and tenant host.

    The backing client is a static ASGI transport, but the credential is per *caller* —
    ``get_http_headers()`` reads the current MCP HTTP request from a contextvar, so
    concurrent callers never see each other's keys. The host rides along as
    ``X-Forwarded-Host`` because that is what ``resolve_org`` prefers (the raw ``Host`` of
    an in-process call is the fake base_url).
    """

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, Any, None]:
        headers = get_http_headers(include_all=True)
        for name in _FORWARDED_HEADERS:
            value = headers.get(name)
            if value:
                request.headers[name] = value
        host = headers.get("x-forwarded-host") or headers.get("host")
        if host:
            request.headers["x-forwarded-host"] = host
        yield request


class RefuseStandaloneStream:
    """Answer ``GET /mcp`` at once instead of opening a stream nothing can ever write to.

    Streamable HTTP lets a client open a standalone ``GET`` stream for messages the *server*
    initiates. That stream only means something while the server holds a session; this one is
    **stateless** by choice (see :func:`build_mcp_asgi_app`), so no message will ever be routed
    to it and it will never end on its own. The SDK does not join those two facts: it refuses
    ``DELETE`` with 405 the moment it sees no session id, and then opens the ``GET`` stream
    anyway — one held connection, one task group and two memory streams per probe, for as long
    as the caller or the edge is willing to wait.

    Clients probe with ``GET``. An MCP client that hangs there reports a timeout, not a refusal,
    which is why the failure reads as "the server is broken" rather than "that verb is not
    offered here" — so say the second thing, with the ``Allow`` header that names what is.

    Written against the *transport*, not against a client: any caller asking for a stream this
    server cannot keep gets the same honest answer.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope["type"] == "http" and scope["method"] == "GET":
            body = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "server-error",
                    "error": {
                        "code": -32600,
                        "message": (
                            "Method Not Allowed: this server is stateless, so it offers no "
                            "standalone SSE stream. Send JSON-RPC over POST."
                        ),
                    },
                }
            ).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 405,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"allow", b"POST"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)


#: The path segment under the mount that selects the compact profile: ``/mcp/compact``.
_COMPACT_SEGMENT = "/compact"
#: Scope key :class:`SelectProfile` stamps and :class:`CompactProfile` reads.
_PROFILE_KEY = "schakl_mcp_profile"

#: The compact profile's tools, by the name ``_tool_names`` gives them.
#:
#: Chosen against one question — *what does somebody ask a chat assistant about an agency?* —
#: and then cut until the whole list fits ChatGPT's 5,000-token ceiling with room to spare
#: (``test_mcp_compact_profile_fits_a_chat_client``, which is the real specification here; a
#: name added below without watching that number is how this profile stops working).
#:
#: **Read-only, and that is a decision rather than an accident.** §12 already calls the surface
#: read-first; a chat client is where that matters most, because the tools a model may reach
#: for are the ones nobody explicitly asked it to call. The full surface at ``/mcp`` keeps
#: every write, gated by the calling key's scopes exactly as before.
#:
#: Picked for grounding first (a person says "AAZET", every other tool wants an id), then the
#: four questions an agency actually asks: what is running, what is owed, where did the hours
#: go, how are the campaigns doing.
_COMPACT_TOOLS = frozenset(
    {
        # Grounding: name → id, for everything below.
        "list_companies",
        "get_company",
        "list_contacts",
        # What is running.
        "list_projects",
        "my_open_tasks",
        "get_task",
        # What is owed.
        "list_invoices",
        "outstanding",
        "unbilled",
        # Where the hours went.
        "time_report",
        # What we host and renew.
        "list_domains",
        # How the campaigns are doing. ``list_google_ads_accounts`` is grounding again: the
        # rest take an ``account_id`` nobody types from memory.
        "list_google_ads_accounts",
        "google_ads_snapshot",
        "google_ads_search_terms",
    }
)


class SelectProfile:
    """Route ``/mcp/compact`` to the same MCP server, with the profile stamped on the scope.

    A path segment rather than a query parameter, because this URL is pasted into somebody
    else's settings screen and a query string is the part of a URL that tools normalise, strip
    and re-encode. The rewrite is the whole mechanism: one server, one session manager, one
    lifespan — the profile changes only what ``tools/list`` answers, which is where the entire
    problem lives.

    Mounted apps see the **whole** path with the mount prefix in ``root_path``, not the
    remainder — so the segment is read relative to ``root_path`` and the rewrite puts it back,
    which is also what keeps this correct if ``/mcp`` ever moves.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope["type"] == "http":
            root = scope.get("root_path", "")
            path = scope.get("path", "")
            relative = path[len(root) :] if root and path.startswith(root) else path
            if relative.rstrip("/") == _COMPACT_SEGMENT:
                scope = {**scope, "path": f"{root}/", _PROFILE_KEY: "compact"}
        await self.app(scope, receive, send)


class CompactProfile(MCPMiddleware):
    """Answer ``tools/list`` with the curated set when the caller asked for the profile.

    Two reductions, and the second one is not decoration. Filtering to :data:`_COMPACT_TOOLS`
    is the obvious half. Dropping ``output_schema`` is the half that makes the budget: it is
    **79% of the full surface's bytes** — response shapes, which a client needs to *validate*
    a result it already has, never to decide whether to call. Six single tools in the full
    surface each exceed ChatGPT's entire allowance on their own, and every one of them is a
    response schema wearing a tool's name.

    Only the listing is narrowed. A call to a tool outside the profile still works and is
    still governed by the key's scopes — hiding a tool is a context-budget decision, and
    dressing it up as an authorization boundary would put a second, weaker answer next to the
    one ``require_context`` already gives.
    """

    async def on_list_tools(self, context: MiddlewareContext, call_next):  # noqa: ANN001, ANN201
        tools = await call_next(context)
        try:
            request = get_http_request()
        except RuntimeError:  # no HTTP request in scope (in-process, tests)
            return tools
        if request.scope.get(_PROFILE_KEY) != "compact":
            return tools
        return [
            tool.model_copy(update={"output_schema": None})
            for tool in tools
            if tool.name in _COMPACT_TOOLS
        ]


#: First match wins. GETs become tools too — an agent calls, it doesn't browse resources.
#: ``/users`` is fastapi-users' cookie-authenticated self-service — dead weight for a key.
_ROUTE_MAPS = [
    RouteMap(pattern=r"^/api/v1/(auth|setup|instance|users)(/.*)?$", mcp_type=MCPType.EXCLUDE),
    # Anything a route authenticates *itself* — with a token in its own URL rather than with the
    # caller's session — is excluded for the same reason ``/auth`` is: the proxy's whole safety
    # argument is that every tool call travels ``require_context`` and can therefore never
    # exceed the key's scopes, and a route that resolves its own tenant does not travel it. The
    # public invoice link (#304) is the first of these; a second one belongs in this pattern
    # rather than in a second decision made somewhere else.
    RouteMap(pattern=r"^/api/v1/invoicing/public(/.*)?$", mcp_type=MCPType.EXCLUDE),
    # A multipart upload is not a tool an LLM can call: the payload is a file it does not have,
    # and the mapping it would have to invent is exactly the human judgement the wizard exists
    # for. `/columns` and `/export` stay — reading a shape and taking data out are both useful.
    RouteMap(pattern=r"^/api/v1/impex/[^/]+/(inspect|import)$", mcp_type=MCPType.EXCLUDE),
    RouteMap(pattern=r"^/api/v1/.*", mcp_type=MCPType.TOOL),
    RouteMap(pattern=r".*", mcp_type=MCPType.EXCLUDE),
]


def _tool_names(app: Any) -> dict[str, str]:
    """operationId → a short, stable tool name (``list_companies``, not
    ``list_companies_api_v1_companies_get``). Falls back to the full operationId when the
    short form would collide."""
    operation_ids = [
        operation.get("operationId", "")
        for operations in app.openapi().get("paths", {}).values()
        for operation in operations.values()
        if isinstance(operation, dict)
    ]
    short = {op_id: op_id.split("_api_v1_")[0] for op_id in operation_ids if op_id}
    counts = Counter(short.values())
    return {op_id: name for op_id, name in short.items() if counts[name] == 1}


def build_mcp_asgi_app(app: Any) -> Any:
    """The Starlette ASGI app serving MCP for ``app``, to be mounted at ``/mcp``.

    Stateless JSON mode: every POST is self-contained, so the server scales without session
    affinity and a plain JSON-RPC request (curl, tests) gets a plain JSON response. Being
    stateless is also what makes ``GET`` meaningless here — see :class:`RefuseStandaloneStream`.
    """
    mcp = FastMCP.from_fastapi(
        app=app,
        name="schakl",
        route_maps=_ROUTE_MAPS,
        mcp_names=_tool_names(app),
        httpx_client_kwargs={"auth": ForwardCallerAuth(), "timeout": 30.0},
    )
    mcp.add_middleware(CompactProfile())
    return mcp.http_app(
        path="/",
        stateless_http=True,
        json_response=True,
        # Outermost first: the profile is selected off the path before anything routes on it,
        # and the standalone-stream refusal answers before the session manager can open one.
        middleware=[Middleware(SelectProfile), Middleware(RefuseStandaloneStream)],
    )
