"""Seeding, assignment and resolution of roles (issue #19).

Everything here assumes the RLS GUC is already bound to ``org_id`` — the three tables are
RLS-forced, so an unbound session sees nothing and writes nothing (fail closed).

**Release *N* invariant:** every membership holds at least one system role, and
``memberships.role`` is dual-written with that membership's *highest-privilege* system role.
That keeps ``Role(membership.role)`` in the previous image parseable, so rolling the image back
is always clean. The invariant (and the dual write) is dropped in the contract release, once
``memberships.role`` is gone.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import Membership, OrgSettings
from app.core.permissions.catalog import (
    PRIVILEGE_ORDER,
    SYSTEM_ROLES,
    default_permissions_for,
    permission_keys,
)
from app.core.permissions.models import MembershipRole, Role, RolePermission
from app.core.permissions.permset import PermissionSet
from app.core.permissions.spec import WILDCARD

#: Holding either of these is what makes a membership a *role manager*. The lockout guard
#: counts them; it must never reach zero (issue #19).
ROLE_MANAGER_PERMISSIONS: tuple[str, ...] = (WILDCARD, "settings.roles.manage")


# --------------------------------------------------------------------------- #
# Seeding
# --------------------------------------------------------------------------- #
async def seed_system_roles(session: AsyncSession, org_id: uuid.UUID) -> dict[str, Role]:
    """Create the four system roles with their default permissions. Idempotent.

    Also stamps ``org_settings.applied_permission_defaults`` with the whole catalog, so the
    startup reconciler knows this org is already current and only grants what ships *later*.
    """
    roles = {
        role.key: role
        for role in (
            await session.execute(select(Role).where(Role.org_id == org_id))
        ).scalars()
    }
    for spec in SYSTEM_ROLES:
        role = roles.get(spec.key)
        if role is None:
            role = Role(
                org_id=org_id,
                key=spec.key,
                name_i18n=dict(spec.name_i18n),
                description_i18n=dict(spec.description_i18n),
                is_system=True,
                position=spec.position,
            )
            session.add(role)
            await session.flush()
            roles[spec.key] = role
        await grant(session, org_id, role.id, default_permissions_for(spec.key))
    await mark_defaults_applied(session, org_id, permission_keys())
    return roles


async def grant(
    session: AsyncSession,
    org_id: uuid.UUID,
    role_id: uuid.UUID,
    permissions: Iterable[str],
) -> None:
    """Add permissions to a role, skipping the ones it already holds."""
    rows = [
        {"id": uuid.uuid4(), "org_id": org_id, "role_id": role_id, "permission": permission}
        for permission in sorted(set(permissions))
    ]
    if not rows:
        return
    await session.execute(
        pg_insert(RolePermission.__table__)
        .values(rows)
        .on_conflict_do_nothing(
            constraint="uq_role_permissions_org_id_role_id_permission"
        )
    )


async def mark_defaults_applied(
    session: AsyncSession, org_id: uuid.UUID, keys: Sequence[str]
) -> None:
    org_settings = await session.scalar(
        select(OrgSettings).where(OrgSettings.org_id == org_id)
    )
    if org_settings is None:  # created later in the same transaction (setup/instance flows)
        return
    applied = set(org_settings.applied_permission_defaults or ())
    org_settings.applied_permission_defaults = sorted(applied | set(keys))


# --------------------------------------------------------------------------- #
# Assignment
# --------------------------------------------------------------------------- #
async def role_by_key(session: AsyncSession, org_id: uuid.UUID, key: str) -> Role | None:
    return await session.scalar(
        select(Role).where(Role.org_id == org_id, Role.key == key)
    )


async def create_membership(
    session: AsyncSession,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    role_key: str,
) -> Membership:
    """The only sanctioned way to add someone to an org: a membership *and* its system role.

    A membership without a role would authenticate and then hold no permissions at all.
    """
    membership = Membership(org_id=org_id, user_id=user_id, role=role_key)
    session.add(membership)
    await session.flush()
    role = await role_by_key(session, org_id, role_key)
    if role is None:  # an org seeded before this release, or an unknown key
        (await seed_system_roles(session, org_id))
        role = await role_by_key(session, org_id, role_key)
    if role is not None:
        session.add(
            MembershipRole(org_id=org_id, membership_id=membership.id, role_id=role.id)
        )
        await session.flush()
    return membership


async def membership_role_ids(
    session: AsyncSession, org_id: uuid.UUID, membership_id: uuid.UUID
) -> list[uuid.UUID]:
    return list(
        (
            await session.execute(
                select(MembershipRole.role_id).where(
                    MembershipRole.org_id == org_id,
                    MembershipRole.membership_id == membership_id,
                )
            )
        )
        .scalars()
        .all()
    )


async def set_membership_roles(
    session: AsyncSession,
    org_id: uuid.UUID,
    membership: Membership,
    role_ids: Sequence[uuid.UUID],
) -> list[Role]:
    """Replace a membership's whole role set (one save), then dual-write the legacy column."""
    wanted = set(role_ids)
    roles = list(
        (
            await session.execute(
                select(Role).where(Role.org_id == org_id, Role.id.in_(wanted or [uuid.uuid4()]))
            )
        )
        .scalars()
        .all()
    )
    existing = list(
        (
            await session.execute(
                select(MembershipRole).where(
                    MembershipRole.org_id == org_id,
                    MembershipRole.membership_id == membership.id,
                )
            )
        )
        .scalars()
        .all()
    )
    for link in existing:
        if link.role_id not in wanted:
            await session.delete(link)
    held = {link.role_id for link in existing}
    for role in roles:
        if role.id not in held:
            session.add(
                MembershipRole(
                    org_id=org_id, membership_id=membership.id, role_id=role.id
                )
            )
    await session.flush()
    membership.role = collapse_to_legacy_role([r.key for r in roles if r.is_system])
    await session.flush()
    return roles


