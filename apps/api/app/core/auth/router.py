"""Assemble the auth surface — everything mounted, gates resolved per request (issue #76).

- The password flows (login/register/reset/verify) are always mounted and carry
  :func:`~app.core.auth.sso.require_local_login`: an org that *enforces* OIDC gets a 403
  ``auth.local_login_disabled`` at request time, any other org logs in normally, and
  ``SCHAKL_FORCE_LOCAL_LOGIN=true`` is the operator break-glass (docs/SSO.md). Logout is
  exempt inside the guard — ending a session must work however it began.
- The OIDC login/callback routes are always mounted too; they resolve the org's stored SSO
  config per request and answer "not configured" cleanly (``oidc.py``).
The ``/users`` router (me / update) works regardless of how the session was obtained.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.config import settings
from app.core.auth.account import router as account_router
from app.core.auth.backend import auth_backend
from app.core.auth.oidc import router as oidc_router
from app.core.auth.ratelimit import rate_limit
from app.core.auth.schemas import UserCreate, UserRead, UserUpdate
from app.core.auth.sso import require_local_login
from app.core.auth.twofactor_router import login_router as twofactor_login_router
from app.core.auth.twofactor_router import router as twofactor_router
from app.core.auth.users import fastapi_users
from app.core.permissions.deps import exempt_routes


def build_auth_router() -> APIRouter:
    router = APIRouter()
    local_login_guard = [Depends(require_local_login)]
    # Brute-force ceilings on the pre-auth surface (ratelimit.py). Login guards against password
    # guessing; the reset routes get their own, tighter budget so one flow can't spend the
    # other's. Both fail open if Redis is down.
    login_limit = [Depends(rate_limit("login", lambda: settings.login_rate_limit_per_minute))]
    reset_limit = [
        Depends(rate_limit("reset", lambda: settings.password_reset_rate_limit_per_minute))
    ]

    # FastAPI Users' auth router minus its /login: the 2FA-aware replacement
    # (twofactor_router.py) owns that path — same name, same contract for accounts without a
    # second factor — while /logout stays the framework's.
    framework_auth = fastapi_users.get_auth_router(auth_backend)
    framework_auth.routes = [
        r for r in framework_auth.routes if r.name != f"auth:{auth_backend.name}.login"
    ]
    router.include_router(
        twofactor_login_router,
        prefix="/auth",
        tags=["auth"],
        dependencies=local_login_guard + login_limit,
    )
    router.include_router(
        framework_auth,
        prefix="/auth",
        tags=["auth"],
        dependencies=local_login_guard,
    )
    # The whole 2FA surface — challenge redemption and self-service enrollment — is local-login
    # machinery, so it sits behind the same per-org guard (an org that enforces OIDC gets its
    # MFA from the IdP; docs/TWOFACTOR.md).
    router.include_router(
        twofactor_router, prefix="/auth", tags=["auth"], dependencies=local_login_guard
    )
    if settings.allow_registration:
        router.include_router(
            fastapi_users.get_register_router(UserRead, UserCreate),
            prefix="/auth",
            tags=["auth"],
            dependencies=local_login_guard,
        )
    router.include_router(
        fastapi_users.get_reset_password_router(),
        prefix="/auth",
        tags=["auth"],
        dependencies=local_login_guard + reset_limit,
    )
    router.include_router(
        fastapi_users.get_verify_router(UserRead),
        prefix="/auth",
        tags=["auth"],
        dependencies=local_login_guard,
    )

    router.include_router(
        fastapi_users.get_users_router(UserRead, UserUpdate), prefix="/users", tags=["users"]
    )
    # Change-email lives with the other credential changes, behind the same per-org guard:
    # the address is the *local* sign-in identifier (an OIDC-enforced org manages it at the IdP).
    router.include_router(
        account_router, prefix="/users", tags=["users"], dependencies=local_login_guard
    )

    router.include_router(oidc_router, prefix="/auth/oidc", tags=["auth"])

    # Authentication, not authorization: these run before a tenant membership is resolved, and
    # ``/users/*`` is the caller's own account (or ``is_superuser``, a different axis). Deny-by-
    # default (issue #19) still demands the exemption be stated, not merely implied.
    exempt_routes(router, "authentication and own-account routes; no membership resolved yet")
    return router
