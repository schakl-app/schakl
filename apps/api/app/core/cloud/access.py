"""Service-PIN access control (epic #199). Business-licensed — see this directory's LICENSE.

On cloud the tenants are paying customers, not the operator's own data: the instance owner
gets no standing access to an org's contents. An org admin generates a **service PIN**
(``settings.service_access.manage``), hands it to support out of band, and the instance
owner claims it — which unlocks that one org, for that one owner, for
``SCHAKL_CLOUD_SERVICE_PIN_HOURS`` (default 24) from issuance. The org sees the grant's
state and can revoke it at any time; every step is on the instance audit trail.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.apikeys.keys import hash_secret, verify_secret
from app.core.auth.models import User
from app.core.cloud.models import ServiceAccessGrant
from app.core.instance import audit
from app.core.instance.guard import InstanceContext
from app.core.models import Org
from app.errors import AppError

#: PIN shape: 12 digits, grouped for reading aloud ("1234-5678-9012"). Stored as SHA-256 of
#: the digits only; presented values are normalised the same way before comparing.
_PIN_DIGITS = 12


def _generate_pin() -> tuple[str, str]:
    """``(display, hash)`` — display is grouped, the hash covers the bare digits."""
    digits = "".join(str(secrets.randbelow(10)) for _ in range(_PIN_DIGITS))
    display = "-".join(digits[i : i + 4] for i in range(0, _PIN_DIGITS, 4))
    return display, hash_secret(digits)


def _normalize_pin(presented: str) -> str:
    return "".join(ch for ch in presented if ch.isdigit())


async def active_grant(session: AsyncSession, org_id: uuid.UUID) -> ServiceAccessGrant | None:
    """The org's single live grant (claimed or not); expired/revoked rows are history."""
    now = datetime.now(UTC)
    return await session.scalar(
        select(ServiceAccessGrant)
        .where(
            ServiceAccessGrant.org_id == org_id,
            ServiceAccessGrant.revoked_at.is_(None),
            ServiceAccessGrant.expires_at > now,
        )
        .order_by(ServiceAccessGrant.created_at.desc())
        .limit(1)
    )


async def issue_pin(
    session: AsyncSession, org: Org, actor: User
) -> tuple[ServiceAccessGrant, str]:
    """Create a fresh grant (revoking any live one — a single PIN is ever valid) and return
    the plaintext PIN exactly once."""
    now = datetime.now(UTC)
    existing = await active_grant(session, org.id)
    if existing is not None:
        existing.revoked_at = now
    display, pin_hash = _generate_pin()
    grant = ServiceAccessGrant(
        org_id=org.id,
        pin_hash=pin_hash,
        created_by_user_id=actor.id,
        created_by_email=actor.email,
        expires_at=now + timedelta(hours=settings.cloud_service_pin_hours),
    )
    session.add(grant)
    await session.flush()
    await audit.record(session, actor=actor, action="service_access.issue", org=org)
    return grant, display


async def revoke_pin(session: AsyncSession, org: Org, actor: User) -> bool:
    grant = await active_grant(session, org.id)
    if grant is None:
        return False
    grant.revoked_at = datetime.now(UTC)
    await session.flush()
    await audit.record(session, actor=actor, action="service_access.revoke", org=org)
    return True


async def claim_pin(ctx: InstanceContext, org: Org, presented: str) -> ServiceAccessGrant:
    """The instance owner presents the org's PIN; a match binds the grant to them."""
    grant = await active_grant(ctx.session, org.id)
    if grant is None or not verify_secret(_normalize_pin(presented), grant.pin_hash):
        raise AppError("invalid_pin", "errors.service_pin_invalid", status_code=403)
    grant.claimed_at = datetime.now(UTC)
    grant.claimed_by_user_id = ctx.user.id
    await ctx.session.flush()
    await audit.record(
        ctx.session,
        actor=ctx.user,
        action="service_access.unlock",
        org=org,
        detail={"expires_at": grant.expires_at.isoformat()},
    )
    return grant


async def access_until(
    session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID
) -> datetime | None:
    """When ``user_id``'s claimed access to ``org_id`` expires, or None without access."""
    grant = await active_grant(session, org_id)
    if grant is None or grant.claimed_at is None or grant.claimed_by_user_id != user_id:
        return None
    return grant.expires_at


async def ensure_cloud_org_access(ctx: InstanceContext, org: Org) -> None:
    """The check the instance guard runs on cloud (``ensure_org_data_access``): tenant-data
    endpoints refuse until this instance owner holds a claimed, unexpired grant."""
    if await access_until(ctx.session, org.id, ctx.user.id) is None:
        raise AppError("service_pin_required", "errors.service_pin_required", status_code=403)
