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
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Generator
from typing import Any

import httpx
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.openapi import MCPType, RouteMap

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


#: First match wins. GETs become tools too — an agent calls, it doesn't browse resources.
_ROUTE_MAPS = [
    RouteMap(pattern=r"^/api/v1/(auth|setup|instance)(/.*)?$", mcp_type=MCPType.EXCLUDE),
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
    affinity and a plain JSON-RPC request (curl, tests) gets a plain JSON response.
    """
    mcp = FastMCP.from_fastapi(
        app=app,
        name="schakl",
        route_maps=_ROUTE_MAPS,
        mcp_names=_tool_names(app),
        httpx_client_kwargs={"auth": ForwardCallerAuth(), "timeout": 30.0},
    )
    return mcp.http_app(path="/", stateless_http=True, json_response=True)
