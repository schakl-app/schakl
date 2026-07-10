"""Time-boxed, audited impersonation for instance owners (issue #26).

The grant is a short-lived JWT in its **own** cookie, set next to — never replacing — the
admin's session cookie. Authentication always stays the real superuser;
``require_context`` swaps the *effective* user only when the request carries both a valid
admin session and a grant that names that admin. A stolen impersonation cookie alone is
therefore useless, and disabling the instance-admin flag kills every outstanding grant
instantly. The banner comes from ``/meta/me`` exposing ``impersonated_by``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Request, Response
from fastapi_users.jwt import decode_jwt, generate_jwt

from app.config import settings
from app.core.auth.models import User

IMPERSONATION_COOKIE = "schakl_impersonate"
_AUDIENCE = "schakl:impersonate"


@dataclass(frozen=True)
class ImpersonationClaims:
    target_user_id: uuid.UUID
    org_id: uuid.UUID
    impersonator_id: uuid.UUID
    expires_at: datetime


def clamp_minutes(minutes: int) -> int:
    return max(1, min(minutes, settings.impersonation_max_minutes))


def issue_grant(
    admin: User, target_user_id: uuid.UUID, org_id: uuid.UUID, minutes: int
) -> tuple[str, datetime]:
    lifetime = clamp_minutes(minutes) * 60
    token = generate_jwt(
        {
            "sub": str(target_user_id),
            "org": str(org_id),
            "imp_by": str(admin.id),
            "aud": _AUDIENCE,
        },
        settings.secret_key,
        lifetime_seconds=lifetime,
    )
    return token, datetime.now(UTC) + timedelta(seconds=lifetime)


def set_grant_cookie(response: Response, token: str, expires_at: datetime) -> None:
    max_age = max(0, int((expires_at - datetime.now(UTC)).total_seconds()))
    response.set_cookie(
        IMPERSONATION_COOKIE,
        token,
        max_age=max_age,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.auth_cookie_secure,
    )


def clear_grant_cookie(response: Response) -> None:
    response.delete_cookie(IMPERSONATION_COOKIE, path="/")


def read_impersonation(request: Request, real_user: User) -> ImpersonationClaims | None:
    """The request's validated impersonation claims, or ``None``.

    Fails soft on any defect (expired, garbled, wrong admin): the request then simply runs
    as the real user — never as the target.
    """
    if not settings.instance_admin_enabled or not real_user.is_superuser:
        return None
    token = request.cookies.get(IMPERSONATION_COOKIE)
    if not token:
        return None
    try:
        data = decode_jwt(token, settings.secret_key, audience=[_AUDIENCE])
    except Exception:
        return None
    if data.get("imp_by") != str(real_user.id):
        return None
    try:
        return ImpersonationClaims(
            target_user_id=uuid.UUID(str(data["sub"])),
            org_id=uuid.UUID(str(data["org"])),
            impersonator_id=uuid.UUID(str(data["imp_by"])),
            expires_at=datetime.fromtimestamp(int(data["exp"]), UTC),
        )
    except (KeyError, ValueError):
        return None
