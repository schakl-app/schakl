"""Team / user management for the current org (CLAUDE.md §5, §9).

Members are ``memberships`` (a global ``user`` linked to the org). This is a manager-only
surface: list the team, invite by email, change a role, or revoke access. All queries are
tenant-scoped (RLS + explicit ``org_id``); an invite creates the global user if needed. No SMTP
in P0, so the invite is logged — the user sets a password via forgot-password.

Authorization is roles → permissions (issue #19). ``membership_roles`` is authoritative; the
``memberships.role`` column is dual-written with the membership's highest-privilege system role,
so rolling the image back to the previous release lands old code on a value it can still parse.
"""

from __future__ import annotations

import logging
import secrets
import uuid

from fastapi import APIRouter, Depends
from pwdlib import PasswordHash
from pydantic import BaseModel, EmailStr
from sqlalchemy import func, select

from app.core.auth.models import User
from app.core.models import Membership
from app.core.permissions.deps import no_permission_required, require_permission
from app.core.permissions.models import MembershipRole
from app.core.permissions.models import Role as RoleRow
from app.core.permissions.service import create_membership, role_by_key, role_manager_count
from app.core.roles import Role
from app.core.tenancy import RequestContext, require_context
from app.errors import AppError

logger = logging.getLogger("vlotr.members")
_password_hash = PasswordHash.recommended()

router = APIRouter(prefix="/members", tags=["members"])


class MemberRead(BaseModel):
    membership_id: str
    user_id: str
    email: str
    full_name: str | None
    role: str
    is_active: bool
    is_self: bool


class MemberLookup(BaseModel):
    """Minimal member identity for pickers (assignee, approver) — safe for any staff role."""

    user_id: str
    full_name: str | None
    email: str


class MemberInvite(BaseModel):
    email: EmailStr
    full_name: str | None = None
    role: Role = Role.MEMBER


class MemberRoleUpdate(BaseModel):
    role: Role


def _member_read(ctx: RequestContext, membership: Membership, user: User) -> MemberRead:
    return MemberRead(
        membership_id=str(membership.id),
        user_id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        role=membership.role,
        is_active=user.is_active,
        is_self=user.id == ctx.user.id,
    )


async def ensure_a_role_manager_remains(ctx: RequestContext) -> None:
    """Reject any mutation that would leave nobody able to administer roles.

    Called **after** the mutation is flushed, so it sees the world the caller is proposing; the
    ``AppError`` unwinds ``require_context``, which rolls the transaction back. This replaces the
    old "never demote the last owner" rule: the moment ``membership_roles`` decides who may do
    what, counting ``memberships.role == 'owner'`` answers the wrong question — an org whose last
    owner becomes an admin has lost nothing, and an org whose last admin becomes a member has lost
    everything.
    """
    if await role_manager_count(ctx.session, ctx.org.id) == 0:
        raise AppError("last_role_manager", "errors.last_role_manager", status_code=409)


@router.get(
    "",
    response_model=list[MemberRead],
    dependencies=[require_permission("members.member.read")],
)
async def list_members(ctx: RequestContext = Depends(require_context)) -> list[MemberRead]:
    rows = (
        await ctx.session.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.org_id == ctx.org.id)
            .order_by(User.email.asc())
        )
    ).all()
    return [_member_read(ctx, m, u) for m, u in rows]


@router.get(
    "/lookup",
    response_model=list[MemberLookup],
    dependencies=[
        no_permission_required("name/email of colleagues, for pickers; open to every member")
    ],
)
async def lookup_members(ctx: RequestContext = Depends(require_context)) -> list[MemberLookup]:
    """Name/email of every org member, for assignee pickers. Open to all staff roles."""
    rows = (
        await ctx.session.execute(
            select(User)
            .join(Membership, Membership.user_id == User.id)
            .where(Membership.org_id == ctx.org.id)
            .order_by(User.full_name.asc().nulls_last(), User.email.asc())
        )
    ).scalars().all()
    return [
        MemberLookup(user_id=str(u.id), full_name=u.full_name, email=u.email) for u in rows
    ]


@router.post(
    "/invite",
    response_model=MemberRead,
    status_code=201,
    dependencies=[require_permission("members.member.write")],
)
async def invite_member(
    payload: MemberInvite, ctx: RequestContext = Depends(require_context)
) -> MemberRead:
    email = payload.email.lower()

    user = await ctx.session.scalar(select(User).where(func.lower(User.email) == email))
    if user is None:
        # Create the global identity with an unusable random password; they set one via
        # forgot-password (token logged in P0 — no SMTP yet).
        user = User(
            id=uuid.uuid4(),
            email=email,
            full_name=payload.full_name,
            hashed_password=_password_hash.hash(secrets.token_urlsafe(24)),
            is_active=True,
            is_verified=False,
        )
        ctx.session.add(user)
        await ctx.session.flush()
    elif payload.full_name and not user.full_name:
        user.full_name = payload.full_name

    existing = await ctx.session.scalar(
        select(Membership).where(
            Membership.org_id == ctx.org.id, Membership.user_id == user.id
        )
    )
    if existing is not None:
        raise AppError("conflict", "errors.conflict", status_code=409)

    membership = await create_membership(ctx.session, ctx.org.id, user.id, payload.role.value)
    logger.info("Invited %s to org %s as %s", email, ctx.org.slug, payload.role.value)
    return _member_read(ctx, membership, user)


async def _membership_or_404(ctx: RequestContext, membership_id: uuid.UUID) -> Membership:
    membership = await ctx.session.scalar(
        select(Membership).where(
            Membership.id == membership_id, Membership.org_id == ctx.org.id
        )
    )
    if membership is None:
        raise AppError("not_found", "errors.not_found", status_code=404)
    return membership


@router.patch(
    "/{membership_id}",
    response_model=MemberRead,
    dependencies=[require_permission("members.member.write")],
)
async def update_member_role(
    membership_id: uuid.UUID,
    payload: MemberRoleUpdate,
    ctx: RequestContext = Depends(require_context),
) -> MemberRead:
    """Swap this membership's **system** role; any custom roles it also holds are untouched."""
    membership = await _membership_or_404(ctx, membership_id)
    target = await role_by_key(ctx.session, ctx.org.id, payload.role.value)
    if target is None:
        raise AppError("not_found", "errors.not_found", status_code=404)

    held_system_links = (
        await ctx.session.execute(
            select(MembershipRole)
            .join(RoleRow, RoleRow.id == MembershipRole.role_id)
            .where(
                MembershipRole.org_id == ctx.org.id,
                MembershipRole.membership_id == membership.id,
                RoleRow.is_system.is_(True),
            )
        )
    ).scalars().all()
    if all(link.role_id != target.id for link in held_system_links):
        for link in held_system_links:
            await ctx.session.delete(link)
        ctx.session.add(
            MembershipRole(org_id=ctx.org.id, membership_id=membership.id, role_id=target.id)
        )
    membership.role = payload.role.value  # dual write, release N only
    await ctx.session.flush()
    await ensure_a_role_manager_remains(ctx)

    user = await ctx.session.get(User, membership.user_id)
    return _member_read(ctx, membership, user)  # type: ignore[arg-type]


@router.delete(
    "/{membership_id}",
    status_code=204,
    dependencies=[require_permission("members.member.write")],
)
async def revoke_member(
    membership_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    membership = await _membership_or_404(ctx, membership_id)
    if membership.user_id == ctx.user.id:
        raise AppError("cannot_remove_self", "errors.cannot_remove_self", status_code=400)
    await ctx.session.delete(membership)
    await ctx.session.flush()
    await ensure_a_role_manager_remains(ctx)