def collapse_to_legacy_role(system_role_keys: Iterable[str]) -> str:
    """The single legacy enum value a multi-role membership writes back: highest privilege wins.

    Release *N* forbids a membership that holds no system role precisely so this never fails
    (see the rollback decision on issue #19); the fallback is defensive, not reachable.
    """
    held = set(system_role_keys)
    for key in PRIVILEGE_ORDER:
        if key in held:
            return key
    return PRIVILEGE_ORDER[-1]


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #
async def effective_permissions(
    session: AsyncSession, org_id: uuid.UUID, membership_id: uuid.UUID
) -> PermissionSet:
    """The union of every permission on every role this membership holds. One query."""
    rows = (
        await session.execute(
            select(RolePermission.permission)
            .join(MembershipRole, MembershipRole.role_id == RolePermission.role_id)
            .where(
                MembershipRole.org_id == org_id,
                MembershipRole.membership_id == membership_id,
            )
            .distinct()
        )
    ).scalars()
    return PermissionSet.of(rows)


async def role_manager_count(session: AsyncSession, org_id: uuid.UUID) -> int:
    """How many memberships can still administer roles.

    Replaces the old ``owner``-counting guard: the moment ``membership_roles`` is authoritative,
    counting ``memberships.role == 'owner'`` answers the wrong question. Zero here means the org
    has locked itself out for good, so every mutation that could cause it is rejected.
    """
    return int(
        await session.scalar(
            select(func.count(func.distinct(MembershipRole.membership_id)))
            .select_from(MembershipRole)
            .join(RolePermission, RolePermission.role_id == MembershipRole.role_id)
            .where(
                MembershipRole.org_id == org_id,
                RolePermission.permission.in_(ROLE_MANAGER_PERMISSIONS),
            )
        )
        or 0
    )


def permission_holder_ids(org_id: uuid.UUID, permission: str):
    """A ``select(user_id)`` over the memberships holding ``permission`` at *any* scope.

    ``DISTINCT`` is mandatory: a user holding two granting roles would otherwise appear twice in
    every people-picker built on this.
    """
    return (
        select(Membership.user_id)
        .distinct()
        .join(MembershipRole, MembershipRole.membership_id == Membership.id)
        .join(RolePermission, RolePermission.role_id == MembershipRole.role_id)
        .where(
            Membership.org_id == org_id,
            RolePermission.permission.in_(
                [permission, f"{permission}:own", f"{permission}:any", WILDCARD]
            ),
        )
    )
