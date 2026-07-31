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
from app.core.auth.models import User

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


async def issue_session_token(user: User, lifetime_seconds: int) -> str:
    """A session token for ``user`` that expires in ``lifetime_seconds``.

    Same secret, same audience and therefore the same validation path as a token from the login
    route — only the lifetime differs. The one caller is the cross-host impersonation handoff
    (#288), which has to put the *administrator's* session on the tenant's hostname (cookies are
    host-scoped, so the console's own session is not there) and wants it to lapse exactly with
    the impersonation grant rather than to sit there for a week.
    """
    return await JWTStrategy(
        secret=settings.secret_key, lifetime_seconds=lifetime_seconds
    ).write_token(user)
