"""The demo-mode guard: a central, enumerable catalog of blocked operations (issue #141 §4).

Not scattered ``if demo:`` checks — one catalog, one middleware, one test. RLS and RBAC are
untouched (a demo visitor is an ordinary member holding a real role); this is the *third* layer on
top, blocking the operations that are dangerous specifically because the instance is open to the
public internet:

- **Outbound side effects** — Google OAuth connect, Drive/Calendar writes (SSRF/spam surface).
- **Credential surfaces** — API-key minting (also the MCP surface), SSO settings (a
  visitor-configured IdP would capture the next visitor's login), password/email change on a
  shared demo account (would lock the next visitor out).
- **Instance identity** — the whole ``/instance`` admin surface (impersonation, org export/import,
  org lifecycle, license), custom-domain claim/verification.
- **Uploads** — a public box storing arbitrary files is a malware host (blocked in v1).

Blocked calls get a distinct ``errors.demo_blocked`` envelope so the web can toast it. The catalog
is the single source of truth; ``tests/test_demo_mode.py`` walks it deny-by-default style.

Note: email delivery and automation *external* actions are silenced by their own backends (a demo
box carries no SMTP creds; external actions no-op at execution), not by this endpoint catalog —
the demo still lets visitors *build* a rule, it just never fires it outward.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import settings

_WRITE = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_ANY = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})

#: (methods this rule blocks, path prefix under ``/api/v1``). A request is blocked when its method
#: is in the set and its ``/api/v1`` tail equals the prefix or is a child of it. Keep this the
#: single source of truth — the test enumerates it, and a new dangerous endpoint is added here.
DEMO_BLOCKED_RULES: tuple[tuple[frozenset[str], str], ...] = (
    # Instance identity + cross-tenant admin (impersonation, export/import, org lifecycle, license).
    (_ANY, "/instance"),
    # The first-run wizard creates an org — the seeder owns that in demo mode. (GET /setup/status
    # stays open so the web can learn no setup is needed.)
    (frozenset({"POST"}), "/setup"),
    # Custom-domain claim / DNS verification — a visitor must not repoint the instance.
    (_ANY, "/meta/tenant/domain"),
    (_WRITE, "/domains"),
    # Credential surfaces.
    (_WRITE, "/settings/sso"),
    (_WRITE, "/api-keys"),
    (_WRITE, "/service-accounts"),
    (_WRITE, "/users"),
    # Outbound side effects — Google connect + Drive/Calendar writes.
    (_WRITE, "/google"),
    # Uploads (v1: blocked entirely).
    (frozenset({"POST"}), "/files"),
)

_API_PREFIX = "/api/v1"


def demo_block_reason(method: str, path: str) -> str | None:
    """The matched blocked prefix if this request would be blocked in demo mode, else ``None``.

    ``path`` is the full request path; only its ``/api/v1`` tail is matched, so the mount point can
    move without touching the rules.
    """
    index = path.find(_API_PREFIX)
    tail = path[index + len(_API_PREFIX) :] if index != -1 else path
    upper = method.upper()
    for methods, prefix in DEMO_BLOCKED_RULES:
        if upper in methods and (tail == prefix or tail.startswith(prefix + "/")):
            return prefix
    return None


async def demo_guard_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Reject catalogued operations with ``errors.demo_blocked`` when demo mode is on.

    A pure pass-through otherwise, so a normal instance carries zero behaviour change. It runs
    before routing and auth, so a blocked path answers the same whether or not the caller is signed
    in — the point is that the operation is unreachable, period.
    """
    if settings.demo_mode:
        reason = demo_block_reason(request.method, request.url.path)
        if reason is not None:
            return JSONResponse(
                status_code=403,
                content={"error": {"code": "demo_blocked", "message": "errors.demo_blocked"}},
            )
    return await call_next(request)
