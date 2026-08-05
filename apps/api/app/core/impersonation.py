"""The impersonation grant — one mechanism, two kinds (issues #26 and #296).

An impersonation is a short-lived JWT in its **own** cookie, set next to — never replacing —
the impersonator's session cookie. Authentication always stays the real, logged-in principal;
``require_context`` swaps the *effective* user only when the request carries both a valid
session and a grant that names that session's user. A stolen grant cookie alone is therefore
useless, and the impersonated session never holds more than the target's own permissions:
they resolve for the target, below the swap.

Two kinds share the mechanism because the mechanism is the same, and because the banner, the
stop button and the ``/meta/me`` shape should not fork:

``instance``
    An **instance owner/admin** entering a tenant to support it (issue #26). Cross-tenant, gated
    on ``instance.impersonate``, audited on the instance audit log, and killed instantly by
    ``SCHAKL_INSTANCE_ADMIN_ENABLED=false``. It may have to cross hostnames, which is what the
    single-use handoff in ``core/instance/impersonation.py`` exists for.

``portal``
    An **agency staff member** inside their own org signing in as a client's contact person
    (#296), to see the client portal exactly as that client sees it. Same host, same tenant, so
    no handoff: the staff session cookie is already on this hostname. Gated on
    ``portal.login.impersonate`` and recorded on the *subject's* own activity trail (§16), where
    the agency actually looks — not on the instance log, which is an operator surface a tenant
    cannot read. Issued by the ``portal`` module, which is also the only thing that knows a
    portal subject is a contact.

The kind is a claim rather than a second cookie: one banner, one stop button, and — decisively —
``read_impersonation`` must be able to answer "is this grant still allowed to exist?" for both,
and the answer differs (a ``portal`` grant must keep working on a box with the instance-admin
surface switched off, which is every self-hosted box).

**The capability is checked where the grant is issued, never here.** This runs on every request
on a tenant host, so a query here is a query on the hot path (docs/PERFORMANCE.md). The grant is
signed, audience-bound and clamped to ``SCHAKL_IMPERSONATION_MAX_MINUTES``. The consequence,
stated plainly because it is a real one: revoking the permission does not kill a grant already in
flight — it survives at most one window (≤60 min). Deactivating the account, or (for ``instance``)
the admin flag, is the immediate lever, and every grant is on a trail either way.
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

#: An instance owner/admin inside a tenant (issue #26).
KIND_INSTANCE = "instance"
#: Agency staff signed in as one of their clients' portal contacts (#296).
KIND_PORTAL = "portal"
KINDS: tuple[str, ...] = (KIND_INSTANCE, KIND_PORTAL)


@dataclass(frozen=True)
class ImpersonationClaims:
    target_user_id: uuid.UUID
    org_id: uuid.UUID
    impersonator_id: uuid.UUID
    expires_at: datetime
    #: Which of :data:`KINDS` this grant is. Absent on a grant minted before #296 — those are
    #: all instance grants, and they lapse within the hour anyway.
    kind: str = KIND_INSTANCE


def clamp_minutes(minutes: int) -> int:
    return max(1, min(minutes, settings.impersonation_max_minutes))


def issue_grant(
    impersonator: User,
    target_user_id: uuid.UUID,
    org_id: uuid.UUID,
    minutes: int,
    *,
    kind: str = KIND_INSTANCE,
) -> tuple[str, datetime]:
    """Mint a time-boxed grant. The caller has already authorized it — see the module docstring."""
    if kind not in KINDS:
        raise ValueError(f"unknown impersonation kind {kind!r}")
    lifetime = clamp_minutes(minutes) * 60
    token = generate_jwt(
        {
            "sub": str(target_user_id),
            "org": str(org_id),
            "imp_by": str(impersonator.id),
            "kind": kind,
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

    Fails soft on any defect (expired, garbled, wrong impersonator, unknown kind): the request
    then simply runs as the real user — never as the target.
    """
    token = request.cookies.get(IMPERSONATION_COOKIE)
    if not token:
        return None
    try:
        data = decode_jwt(token, settings.secret_key, audience=[_AUDIENCE])
    except Exception:
        return None
    kind = str(data.get("kind") or KIND_INSTANCE)
    if kind not in KINDS:
        return None
    # Switching the cross-tenant admin surface off kills every instance grant outstanding — the
    # flag is the emergency brake (issue #26). A portal grant is ordinary tenant business and
    # lives on whether the box has that surface at all.
    if kind == KIND_INSTANCE and not settings.instance_admin_enabled:
        return None
    if data.get("imp_by") != str(real_user.id):
        return None
    try:
        return ImpersonationClaims(
            target_user_id=uuid.UUID(str(data["sub"])),
            org_id=uuid.UUID(str(data["org"])),
            impersonator_id=uuid.UUID(str(data["imp_by"])),
            expires_at=datetime.fromtimestamp(int(data["exp"]), UTC),
            kind=kind,
        )
    except (KeyError, ValueError):
        return None
