"""Grant a *later* module's permissions to an existing org's system roles (issue #19).

An org seeded before `subscriptions` shipped has an `admin` role that has never heard of
``subscriptions.read``, and — worse — a `member` role without the module's read permission, which
would make the whole module invisible to members. Giving `admin` a ``"*"`` does not fix the second
half, so both need reconciling.

``org_settings.applied_permission_defaults`` records which catalog keys this org has already been
seeded with. The reconciler diffs the code catalog against it and grants **only the new keys**,
so a tenant who unticked something keeps it unticked.

It runs in the app's lifespan hook, not in a migration: a migration must apply on top of any older
head (`docs/WORKFLOW.md`) and therefore must never import the evolving catalog. Steady state is one
``SELECT`` per org at boot, and a self-hosted instance has exactly one org (CLAUDE.md §5).
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.jobs import run_per_org
from app.core.models import Org, OrgSettings
from app.core.permissions.catalog import ROLE_OWNER, all_permissions
from app.core.permissions.models import Role
from app.core.permissions.service import grant, mark_defaults_applied

logger = logging.getLogger("vlotr.permissions")


async def reconcile_org(org: Org, session: AsyncSession) -> int:
    """Grant this org's system roles any catalog permission they have never been offered."""
    org_settings = await session.scalar(
        select(OrgSettings).where(OrgSettings.org_id == org.id)
    )
    if org_settings is None:
        return 0

    catalog = all_permissions()
    applied = set(org_settings.applied_permission_defaults or ())
    fresh = [spec for spec in catalog if spec.key not in applied]
    if not fresh:
        return 0

    roles = {
        role.key: role
        for role in (
            await session.execute(
                select(Role).where(Role.org_id == org.id, Role.is_system.is_(True))
            )
        ).scalars()
    }
    granted = 0
    for spec in fresh:
        for role_key, permission in spec.default_grants().items():
            if role_key == ROLE_OWNER:
                continue  # owner holds "*" and nothing else, forever
            role = roles.get(role_key)
            if role is None:
                continue
            await grant(session, org.id, role.id, [permission])
            granted += 1

    await mark_defaults_applied(session, org.id, [spec.key for spec in catalog])
    logger.info(
        "granted %d new default permission(s) to org %s (%d new capabilities)",
        granted,
        org.slug,
        len(fresh),
    )
    return granted


async def reconcile_permission_defaults() -> None:
    """Run :func:`reconcile_org` for every active org, one transaction each.

    Never fatal: a stale catalog is a missing capability, not a broken API, and refusing to boot
    would take a healthy instance down. The failure is logged and retried on the next start.
    """
    try:
        await run_per_org(reconcile_org)
    except Exception:  # noqa: BLE001 - boot must not depend on this succeeding
        logger.exception("permission default reconciliation failed")
