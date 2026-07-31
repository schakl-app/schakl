"""Time-boxed, audited impersonation for instance owners (issue #26).

The grant is a short-lived JWT in its **own** cookie, set next to — never replacing — the
admin's session cookie. Authentication always stays the real superuser;
``require_context`` swaps the *effective* user only when the request carries both a valid
admin session and a grant that names that admin. A stolen impersonation cookie alone is
therefore useless, and disabling the instance-admin flag kills every outstanding grant
instantly. The banner comes from ``/meta/me`` exposing ``impersonated_by``.

**Both cookies have to reach the tenant's own hostname**, and that is the whole difficulty
(#288). Cookies are host-scoped: the console runs on the apex of a cloud install (or on
another org's host on a multi-org box), so the admin's session simply does not exist on
``<slug>.<base_domain>`` — let alone on a customer-owned custom domain, where no shared parent
domain exists to widen a cookie to. Redirecting with the grant in the query string therefore
landed on a host that had the grant and no session, the API refused before
``read_impersonation`` could apply anything, and the browser bounced to the login screen.

So the crossing is an explicit, **single-use handoff**: issuing an impersonation stores a
``ImpersonationHandoff`` row and hands the console nothing but an opaque ticket for the target
host. That host's SSR route redeems it once (``claim_handoff``) and receives the two things it
needs — a session token for the *real* admin, minted for exactly the grant's lifetime, and the
grant itself. Consequences worth stating:

* the ticket is worthless anywhere else: it is bound to the host, the org, the impersonator and
  the target, checked again at redemption, and it expires in ``HANDOFF_LIFETIME``;
* redeeming is atomic and once-only, so a replayed link (browser history, a proxy log, a shared
  screen) refuses instead of re-opening the session;
* no long-lived credential ever travels in a URL — the ticket authenticates *nothing* by
  itself, it only exchanges for cookies over a server-side call;
* the grant JWT does not exist until the handoff is redeemed, so an unclaimed one leaves no
  usable grant anywhere.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Request, Response
from fastapi_users.jwt import decode_jwt, generate_jwt
from sqlalchemy import DateTime, ForeignKey, Integer, String, delete, select
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.config import settings
from app.core.apikeys.keys import hash_secret
from app.core.auth.models import User
from app.core.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base

IMPERSONATION_COOKIE = "schakl_impersonate"
_AUDIENCE = "schakl:impersonate"

#: How long a cross-host ticket stays redeemable. It only has to survive one 303 redirect, so
#: minutes rather than the grant's hour — a link that leaks is useless almost immediately.
HANDOFF_LIFETIME = timedelta(minutes=2)
#: Redeemed and expired rows are kept this long as a trail, then pruned on the next issue.
_HANDOFF_RETENTION = timedelta(days=1)


@dataclass(frozen=True)
class ImpersonationClaims:
    target_user_id: uuid.UUID
    org_id: uuid.UUID
    impersonator_id: uuid.UUID
    expires_at: datetime


class ImpersonationHandoff(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One pending cross-host impersonation crossing (#288).

    Instance-level like ``instance_admins``: it is read on a host whose tenant is not yet bound
    (that is the point — the ticket is what proves *which* tenant may be entered), so it cannot
    sit under RLS, and it is emphatically not tenant data, so an org export never carries it.

    Only the ticket's SHA-256 is stored — verify-only, like an API key or a service PIN. Every
    other column is a binding that is re-checked at redemption, which is what makes a leaked
    link useless: another host, another org, a revoked administrator or a second attempt all
    fail on the row itself rather than on the honesty of the caller.
    """

    __tablename__ = "impersonation_handoffs"

    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    impersonator_user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    target_user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
    )
    #: The tenant hostname this ticket may be redeemed on, resolved from the org when it was
    #: issued. A ticket presented on any other host is refused, so one org's crossing can never
    #: be replayed into another's — nor into the console's own host.
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    #: The (already clamped) grant window to mint on redemption. The clock starts when the
    #: admin actually arrives, not when the console asked.
    minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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


# --------------------------------------------------------------------------- #
# Cross-host handoff (#288)
# --------------------------------------------------------------------------- #
async def create_handoff(
    session: AsyncSession,
    *,
    admin: User,
    target_user_id: uuid.UUID,
    org_id: uuid.UUID,
    host: str,
    minutes: int,
) -> tuple[str, datetime]:
    """Store a pending crossing and return ``(ticket, expires_at)``.

    The ticket is returned exactly once and never stored: what lands in the row is its digest,
    so the console's own logs, the browser history and this table all hold something that cannot
    be redeemed twice anyway.
    """
    now = datetime.now(UTC)
    # Housekeeping, on the rare write rather than in a cron: a redeemed or lapsed ticket is
    # history, and history older than a day answers nothing anybody asks.
    await session.execute(
        delete(ImpersonationHandoff).where(
            ImpersonationHandoff.expires_at < now - _HANDOFF_RETENTION
        )
    )
    ticket = secrets.token_urlsafe(32)
    expires_at = now + HANDOFF_LIFETIME
    session.add(
        ImpersonationHandoff(
            token_hash=hash_secret(ticket),
            impersonator_user_id=admin.id,
            target_user_id=target_user_id,
            org_id=org_id,
            host=host,
            minutes=clamp_minutes(minutes),
            expires_at=expires_at,
        )
    )
    await session.flush()
    return ticket, expires_at


async def claim_handoff(
    session: AsyncSession, ticket: str, host: str
) -> ImpersonationHandoff | None:
    """Redeem a ticket **once**, or ``None`` for every way that can fail.

    ``FOR UPDATE`` is what makes "once" true rather than likely: two requests presenting the
    same ticket serialise on the row, and the second one sees ``claimed_at`` already set. A
    deliberately undifferentiated ``None`` covers unknown, expired, already-redeemed and
    wrong-host — the caller must not be told which, and there is nothing useful it could do
    with the difference.
    """
    if not ticket:
        return None
    row = await session.scalar(
        select(ImpersonationHandoff)
        .where(ImpersonationHandoff.token_hash == hash_secret(ticket))
        .with_for_update()
    )
    now = datetime.now(UTC)
    if row is None or row.claimed_at is not None or row.expires_at <= now or row.host != host:
        return None
    row.claimed_at = now
    await session.flush()
    return row


def read_impersonation(request: Request, real_user: User) -> ImpersonationClaims | None:
    """The request's validated impersonation claims, or ``None``.

    Fails soft on any defect (expired, garbled, wrong admin): the request then simply runs
    as the real user — never as the target.
    """
    # Deliberately **not** a capability lookup. This runs on every request on a tenant host,
    # so a query here is a query on the hot path (docs/PERFORMANCE.md). The capability is
    # checked once, where the grant is *issued* — an instance route that already has the
    # principal loaded — and the grant itself is signed, audience-bound and time-boxed to
    # ``SCHAKL_IMPERSONATION_MAX_MINUTES``.
    #
    # The consequence, stated plainly because it is a real one: revoking
    # ``instance.impersonate`` does not kill a grant already in flight — it survives at most
    # one window (≤60 min). Revoking the ``instance_admins`` row, or deactivating the account,
    # is the immediate lever, and every grant is on the instance audit trail either way.
    if not settings.instance_admin_enabled:
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
