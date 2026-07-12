"""Tenant-facing roles API (issue #19): the permission catalog and role CRUD.

Every mutation here can lock an agency out of its own instance, so every mutation goes through
:func:`ensure_a_role_manager_remains` **after** the write is flushed — the check sees the world
the caller is proposing, and the ``AppError`` unwinds ``require_context``, which rolls the
transaction back. Four shapes reach it: untick the permission, delete the role, remove the role
from a membership, revoke the membership (that last one lives in ``members.py``).
"""

from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.core.permissions import audit
from app.core.permissions.catalog import ROLE_OWNER, all_permissions
from app.core.permissions.deps import no_permission_required, require_permission
from app.core.permissions.models import MembershipRole, Role
from app.core.permissions.schemas import (
    PermissionCatalog,
    PermissionRead,
    RoleCreate,
    RoleRead,
    RoleUpdate,
)
from app.core.permissions.service import (
    member_counts,
    permissions_by_role,
    replace_permissions,
)
from app.core.permissions.spec import WILDCARD
from app.core.tenancy import RequestContext, require_context
from app.errors import AppError

roles_router = APIRouter(prefix="/roles", tags=["roles"])
permissions_router = APIRouter(prefix="/permissions", tags=["roles"])

_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


# --------------------------------------------------------------------------- #
# Shared guards
# --------------------------------------------------------------------------- #
def _lockout_guard():
    """Imported lazily: ``members.py`` owns the canonical definition and imports this module's
    routers, so a module-level import here would close the cycle."""
    from app.core.members import ensure_a_role_manager_remains

    return ensure_a_role_manager_remains


def validate_permissions(permissions: list[str]) -> list[str]:
    """Reject anything not in the code registry — ``role_permissions`` is never free text.

    A scoped permission may only be stored suffixed, and an unscoped one may only be stored
    bare; the wildcard belongs to ``owner`` alone and is not assignable through this API.
    """
    allowed: set[str] = set()
    for spec in all_permissions():
        if spec.scopes:
            allowed.update(f"{spec.key}:{scope}" for scope in spec.scopes)
        else:
            allowed.add(spec.key)
    unknown = sorted(set(permissions) - allowed)
    if unknown:
        raise AppError(
            "validation",
            "errors.validation",
            status_code=422,
            fields={"permissions": "errors.validation"},
        )
    return sorted(set(permissions))


async def _role_or_404(ctx: RequestContext, role_id: uuid.UUID) -> Role:
    role = await ctx.session.scalar(
        select(Role).where(Role.id == role_id, Role.org_id == ctx.org.id)
    )
    if role is None:
        raise AppError("not_found", "errors.not_found", status_code=404)
    return role


def _reject_system_role_change(role: Role) -> None:
    raise AppError("system_role_immutable", "errors.system_role_immutable", status_code=409)


async def _read(ctx: RequestContext, roles: list[Role]) -> list[RoleRead]:
    """Roles with their permissions and member counts — two grouped queries, never 2×N."""
    permissions = await permissions_by_role(ctx.session, ctx.org.id)
    counts = await member_counts(ctx.session, ctx.org.id)
    return [
        RoleRead(
            id=str(role.id),
            key=role.key,
            name_i18n=role.name_i18n,
            description_i18n=role.description_i18n,
            is_system=role.is_system,
            position=role.position,
            permissions=permissions.get(role.id, []),
            member_count=counts.get(role.id, 0),
        )
        for role in roles
    ]


# --------------------------------------------------------------------------- #
# Catalog
# --------------------------------------------------------------------------- #
@permissions_router.get(
    "/catalog",
    response_model=PermissionCatalog,
    dependencies=[
        no_permission_required(
            "the code-defined permission registry; holds no tenant data and the roles UI "
            "renders its matrix from it"
        )
    ],
)
async def permission_catalog(
    _: RequestContext = Depends(require_context),
) -> PermissionCatalog:
    specs = all_permissions()
    groups: list[str] = []
    for spec in specs:
        if spec.module not in groups:
            groups.append(spec.module)
    return PermissionCatalog(
        permissions=[
            PermissionRead(
                key=spec.key,
                scopes=list(spec.scopes),
                label_key=spec.i18n_key,
                group=spec.module,
                position=spec.position,
            )
            for spec in specs
        ],
        groups=groups,
    )


# --------------------------------------------------------------------------- #
# Roles CRUD
# --------------------------------------------------------------------------- #
@roles_router.get(
    "",
    response_model=list[RoleRead],
    dependencies=[require_permission("settings.roles.manage")],
)
async def list_roles(ctx: RequestContext = Depends(require_context)) -> list[RoleRead]:
    roles = (
        await ctx.session.execute(
            select(Role).where(Role.org_id == ctx.org.id).order_by(Role.position, Role.key)
        )
    ).scalars().all()
    return await _read(ctx, list(roles))


