"""CORS for the bearer-token surfaces, and only those (#441).

A browser-resident MCP client (MCP Inspector, an in-page connector) passes discovery — the two
``.well-known`` web proxies already say ``Access-Control-Allow-Origin: *`` — and then dies on
the preflight, because the API served no CORS headers at all and ``/mcp``'s credential gate
401s an ``OPTIONS`` that by definition carries no credential.

The boundary is the authentication model, not a list of favourite endpoints. The OAuth
endpoints and ``/mcp`` authenticate by **bearer token** (or by being the token-minting step
itself), so any-origin CORS gives an attacker's page nothing the token does not already gate —
CORS exists to protect *ambient* credentials, and these surfaces use none. The
cookie-authenticated ``/api/v1`` surface is exactly the opposite and never gets a header from
here: blanket CORS over a cookie surface is how a CSRF protection gets deleted by middleware.

Deliberately not Starlette's ``CORSMiddleware``: that is app-wide by construction, and an
allow-list of paths is the entire point here.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

#: Exact paths: the token-minting and lifecycle steps a browser client calls directly.
CORS_PATHS = frozenset(
    {
        "/api/v1/oauth/register",
        "/api/v1/oauth/token",
        "/api/v1/oauth/revoke",
        "/api/v1/oauth/metadata/authorization-server",
        "/api/v1/oauth/metadata/protected-resource",
    }
)
#: Prefixes: the MCP transport, sections included (`/mcp/compact`, `/mcp/google-ads`, …).
CORS_PREFIXES = ("/mcp",)

_PREFLIGHT_HEADERS = [
    (b"access-control-allow-origin", b"*"),
    (b"access-control-allow-methods", b"GET, POST, DELETE, OPTIONS"),
    # What an MCP client actually sends; `*` would exclude Authorization by spec.
    (
        b"access-control-allow-headers",
        b"authorization, content-type, x-api-key, "
        b"mcp-protocol-version, mcp-session-id, last-event-id",
    ),
    (b"access-control-expose-headers", b"www-authenticate, mcp-protocol-version, mcp-session-id"),
    (b"access-control-max-age", b"600"),
]


def _covered(path: str) -> bool:
    return path in CORS_PATHS or any(
        path == prefix or path.startswith(prefix + "/") for prefix in CORS_PREFIXES
    )


class BearerSurfaceCORS:
    """Pure ASGI, wrapping the whole app so the ``/mcp`` mount's own gates sit behind it —
    a preflight is answered here and never has to survive ``RequireCredential``."""

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope["type"] != "http" or not _covered(scope.get("path", "")):
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        origin = headers.get(b"origin")

        if (
            scope.get("method") == "OPTIONS"
            and origin is not None
            and b"access-control-request-method" in headers
        ):
            await send(
                {
                    "type": "http.response.start",
                    "status": 204,
                    "headers": list(_PREFLIGHT_HEADERS),
                }
            )
            await send({"type": "http.response.body", "body": b""})
            return

        async def send_with_cors(message) -> None:  # noqa: ANN001
            if message["type"] == "http.response.start" and origin is not None:
                message.setdefault("headers", [])
                message["headers"] = [
                    *message["headers"],
                    (b"access-control-allow-origin", b"*"),
                    (
                        b"access-control-expose-headers",
                        b"www-authenticate, mcp-protocol-version, mcp-session-id",
                    ),
                ]
            await send(message)

        await self.app(scope, receive, send_with_cors)
