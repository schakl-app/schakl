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
from app.core.auth.backend import auth_backend
from app.core.auth.oidc import router as oidc_router
from app.core.auth.schemas import UserCreate, UserRead, UserUpdate
from app.core.auth.sso import require_local_login
from app.core.auth.users import fastapi_users
from app.core.permissions.deps import exempt_routes


def build_auth_router() -> APIRouter:
    router = APIRouter()
    local_login_guard = [Depends(require_local_login)]

    router.include_router(
        fastapi_users.get_auth_router(auth_backend),
        prefix="/auth",
        tags=["auth"],
        dependencies=local_login_guard,
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
        dependencies=local_login_guard,
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

    router.include_router(oidc_router, prefix="/auth/oidc", tags=["auth"])

    # Authentication, not authorization: these run before a tenant membership is resolved, and
    # ``/users/*`` is the caller's own account (or ``is_superuser``, a different axis). Deny-by-
    # default (issue #19) still demands the exemption be stated, not merely implied.
    exempt_routes(router, "authentication and own-account routes; no membership resolved yet")
    return router
