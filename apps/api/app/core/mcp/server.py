"""MCP server over Streamable HTTP at ``/mcp`` (CLAUDE.md §12).

Every ``/api/v1`` operation becomes an MCP tool, generated from the app's own OpenAPI spec
(FastMCP's OpenAPI integration) and proxied **in-process** back to the REST API. No second
data path exists: a tool call travels through ``require_context`` — hostname → org, RLS
bound, permissions resolved — exactly like the HTTP request it is.

**Auth: API keys, and OAuth mints one.** The platform's keys (#20) already carry per-key
permission scopes, are tenant-scoped, revocable and optionally non-expiring — precisely the
per-MCP-key permission model wanted here. A client configures ``Authorization: Bearer schakl_…``
(or ``X-API-Key``); the proxy forwards the credential plus the tenant host on every internal
call, so deny-by-default route permissions and the key's scopes govern MCP exactly as they
govern HTTP. The OAuth 2.1 layer in ``app.core.oauth`` does not add a second credential: what
its flow hands back **is** an API key row, so everything below is unchanged by it and the only
new thing here is the 401 that tells a client where to go and get one
(:class:`RequireCredential`).

Excluded from the tool surface: the session flows (``/auth``, ``/setup``) and the
instance-operator surface (``/instance``) — none make sense for a headless key.

**Sections, one server.** ``/mcp`` is the whole surface. ``/mcp/<section>`` is the same server,
the same session manager and the same lifespan answering ``tools/list`` with less —
see :mod:`app.core.mcp.sections` for what a section is and, more importantly, for why almost
none of them are written down by hand.
"""

from __future__ import annotations

import json
import logging
import re
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

from app.core.mcp.sections import (
    Section,
    assert_no_collisions,
    build_sections,
    resolve_segment,
)
from app.core.tenancy import origin_from

logger = logging.getLogger("schakl.mcp")

#: Credential headers copied from the incoming MCP request onto the proxied API call.
_FORWARDED_HEADERS = ("authorization", "x-api-key")

#: Scope key :class:`SelectSection` stamps and :class:`SectionListing` reads.
_SECTION_KEY = "schakl_mcp_section"


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


