"""Assemble the auth surface, gating local vs. OIDC (CLAUDE.md §3).

- Local enabled (default): login/logout, register, reset-password, verify.
- OIDC enforced: local login/registration are replaced by a disabled stub returning the
  ``auth.local_login_disabled`` i18n key; only SSO routes remain.
The ``/users`` router (me / update) works regardless of how the session was obtained.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.config import settings
from app.core.auth.backend import auth_backend
from app.core.auth.oidc import build_oidc_router
from app.core.auth.schemas import UserCreate, UserRead, UserUpdate
from app.core.auth.users import fastapi_users
from app.core.permissions.deps import exempt_routes
from app.errors import AppError


def build_auth_router() -> APIRouter:
    router = APIRouter()

    if settings.local_login_enabled:
        router.include_router(
            fastapi_users.get_auth_router(auth_backend), prefix="/auth", tags=["auth"]
        )
        if settings.allow_registration:
            router.include_router(
                fastapi_users.get_register_router(UserRead, UserCreate),
                prefix="/auth",
                tags=["auth"],
            )
        router.include_router(
            fastapi_users.get_reset_password_router(), prefix="/auth", tags=["auth"]
        )
        router.include_router(
            fastapi_users.get_verify_router(UserRead), prefix="/auth", tags=["auth"]
        )
    else:

        @router.post("/auth/login", tags=["auth"])
        async def local_login_disabled() -> None:
            raise AppError(
                "local_login_disabled", "auth.local_login_disabled", status_code=403
            )

    router.include_router(
        fastapi_users.get_users_router(UserRead, UserUpdate), prefix="/users", tags=["users"]
    )

    oidc_router = build_oidc_router()
    if oidc_router is not None:
        router.include_router(oidc_router, prefix="/auth/oidc", tags=["auth"])

    # Authentication, not authorization: these run before a tenant membership is resolved, and
    # ``/users/*`` is the caller's own account (or ``is_superuser``, a different axis). Deny-by-
    # default (issue #19) still demands the exemption be stated, not merely implied.
    exempt_routes(router, "authentication and own-account routes; no membership resolved yet")
    return router
