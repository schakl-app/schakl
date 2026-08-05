"""Time-boxed, audited impersonation for instance owners (issue #26).

The grant itself — cookie, claims, lifetime — is the shared mechanism in
``app/core/impersonation.py``, which #296 also mints ``portal`` grants from; this module owns
the half that is *only* an instance concern: crossing hostnames. The names it re-exports below
are that shared layer, kept importable from here so the console's router reads as one surface.

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
from datetime import UTC, datetime, timedelta

from sqlalchemy import DateTime, ForeignKey, Integer, String, delete, select
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.apikeys.keys import hash_secret
from app.core.auth.models import User
from app.core.impersonation import (  # noqa: F401 — re-exported: this is the console's seam
    IMPERSONATION_COOKIE,
    KIND_INSTANCE,
    ImpersonationClaims,
    clamp_minutes,
    clear_grant_cookie,
    issue_grant,
    read_impersonation,
    set_grant_cookie,
)
from app.core.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base

#: How long a cross-host ticket stays redeemable. It only has to survive one 303 redirect, so
#: minutes rather than the grant's hour — a link that leaks is useless almost immediately.
HANDOFF_LIFETIME = timedelta(minutes=2)
#: Redeemed and expired rows are kept this long as a trail, then pruned on the next issue.
_HANDOFF_RETENTION = timedelta(days=1)


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
