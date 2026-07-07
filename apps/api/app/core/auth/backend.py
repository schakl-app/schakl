"""Authentication backend: cookie transport + JWT strategy (CLAUDE.md §3).

The SSR web app authenticates via an httpOnly cookie, so tokens never touch client JS.
"""

from __future__ import annotations

from fastapi_users.authentication import (
    AuthenticationBackend,
    CookieTransport,
    JWTStrategy,
)

from app.config import settings

cookie_transport = CookieTransport(
    cookie_name=settings.auth_cookie_name,
    cookie_max_age=settings.auth_token_lifetime_seconds,
    cookie_secure=settings.auth_cookie_secure,
    cookie_httponly=True,
    cookie_samesite="lax",
)


def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(
        secret=settings.secret_key,
        lifetime_seconds=settings.auth_token_lifetime_seconds,
    )


auth_backend = AuthenticationBackend(
    name="cookie",
    transport=cookie_transport,
    get_strategy=get_jwt_strategy,
)
