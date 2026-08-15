"""Brute-force rate limiting for the pre-auth surface — login and password reset.

There is no session yet on these routes, so the only thing to key on is the caller's IP.
This mirrors the API-key limiter (:mod:`app.core.apikeys.auth`): a fixed one-minute window in
the shared Redis, so the ceiling holds across every ``api`` replica (an in-process counter
would not), and it **fails open** if Redis is unreachable — a login rate limit is a safeguard,
not an availability dependency, and must never be the reason nobody can sign in.

It is wired as a router dependency (like ``require_local_login``), so it covers the framework-
generated reset routes uniformly without decorating each one. Each protected flow gets its own
bucket name, so a burst of password guesses cannot spend the password-reset budget and vice
versa; the tenant hostname is folded into the key so one tenant's traffic never trips another's.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from fastapi import Request

from app.core.cache import get_redis
from app.errors import AppError

logger = logging.getLogger("schakl.auth")

#: Bucket keys stay valid a little past their window so a single missed expiry can't leak a
#: count into the next minute.
_BUCKET_TTL_SECONDS = 120


def _client_ip(request: Request) -> str:
    """The caller's address as seen past the edge proxy.

    The app sits behind Cloudflare → Traefik (CLAUDE.md §3), so the trustworthy source is
    Cloudflare's ``CF-Connecting-IP``; ``X-Forwarded-For``'s left-most hop is the fallback for a
    plain-Traefik deployment. ``request.client`` is the last resort (and is ``None`` under the
    ASGI test transport, hence the literal).
    """
    cf = request.headers.get("cf-connecting-ip")
    if cf:
        return cf.strip()
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def _enforce(bucket: str, limit: int) -> None:
    """Increment ``bucket``'s per-minute counter and raise 429 once it exceeds ``limit``.

    Fails open on any Redis error — the safeguard is best-effort, authentication is not.
    """
    if limit <= 0:  # 0 (or negative) disables the limit, e.g. in the test environment.
        return
    try:
        redis = get_redis()
        window = int(datetime.now(UTC).timestamp() // 60)
        key = f"schakl:ratelimit:auth:{bucket}:{window}"
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, _BUCKET_TTL_SECONDS)
        if count > limit:
            raise AppError("rate_limited", "errors.rate_limited", status_code=429)
    except AppError:
        raise
    except Exception:  # noqa: BLE001 - a Redis hiccup must never block sign-in
        logger.debug("auth rate limit skipped (redis unavailable)", exc_info=True)


async def limit_by_ip(request: Request, *, bucket: str, limit: int) -> None:
    """The same per-IP, per-tenant, per-minute ceiling, applied from inside a handler.

    :func:`rate_limit` builds a *router* dependency, which is right when a whole flow shares one
    budget. This is for a single route that needs its own — OAuth's dynamic client registration
    (docs/MCP.md), which is the one unauthenticated write in the codebase a stranger can repeat.
    """
    host = request.headers.get("host", "") or _client_ip(request)
    await _enforce(f"{bucket}:{host}:{_client_ip(request)}", limit)


async def limit_by_principal(*, bucket: str, principal: str, limit: int) -> None:
    """The same fixed window, keyed on **who is signed in** rather than on where they came from.

    Everything above this line is pre-auth, where an IP is the only thing there is to count. Past
    the door there is something better: a session names an org and a user, so a ceiling on *them*
    survives a changed network, and — the reason this exists — it does not punish a whole office
    behind one NAT for one person's script. ``principal`` is the caller's identity as the calling
    module wants it counted (``"<org>:<user>"`` for a per-user budget on a third-party quota).

    Everything else is deliberately shared with the pre-auth limiter, including the two
    properties that matter: the window lives in the shared Redis, so the ceiling holds across
    both API replicas rather than per process, and it **fails open** — a rate limit is a
    safeguard, and a Redis outage must not become an outage of the feature it guards.
    """
    await _enforce(f"{bucket}:{principal}", limit)


def rate_limit(name: str, limit: Callable[[], int]) -> Callable[[Request], Awaitable[None]]:
    """Build a FastAPI dependency limiting requests per IP, per minute, per tenant.

    ``name`` names the bucket so distinct flows (login vs. reset) are counted independently.
    ``limit`` is read *live* on every request (not captured at app-build time), so an operator's
    env change — or a test override — takes effect without reconstructing the router.
    """

    async def dependency(request: Request) -> None:
        host = request.headers.get("host", "") or _client_ip(request)
        await _enforce(f"{name}:{host}:{_client_ip(request)}", limit())

    return dependency
