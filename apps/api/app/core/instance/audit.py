"""Write path for the instance audit trail (issue #26).

Every instance-level mutation — org lifecycle, impersonation, domain claims, imports —
records who did what to which org, in the emitter's transaction (an audit row for an action
that rolled back would be a lie, and an action whose audit failed must roll back too).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.models import User
from app.core.models import InstanceAuditLog, Org

#: Every action this trail can record.
#:
#: The console renders each one as a sentence (``instance.audit.action.<action>``, en + nl),
#: which it could not do while the vocabulary was thirty-odd string literals scattered over ten
#: modules — so it printed the raw key, in a monospace font, on the one screen an owner reads to
#: find out who signed in as whom (#359). Naming the set here is what makes the labels
#: enumerable: ``tests/test_instance_audit_actions.py`` asserts that every literal handed to
#: :func:`record` is a member, and that every member has both labels. Adding an action means
#: adding it here and to both catalogs in the same change (CLAUDE.md §8).
ACTIONS: frozenset[str] = frozenset(
    {
        "domain.activate",
        "domain.attach",
        "domain.cancel_claim",
        "domain.claim",
        "domain.clear",
        "domain.ownership_verified",
        "impersonate.handoff",
        "impersonate.start",
        "impersonate.stop",
        "instance_admin.grant",
        "instance_admin.revoke",
        "instance_admin.update",
        "instance_key.create",
        "instance_key.revoke",
        "membership.invited",
        "membership.revoked",
        "membership.roles_changed",
        "membership.two_factor_reset",
        "org.create",
        "org.export",
        "org.import",
        "org.lifecycle_archived",
        "org.lifecycle_set",
        "org.lifecycle_suspended",
        "org.lifecycle_terminated",
        "org.lifecycle_warning",
        "org.modules",
        "org.plan",
        "org.purge",
        "org.trial_expired",
        "org.update",
        "service_access.issue",
        "service_access.revoke",
        "service_access.unlock",
        "setup",
    }
)


@dataclass(frozen=True)
class SystemActor:
    """A non-user principal on the trail (§16: an absent actor is the system) — the cron
    that expires trials, or a provisioning API key acting as ``key:<name>``."""

    email: str
    id: None = None


async def record(
    session: AsyncSession,
    *,
    actor: User | SystemActor,
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
