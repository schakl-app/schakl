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