@roles_router.post(
    "",
    response_model=RoleRead,
    status_code=201,
    dependencies=[require_permission("settings.roles.manage")],
)
async def create_role(
    payload: RoleCreate,
    source_role_id: uuid.UUID | None = Query(
        None, alias="from", description="Duplicate this role's permissions into the new one."
    ),
    ctx: RequestContext = Depends(require_context),
) -> RoleRead:
    """Create a custom role, optionally seeded from an existing one.

    Duplicating a system role is how the restrictive ``member`` default gets loosened without
    editing the system role itself. Copying ``owner`` copies its *effective* set — the wildcard,
    which is not assignable — so it starts empty and the caller ticks what they mean.
    """
    key = payload.key.strip().lower()
    if not _KEY_RE.fullmatch(key):
        raise AppError(
            "validation",
            "errors.validation",
            status_code=422,
            fields={"key": "errors.validation"},
        )
    existing = await ctx.session.scalar(
        select(Role).where(Role.org_id == ctx.org.id, Role.key == key)
    )
    if existing is not None:
        raise AppError(
            "conflict", "errors.conflict", status_code=409, fields={"key": "errors.conflict"}
        )

    permissions = payload.permissions
    if permissions is None and source_role_id is not None:
        source = await _role_or_404(ctx, source_role_id)
        permissions = [
            p
            for p in (await permissions_by_role(ctx.session, ctx.org.id)).get(source.id, [])
            if p != WILDCARD
        ]
    permissions = validate_permissions(permissions or [])

    role = Role(
        org_id=ctx.org.id,
        key=key,
        name_i18n=payload.name_i18n,
        description_i18n=payload.description_i18n,
        is_system=False,
        position=payload.position,
    )
    ctx.session.add(role)
    await ctx.session.flush()
    await replace_permissions(ctx.session, ctx.org.id, role.id, permissions)
    await audit.record(
        ctx.session,
        org_id=ctx.org.id,
        actor=ctx.user,
        action="role.create",
        role_id=role.id,
        role_key=role.key,
        detail={
            "permissions": permissions,
            "duplicated_from": str(source_role_id) if source_role_id else None,
        },
    )
    return (await _read(ctx, [role]))[0]


@roles_router.patch(
    "/{role_id}",
    response_model=RoleRead,
    dependencies=[require_permission("settings.roles.manage")],
)
async def update_role(
    role_id: uuid.UUID,
    payload: RoleUpdate,
    ctx: RequestContext = Depends(require_context),
) -> RoleRead:
    """Rename, reposition, or replace the whole permission set in one save.

    ``owner`` is the one role whose permissions cannot be edited: it holds ``*`` so that it is
    always possible to fix a mistake made anywhere else. The other system roles are freely
    editable — that is the sanctioned way to loosen the restrictive ``member`` default.
    """
    role = await _role_or_404(ctx, role_id)
    before = (await permissions_by_role(ctx.session, ctx.org.id)).get(role.id, [])

    if payload.permissions is not None:
        if role.key == ROLE_OWNER:
            _reject_system_role_change(role)
        await replace_permissions(
            ctx.session, ctx.org.id, role.id, validate_permissions(payload.permissions)
        )
    if payload.name_i18n is not None:
        role.name_i18n = payload.name_i18n
    if payload.description_i18n is not None:
        role.description_i18n = payload.description_i18n
    if payload.position is not None:
        role.position = payload.position
    await ctx.session.flush()
    await _lockout_guard()(ctx)

    after = (await permissions_by_role(ctx.session, ctx.org.id)).get(role.id, [])
    await audit.record(
        ctx.session,
        org_id=ctx.org.id,
        actor=ctx.user,
        action="role.update",
        role_id=role.id,
        role_key=role.key,
        detail={
            "granted": sorted(set(after) - set(before)),
            "revoked": sorted(set(before) - set(after)),
        },
    )
    return (await _read(ctx, [role]))[0]


@roles_router.delete(
    "/{role_id}",
    status_code=204,
    dependencies=[require_permission("settings.roles.manage")],
)
async def delete_role(
    role_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> None:
    """System roles are not deletable — they are what ``memberships.role`` still collapses to.

    A custom role's ``membership_roles`` rows cascade away, so this can strand a membership with
    no system role. The guard below is what catches the case where it strands the whole org.
    """
    role = await _role_or_404(ctx, role_id)
    if role.is_system:
        _reject_system_role_change(role)
    await _reassert_system_roles_after_delete(ctx, role)
    await ctx.session.delete(role)
    await ctx.session.flush()
    await _lockout_guard()(ctx)
    await audit.record(
        ctx.session,
        org_id=ctx.org.id,
        actor=ctx.user,
        action="role.delete",
        role_id=role.id,
        role_key=role.key,
    )


async def _reassert_system_roles_after_delete(ctx: RequestContext, role: Role) -> None:
    """Deleting a role must not leave a membership holding no roles at all (issue #56).

    Custom-role-only memberships are legal since the legacy column dropped; *zero* roles is
    not — that member would authenticate into a wall of 403s with no visible reason.
    """
    orphans = (
        await ctx.session.execute(
            select(MembershipRole.membership_id).where(
                MembershipRole.org_id == ctx.org.id, MembershipRole.role_id == role.id
            )
        )
    ).scalars().all()
    if not orphans:
        return
    survivors = (
        await ctx.session.execute(
            select(MembershipRole.membership_id)
            .where(
                MembershipRole.org_id == ctx.org.id,
                MembershipRole.membership_id.in_(orphans),
                MembershipRole.role_id != role.id,
            )
        )
    ).scalars().all()
    if set(orphans) - set(survivors):
        raise AppError("role_required", "errors.role_required", status_code=409)
