"""Write path for the instance audit trail (issue #26).

Every instance-level mutation — org lifecycle, impersonation, domain claims, imports —
records who did what to which org, in the emitter's transaction (an audit row for an action
that rolled back would be a lie, and an action whose audit failed must roll back too).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.models import User
from app.core.models import InstanceAuditLog, Org


async def record(
    session: AsyncSession,
    *,
    actor: User,
    action: str,
    org: Org | None = None,
    target_user_id: uuid.UUID | None = None,
    detail: dict[str, Any] | None = None,
) -> InstanceAuditLog:
    entry = InstanceAuditLog(
        actor_user_id=actor.id,
        actor_email=actor.email,
        action=action,
        org_id=org.id if org is not None else None,
        org_slug=org.slug if org is not None else None,
        target_user_id=target_user_id,
        detail=detail or {},
    )
    session.add(entry)
    await session.flush()
    return entry
