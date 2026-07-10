"""Org-scoped audit of role changes (issue #19).

Every mutation that changes what somebody may do writes one row here, in the caller's
transaction — so an audited action that later rolls back leaves no trail claiming it happened.
``actor_email`` is a snapshot: a deleted user's history stays readable.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.models import User
from app.core.permissions.models import RoleAuditLog


async def record(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor: User,
    action: str,
    role_id: uuid.UUID | None = None,
    role_key: str | None = None,
    target_user_id: uuid.UUID | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    session.add(
        RoleAuditLog(
            org_id=org_id,
            actor_user_id=actor.id,
            actor_email=actor.email,
            action=action,
            role_id=role_id,
            role_key=role_key,
            target_user_id=target_user_id,
            detail=detail or {},
        )
    )
