"""Team / user management for the current org (CLAUDE.md §5, §9).

Members are ``memberships`` (a global ``user`` linked to the org with a ``role``). This is a
manager-only surface: list the team, invite by email, change a role, or revoke access. All
queries are tenant-scoped (RLS + explicit ``org_id``); an invite creates the global user if
needed. No SMTP in P0, so the invite is logged — the user sets a password via forgot-password.
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


async def _owner_count(ctx: RequestContext) -> int:
    return int(
        await ctx.session.scalar(
            select(func.count())
            .select_from(Membership)
            .where(Membership.org_id == ctx.org.id, Membership.role == Role.OWNER.value)
        )
        or 0
    )


@router.get("", response_model=list[MemberRead])
async def list_members(ctx: RequestContext = Depends(require_context)) -> list[MemberRead]:
    ctx.ensure_can_manage()
    rows = (
        await ctx.session.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.org_id == ctx.org.id)
            .order_by(User.email.asc())
        )
    ).all()
    return [_member_read(ctx, m, u) for m, u in rows]


@router.post("/invite", response_model=MemberRead, status_code=201)
async def invite_member(
    payload: MemberInvite, ctx: RequestContext = Depends(require_context)
) -> MemberRead:
    ctx.ensure_can_manage()
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

    membership = Membership(org_id=ctx.org.id, user_id=user.id, role=payload.role.value)
    ctx.session.add(membership)
    await ctx.session.flush()
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


@router.patch("/{membership_id}", response_model=MemberRead)
async def update_member_role(
    membership_id: uuid.UUID,
    payload: MemberRoleUpdate,
    ctx: RequestContext = Depends(require_context),
) -> MemberRead:
    ctx.ensure_can_manage()
    membership = await _membership_or_404(ctx, membership_id)
    # Never leave the org without an owner.
    if (
        membership.role == Role.OWNER.value
        and payload.role != Role.OWNER
        and await _owner_count(ctx) <= 1
    ):
        raise AppError("last_owner", "errors.last_owner", status_code=400)
    membership.role = payload.role.value
    await ctx.session.flush()
    user = await ctx.session.get(User, membership.user_id)
    return _member_read(ctx, membership, user)  # type: ignore[arg-type]


@router.delete("/{membership_id}", status_code=204)
async def revoke_member(
    membership_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    ctx.ensure_can_manage()
    membership = await _membership_or_404(ctx, membership_id)
    if membership.user_id == ctx.user.id:
        raise AppError("cannot_remove_self", "errors.cannot_remove_self", status_code=400)
    if membership.role == Role.OWNER.value and await _owner_count(ctx) <= 1:
        raise AppError("last_owner", "errors.last_owner", status_code=400)
    await ctx.session.delete(membership)
    await ctx.session.flush()