async def _json_error(
    send: Any, *, status: int, code: int, message: str, headers: tuple = ()
) -> None:
    """A JSON-RPC error body written straight to the transport, before the SDK sees the request.

    All three refusals below answer *outside* the protocol handler — a verb it will not serve, a
    caller with no credential, a section that does not exist — so each has to write its own
    response. Shaped as JSON-RPC anyway, because the thing reading it is an MCP client, and a
    bare Starlette 404 gives it nothing to report but "connection failed".
    """
    body = json.dumps(
        {"jsonrpc": "2.0", "id": "server-error", "error": {"code": code, "message": message}}
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                *headers,
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _external_origin(scope: dict) -> str:
    """The origin a caller reached this server on, as the edge saw it.

    Read from the forwarded headers rather than from configuration, because the tenant's
    hostname *is* the tenant (§5) and a document naming the wrong host sends a client to a
    different org's authorization server. The scheme rule is shared with the API's own
    ``external_origin`` rather than restated — one guess, in one place.
    """
    headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
    return origin_from(
        headers.get("x-forwarded-host") or headers.get("host") or "",
        headers.get("x-forwarded-proto", ""),
    )


class RequireCredential:
    """Answer an MCP request carrying no credential with 401 and where to get one.

    Two things are true and only one of them was being served. A client that speaks OAuth
    discovers the authorization server by *being refused* — RFC 9728 says the refusal carries
    ``WWW-Authenticate: Bearer resource_metadata="…"``, and a server that answers an
    unauthenticated request with 200 tells it nothing, so "Add connector" in someone's chat
    client can never complete a flow. And listing 623 tool names to nobody in particular
    discloses this tenant's entire module set and feature surface before anyone has proved they
    may see it.

    So an MCP request with neither ``Authorization`` nor ``X-API-Key`` is refused here, at the
    transport, before the session manager reads a byte of JSON-RPC. **This is a behaviour
    change**: ``tools/list`` used to answer anonymously. Every authenticated call is untouched —
    a presented credential takes exactly the path it took before, including being wrong, which
    still surfaces as the API's own envelope on the individual tool call.

    ``resource`` names *this* URL, section segment included, because that is the resource the
    token will be audience-bound to and RFC 8707 does not accept a near-enough one.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower() for k, _ in scope.get("headers", [])}
        if "authorization" in headers or "x-api-key" in headers:
            await self.app(scope, receive, send)
            return

        origin = _external_origin(scope)
        path = scope.get("path", "/mcp").rstrip("/") or "/mcp"
        resource = f"{origin}{path}"
        # RFC 9728 §3.1: the metadata URL inserts the well-known segment between host and path,
        # so the document for `https://host/mcp/google-ads` is served at
        # `https://host/.well-known/oauth-protected-resource/mcp/google-ads`.
        metadata = f"{origin}/.well-known/oauth-protected-resource{path}"
        await _json_error(
            send,
            status=401,
            code=-32001,
            message=(
                "Unauthorized: present an API key as `Authorization: Bearer schakl_…`, or "
                f"complete the OAuth flow advertised for {resource} in WWW-Authenticate."
            ),
            headers=(
                (
                    b"www-authenticate",
                    f'Bearer realm="schakl", resource_metadata="{metadata}"'.encode(),
                ),
            ),
        )


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
            await _json_error(
                send,
                status=405,
                code=-32600,
                message=(
                    "Method Not Allowed: this server is stateless, so it offers no standalone "
                    "SSE stream. Send JSON-RPC over POST."
                ),
                headers=((b"allow", b"POST"),),
            )
            return
        await self.app(scope, receive, send)


class SelectSection:
    """Route ``/mcp/<section>`` to the same MCP server, with the section stamped on the scope.

    A path segment rather than a query parameter, because this URL is pasted into somebody
    else's settings screen and a query string is the part of a URL that tools normalise, strip
    and re-encode. The rewrite is the whole mechanism: one server, one session manager, one
    lifespan — the section changes only what ``tools/list`` answers, which is where the entire
    problem lives.

    Mounted apps see the **whole** path with the mount prefix in ``root_path``, not the
    remainder — so the segment is read relative to ``root_path`` and the rewrite puts it back,
    which is also what keeps this correct if ``/mcp`` ever moves.
    """

    def __init__(self, app: Any, sections: dict[str, Section]) -> None:
        self.app = app
        self.sections = sections

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        root = scope.get("root_path", "")
        path = scope.get("path", "")
        relative = path[len(root) :] if root and path.startswith(root) else path
        section = resolve_segment(self.sections, relative)
        if section is None:
            # An unknown segment is refused, and the refusal *names what exists* — this URL is
            # typed by hand into somebody else's settings screen, so the one place the answer is
            # useful is the response to the typo. See ``sections.resolve_segment`` for why this
            # is not quietly widened to the full surface.
            await _json_error(
                send,
                status=404,
                code=-32601,
                message=(
                    f"No such MCP section {relative.strip('/')!r}. Available: "
                    f"{', '.join(sorted(self.sections))}."
                ),
            )
            return
        if isinstance(section, Section):
            scope = {**scope, "path": f"{root}/", _SECTION_KEY: section.key}
        await self.app(scope, receive, send)


class SectionListing(MCPMiddleware):
    """Answer ``tools/list`` with the section's tools when the caller asked for a section.

    Two reductions, and the second one is not decoration. Filtering to the section's set is the
    obvious half. Dropping ``output_schema`` is the half that makes the budget: it is **79% of
    the full surface's bytes** — response shapes, which a client needs to *validate* a result it
    already has, never to decide whether to call. Six single tools in the full surface each
    exceed ChatGPT's entire allowance on their own, and every one of them is a response schema
    wearing a tool's name.

    It is dropped for **every** section rather than only for the curated one, because a section
    is asked for by somebody who needed a smaller surface, and this is the largest reduction
    available that costs nothing at the moment a caller decides. ``/mcp`` is the URL that means
    "give me everything" and keeps everything.

    Only the listing is narrowed. A call to a tool outside the section still works and is still
    governed by the key's scopes — see :mod:`app.core.mcp.sections` on why dressing that up as
    an authorization boundary would be worse than not having sections at all.
    """

    def __init__(self, sections: dict[str, Section]) -> None:
        self.sections = sections

    async def on_list_tools(self, context: MiddlewareContext, call_next):  # noqa: ANN001, ANN201
        tools = await call_next(context)
        try:
            request = get_http_request()
        except RuntimeError:  # no HTTP request in scope (in-process, tests)
            return tools
        section = self.sections.get(request.scope.get(_SECTION_KEY) or "")
        if section is None:
            return tools
        return [
            tool.model_copy(update={"output_schema": None})
            for tool in tools
            if tool.name in section.tools
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
    # The OAuth 2.1 endpoints are how a client *gets* a credential, so they are reached before
    # one exists and are excluded for the same reason ``/auth`` is. Offering them as tools would
    # also hand a model the registration endpoint, which is the one route here that writes a row
    # for an unauthenticated caller.
    RouteMap(pattern=r"^/api/v1/oauth(/.*)?$", mcp_type=MCPType.EXCLUDE),
    # A multipart upload is not a tool an LLM can call: the payload is a file it does not have,
    # and the mapping it would have to invent is exactly the human judgement the wizard exists
    # for. `/columns` and `/export` stay — reading a shape and taking data out are both useful.
    RouteMap(pattern=r"^/api/v1/impex/[^/]+/(inspect|import)$", mcp_type=MCPType.EXCLUDE),
    # The other multipart routes, for a different reason: a generated tool sends a JSON body,
    # so a ``multipart/form-data`` route answers every agent with ``422 file: field required``
    # however the bytes were meant — a tool that can only refuse (#253), listed. Each has a
    # JSON twin or a JSON-only equivalent an agent *can* call: ``POST /files/inline`` carries
    # the bytes as base64, which is the route to reach for. Method-scoped so ``GET /files``
    # (the list) stays a tool.
    RouteMap(
        methods=["POST"],
        pattern=r"^/api/v1/(files|hr/documents|interactions/upload-eml|companies/[^/]+/logo)$",
        mcp_type=MCPType.EXCLUDE,
    ),
    RouteMap(pattern=r"^/api/v1/.*", mcp_type=MCPType.TOOL),
    RouteMap(pattern=r".*", mcp_type=MCPType.EXCLUDE),
]


def _becomes_a_tool(path: str, method: str = "GET") -> bool:
    """Whether ``_ROUTE_MAPS`` turns this operation into a tool — read from the maps, never
    restated. ``method`` matters since the multipart exclusions: ``GET /files`` is a tool and
    ``POST /files`` is not, and a path-only answer would drop or keep both.

    A section counts its tools, and a count is a number printed on a settings screen, so it has
    to be the number a client will actually receive. Restating the exclusions here would put a
    second copy of them one function away from the first: the invoicing section would have gone
    on claiming 66 tools while serving 61, because ``/invoicing/public`` is excluded in one place
    and was not excluded in the other.
    """
    verb = method.upper()
    for route_map in _ROUTE_MAPS:
        if route_map.methods != "*" and verb not in route_map.methods:
            continue
        pattern = route_map.pattern
        if re.search(pattern, path) if isinstance(pattern, str) else pattern.search(path):
            return route_map.mcp_type is MCPType.TOOL
    return False


def _tool_routes(mcp: Any, fallback: dict[str, str]) -> dict[str, str]:
    """tool name → API path, read off the **built server** rather than predicted from the spec.

    Predicting it does not work, and the way it fails is the reason this function exists.
    ``mcp_names`` only supplies a name where the short form is unique; everything else keeps its
    operationId, and FastMCP then derives a name from that — splitting at the first ``__`` (the
    delimiter FastAPI puts around a path parameter) and capping the result. So
    ``delete_account_api_v1_cloudflare_accounts__account_id__delete`` is served as
    ``delete_account_api_v1_cloudflare_accounts``, and an index keyed on the operationId matches
    **no tool at all**: 27 of them were silently absent from their own module's section, and the
    section still looked plausible because the other 597 were there.

    Reading a private attribute is the lesser evil, and deliberately so. Restating the naming
    rule here would be a second copy of somebody else's implementation detail, and a copy that
    goes stale drops tools *quietly* — which is the failure above, one release later. This
    breaks loudly instead, and ``test_a_module_section_is_derived_from_the_module_router``
    compares a section against what ``tools/list`` actually answers, so a FastMCP upgrade that
    moves this turns CI red rather than a customer's agent stupid.

    The fallback keeps a surprised instance serving: sections built from the spec are right for
    the overwhelming majority of tools, which beats 404ing every section URL at boot.
    """
    registered = getattr(getattr(mcp, "_tool_manager", None), "_tools", None)
    if not isinstance(registered, dict) or not registered:
        logger.warning(
            "MCP: cannot read the built tool registry, falling back to spec-derived section "
            "membership — sections may omit tools whose name FastMCP rewrote"
        )
        return fallback
    routes = {
        name: tool._route.path  # noqa: SLF001 — see the docstring; the alternative is worse
        for name, tool in registered.items()
        if getattr(getattr(tool, "_route", None), "path", None)
    }
    return routes or fallback


def _tool_index(app: Any) -> tuple[dict[str, str], dict[str, str]]:
    """``(operationId → tool name, tool name → API path)``, built in one pass over the spec.

    The first half is the naming this server has always done: a short, stable ``list_companies``
    rather than ``list_companies_api_v1_companies_get``, falling back to the full operationId
    when the short form would collide. It covers **every** operation, because a name is only
    unique if collisions are counted across the whole spec — narrowing it to the tool routes
    first would let an excluded operation's twin quietly claim the short name.

    The second half is what sections are derived from, and it is built *here* because this is
    the only place both facts are in hand at once. Asking FastMCP afterwards which route backs a
    tool would mean reading an attribute it does not promise; asking the spec afterwards would
    mean reproducing the naming rule above and hoping the copy stays honest.
    """
    entries = [
        (path, method, operation.get("operationId", ""))
        for path, operations in app.openapi().get("paths", {}).items()
        for method, operation in operations.items()
        if isinstance(operation, dict)
    ]
    short = {op_id: op_id.split("_api_v1_")[0] for _, _, op_id in entries if op_id}
    counts = Counter(short.values())
    names = {op_id: name for op_id, name in short.items() if counts[name] == 1}
    paths = {
        names.get(op_id, op_id): path
        for path, method, op_id in entries
        if op_id and _becomes_a_tool(path, method)
    }
    return names, paths


def build_mcp_asgi_app(app: Any) -> Any:
    """The Starlette ASGI app serving MCP for ``app``, to be mounted at ``/mcp``.

    Stateless JSON mode: every POST is self-contained, so the server scales without session
    affinity and a plain JSON-RPC request (curl, tests) gets a plain JSON response. Being
    stateless is also what makes ``GET`` meaningless here — see :class:`RefuseStandaloneStream`.
    """
    names, spec_paths = _tool_index(app)
    mcp = FastMCP.from_fastapi(
        app=app,
        name="schakl",
        route_maps=_ROUTE_MAPS,
        mcp_names=names,
        httpx_client_kwargs={"auth": ForwardCallerAuth(), "timeout": 30.0},
    )
    # Sections are built *after* the server, from the names it actually serves — never from the
    # names the spec suggests. See :func:`_tool_routes` for the 27 tools that difference hid.
    paths = _tool_routes(mcp, spec_paths)
    sections = build_sections(paths)
    assert_no_collisions(sections)
    mcp.add_middleware(SectionListing(sections))
    asgi = mcp.http_app(
        path="/",
        stateless_http=True,
        json_response=True,
        # Outermost first, and the order is an argument. The standalone-stream refusal comes
        # first because ``GET`` is not offered here to *anyone* — answering 401 to a verb that
        # would still be refused after authenticating sends a client off to complete an OAuth
        # flow that cannot fix it. Then the credential challenge, before anything reads a body.
        # Then the section, before anything routes on the path.
        middleware=[
            Middleware(RefuseStandaloneStream),
            Middleware(RequireCredential),
            Middleware(SelectSection, sections=sections),
        ],
    )
    # Hung off the app so ``/meta/mcp`` can describe what this instance serves without building
    # a second FastMCP (which would cost the whole spec walk again, per request).
    asgi.state.sections = sections
    asgi.state.tool_count = len(paths)
    return asgi
