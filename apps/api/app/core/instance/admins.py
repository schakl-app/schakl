"""Managing who has instance access (issue #26): ``/api/v1/instance/admins``.

**Owner-only, and deliberately not a capability.** An admin who could grant
``instance.impersonate`` to themselves is an owner with extra steps, so the escalation edge is
removed rather than guarded — every route here demands ``users.is_superuser``, and a delegated
admin gets 403 (not 404: they are legitimately on this surface and simply may not do this).

Two principals are managed from one screen, because from the operator's point of view they are
one question — "who can touch this platform, and how much":

* promoting/demoting an **owner** flips ``users.is_superuser``;
* granting an **admin** writes an ``instance_admins`` row with an explicit capability set.

The invite path mirrors ``app.core.members.invite_member``: an account is created with an
unusable random password when the email is new, and the person sets one through
forgot-password. That matters on cloud, where the apex host has no org and a support hire has
no user record anywhere yet.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pwdlib import PasswordHash
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select

from app.core.auth.models import User
from app.core.instance import audit
from app.core.instance.capabilities import CATALOG, validate
from app.core.instance.guard import (
    InstanceContext,
    no_capability_required,
    require_instance_owner_principal,
)
from app.core.models import InstanceAdmin
from app.core.permissions.deps import no_permission_required
from app.errors import AppError

logger = logging.getLogger(__name__)
_password_hash = PasswordHash.recommended()

router = APIRouter(
    prefix="/instance/admins",
    tags=["instance"],
    # Exempt on the *org* permission axis for the same reason as the rest of /instance: this
    # surface is gated on the instance principal, which is deliberately not expressible as a
    # membership permission (CLAUDE.md §5, §15). The instance-side deny-by-default rule still
    # applies and is enforced by tests/test_instance_deny_by_default.py.
    dependencies=[
        no_permission_required("instance administration: gated on the instance principal")
    ],
)


class CapabilityInfo(BaseModel):
    key: str
    label_key: str
    group: str
    sensitive: bool


class InstancePrincipal(BaseModel):
    user_id: str
    email: str
    full_name: str | None
    is_active: bool
    #: True for ``users.is_superuser``: holds everything, and may manage this screen.
    is_owner: bool
    #: Owner-expanded, so the console renders one list and never has to know the rule.
    capabilities: list[str]
    granted_by_email: str | None = None
    granted_at: datetime | None = None


class AdminsPage(BaseModel):
    #: The code-defined catalog, so the console renders checkboxes without hardcoding keys.
    catalog: list[CapabilityInfo]
    principals: list[InstancePrincipal]


class AdminInvite(BaseModel):
    email: EmailStr
    full_name: str | None = Field(default=None, max_length=255)
    #: Empty is valid and is the default: a half-finished invite must never over-grant.
    capabilities: list[str] = Field(default_factory=list)


class AdminUpdate(BaseModel):
    capabilities: list[str] | None = None
    #: Promote to / demote from the owner principal. None leaves it alone.
    is_owner: bool | None = None


async def ensure_an_instance_owner_remains(ctx: InstanceContext) -> None:
    """Reject any mutation that would leave the instance with no owner.

    Called **after** the mutation is flushed, so it sees the world the caller is proposing; the
    ``AppError`` unwinds ``require_instance_admin``, which rolls the transaction back. Mirrors
    ``ensure_a_role_manager_remains`` in ``app/core/members.py`` for exactly the same reason: a
    box nobody can administer is unrecoverable without database access, and a delegated admin
    cannot promote anyone.

    Counts **active** owners: deactivating the last one locks the instance just as surely as
    demoting them.
    """
    remaining = await ctx.session.scalar(
        select(func.count())
        .select_from(User)
        .where(User.is_superuser.is_(True), User.is_active.is_(True))
    )
    if not remaining:
        raise AppError("last_instance_owner", "errors.last_instance_owner", status_code=409)


def _principal(user: User, row: InstanceAdmin | None) -> InstancePrincipal:
    from app.core.instance.capabilities import CAPABILITY_KEYS

    return InstancePrincipal(
        user_id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
        is_owner=user.is_superuser,
        capabilities=(
            sorted(CAPABILITY_KEYS)
            if user.is_superuser
            else sorted(row.capabilities if row else [])
        ),
        granted_by_email=row.granted_by_email if row else None,
        granted_at=row.created_at if row else None,
    )


@router.get(
    "",
    response_model=AdminsPage,
    dependencies=[
        no_capability_required(
            "owner-only by a stronger gate than a capability: require_instance_owner_principal. "
            "Delegation is not delegable (see this module's docstring)."
        )
    ],
)
async def list_admins(
    ctx: InstanceContext = Depends(require_instance_owner_principal),
) -> AdminsPage:
    owners = (
        (await ctx.session.execute(select(User).where(User.is_superuser.is_(True))))
        .scalars()
        .all()
    )
    rows = (await ctx.session.execute(select(InstanceAdmin))).scalars().all()
    by_user = {row.user_id: row for row in rows}
    admins = (
        (await ctx.session.execute(select(User).where(User.id.in_(by_user.keys()))))
        .scalars()
        .all()
        if by_user
        else []
    )
    principals = [_principal(u, None) for u in owners] + [
        _principal(u, by_user.get(u.id)) for u in admins if not u.is_superuser
    ]
    principals.sort(key=lambda p: (not p.is_owner, p.email))
    return AdminsPage(
        catalog=[
            CapabilityInfo(
                key=s.key, label_key=s.label_key, group=s.group, sensitive=s.sensitive
            )
            for s in CATALOG
        ],
        principals=principals,
    )


@router.post(
    "",
    response_model=InstancePrincipal,
    status_code=201,
    dependencies=[
        no_capability_required(
            "owner-only by a stronger gate than a capability: require_instance_owner_principal. "
            "Delegation is not delegable (see this module's docstring)."
        )
    ],
)
async def invite_admin(
    payload: AdminInvite, ctx: InstanceContext = Depends(require_instance_owner_principal)
) -> InstancePrincipal:
    email = payload.email.lower()
    capabilities = validate(payload.capabilities)

    user = await ctx.session.scalar(select(User).where(func.lower(User.email) == email))
    if user is None:
        # The global identity, with an unusable password: they set one via forgot-password,
        # exactly like an invited org member. Never is_superuser — promoting is a second,
        # explicit act, so a typo'd invite cannot mint an owner.
        user = User(
            id=uuid.uuid4(),
            email=email,
            full_name=payload.full_name,
            hashed_password=_password_hash.hash(secrets.token_urlsafe(24)),
            is_active=True,
            is_verified=False,
            is_superuser=False,
        )
        ctx.session.add(user)
        await ctx.session.flush()
    elif payload.full_name and not user.full_name:
        user.full_name = payload.full_name

    if await ctx.session.scalar(
        select(InstanceAdmin).where(InstanceAdmin.user_id == user.id)
    ):
        raise AppError("conflict", "errors.conflict", status_code=409)

    ctx.session.add(
        InstanceAdmin(
            user_id=user.id,
            capabilities=capabilities,
            granted_by_user_id=ctx.user.id,
            granted_by_email=ctx.user.email,
        )
    )
    await ctx.session.flush()
    await audit.record(
        ctx.session,
        actor=ctx.user,
        action="instance_admin.grant",
        target_user_id=user.id,
        detail={"email": email, "capabilities": capabilities},
    )
    logger.info("Granted instance admin to %s (%s)", email, ",".join(capabilities) or "nothing")
    row = await ctx.session.scalar(
        select(InstanceAdmin).where(InstanceAdmin.user_id == user.id)
    )
    return _principal(user, row)


@router.patch(
    "/{user_id}",
    response_model=InstancePrincipal,
    dependencies=[
        no_capability_required(
            "owner-only by a stronger gate than a capability: require_instance_owner_principal. "
            "Delegation is not delegable (see this module's docstring)."
        )
    ],
)
async def update_admin(
    user_id: uuid.UUID,
    payload: AdminUpdate,
    ctx: InstanceContext = Depends(require_instance_owner_principal),
) -> InstancePrincipal:
    user = await ctx.session.get(User, user_id)
    if user is None:
        raise AppError("not_found", "errors.not_found", status_code=404)
    row = await ctx.session.scalar(
        select(InstanceAdmin).where(InstanceAdmin.user_id == user_id)
    )
    detail: dict = {"email": user.email}

    if payload.is_owner is not None and payload.is_owner != user.is_superuser:
        detail["is_owner"] = {"from": user.is_superuser, "to": payload.is_owner}
        user.is_superuser = payload.is_owner
        if payload.is_owner and row is not None:
            # An owner holds everything implicitly; keeping a capability row beside it would
            # be a second, contradictory source of truth for the same person.
            await ctx.session.delete(row)
            row = None

    if payload.capabilities is not None and not user.is_superuser:
        capabilities = validate(payload.capabilities)
        detail["capabilities"] = {
            "from": sorted(row.capabilities) if row else [], "to": capabilities
        }
        if row is None:
            row = InstanceAdmin(
                user_id=user.id,
                capabilities=capabilities,
                granted_by_user_id=ctx.user.id,
                granted_by_email=ctx.user.email,
            )
            ctx.session.add(row)
        else:
            row.capabilities = capabilities

    await ctx.session.flush()
    await ensure_an_instance_owner_remains(ctx)
    await audit.record(
        ctx.session, actor=ctx.user, action="instance_admin.update",
        target_user_id=user.id, detail=detail,
    )
    return _principal(user, row)


@router.delete(
    "/{user_id}",
    status_code=204,
    dependencies=[
        no_capability_required(
            "owner-only by a stronger gate than a capability: require_instance_owner_principal. "
            "Delegation is not delegable (see this module's docstring)."
        )
    ],
)
async def revoke_admin(
    user_id: uuid.UUID, ctx: InstanceContext = Depends(require_instance_owner_principal)
) -> None:
    """Remove all instance access: the capability row, and the owner flag if they had it.

    This is the immediate lever against a live impersonation grant, which the signed token
    outlives by design (see ``impersonation.read_impersonation``).
    """
    user = await ctx.session.get(User, user_id)
    if user is None:
        raise AppError("not_found", "errors.not_found", status_code=404)
    row = await ctx.session.scalar(
        select(InstanceAdmin).where(InstanceAdmin.user_id == user_id)
    )
    if row is None and not user.is_superuser:
        raise AppError("not_found", "errors.not_found", status_code=404)

    was_owner = user.is_superuser
    if row is not None:
        await ctx.session.delete(row)
    user.is_superuser = False
    await ctx.session.flush()
    await ensure_an_instance_owner_remains(ctx)
    await audit.record(
        ctx.session, actor=ctx.user, action="instance_admin.revoke",
        target_user_id=user.id, detail={"email": user.email, "was_owner": was_owner},
    )
