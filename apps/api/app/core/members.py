"""Team / user management for the current org (CLAUDE.md §5, §9).

Members are ``memberships`` (a global ``user`` linked to the org). This is a manager-only
surface: list the team, invite by email, change a role, or revoke access. All queries are
tenant-scoped (RLS + explicit ``org_id``); an invite creates the global user if needed. No SMTP
in P0, so the invite is logged — the user sets a password via forgot-password.

Authorization is roles → permissions (issue #19). ``membership_roles`` is authoritative;
the legacy ``memberships.role`` column is gone (issue #56),
so rolling the image back to the previous release lands old code on a value it can still parse.
"""

from __future__ import annotations

import logging
import secrets
import uuid

from fastapi import APIRouter, Depends, Query, Request
from pwdlib import PasswordHash
from pydantic import BaseModel, EmailStr
from sqlalchemy import func, select

from app.core.auth import twofactor
from app.core.auth.models import User
from app.core.auth.users import get_user_manager
from app.core.email.service import email_configured
from app.core.models import Membership
from app.core.permissions import audit
from app.core.permissions.catalog import PRIVILEGE_ORDER, ROLE_OWNER, permission_keys
from app.core.permissions.deps import no_permission_required, require_permission
from app.core.permissions.models import MembershipRole
from app.core.permissions.models import Role as RoleRow
from app.core.permissions.schemas import EffectivePermissions, MembershipRolesUpdate
from app.core.permissions.service import (
    collapse_to_legacy_role,
    create_membership,
    effective_permissions,
    membership_role_ids,
    permission_holder_ids,
    role_by_key,
    role_manager_count,
    set_membership_roles,
)
from app.core.tenancy import RequestContext, require_context
from app.errors import AppError

logger = logging.getLogger("schakl.members")
_password_hash = PasswordHash.recommended()

router = APIRouter(prefix="/members", tags=["members"])


def effective_avatar_url(user: User) -> str | None:
    """#122's one precedence rule: personal override → OIDC picture → None (initials)."""
    return user.custom_avatar_url or user.oidc_avatar_url or None


class MemberRead(BaseModel):
    membership_id: str
    user_id: str
    email: str
    full_name: str | None
    avatar_url: str | None = None
    #: Every role this membership holds. The Users screen derives the effective permission set
    #: from these plus ``GET /roles`` — one grouped query here, never one per member.
    role_ids: list[str] = []
    is_active: bool
    is_self: bool
    #: The member's account demands a second factor at login — what makes the admin's
    #: "reset 2FA" action (a lost-phone escape hatch) appear only where it means something.
    two_factor_enabled: bool = False
    #: Set only on the invite response (#161): whether the welcome mail went out, and the
    #: i18n key saying why not (e.g. no transport configured) so the admin knows to act.
    invite_email_sent: bool | None = None
    invite_email_error: str | None = None


class MemberLookup(BaseModel):
    """Minimal member identity for pickers (assignee, approver) — safe for any staff role."""

    user_id: str
    full_name: str | None
    email: str
    avatar_url: str | None = None


class MemberInvite(BaseModel):
    email: EmailStr
    full_name: str | None = None
    #: A system role key (owner/admin/member/client); custom roles are assigned afterwards.
    role: str = "member"
    #: Send the welcome mail with a set-password link (#161). Off = the admin distributes
    #: credentials themselves (the new user can still use "wachtwoord vergeten").
    send_email: bool = True


class MemberRoleUpdate(BaseModel):
    role: str


def _system_role_key_or_422(key: str) -> str:
    if key not in PRIVILEGE_ORDER:
        raise AppError(
            "validation",
            "errors.validation",
            status_code=422,
            fields={"role": "errors.validation"},
        )
    return key


def _guard_owner_grant(ctx: RequestContext, role_key: str) -> None:
    """Conferring the ``owner`` role requires role-administration power (audit F2).

    ``owner`` is the sole role that stores ``*`` (full control). ``update_member_role`` and
    ``invite_member`` are gated on ``members.member.write`` — *team* management, deliberately a
    tier below the ``settings.roles.manage`` role machinery. Without this guard a holder of a
    custom role carrying only ``members.member.write`` (a natural "office manager" grant) could
    assign ``owner`` to themselves or an accomplice and escalate straight to the wildcard. Require
    the role-administration capability specifically for the ``owner`` step, so team-management
    alone can no longer mint an owner. A role manager (or owner) designating an owner stays legal —
    that is intended and covered by ``test_change_role_and_last_role_manager_guard``.
    """
    if role_key == ROLE_OWNER and not ctx.can("settings.roles.manage"):
        raise AppError("forbidden", "errors.forbidden", status_code=403)


def _member_read(
    ctx: RequestContext,
    membership: Membership,
    user: User,
    role_ids: list[uuid.UUID] | None = None,
    two_factor_enabled: bool = False,
) -> MemberRead:
    return MemberRead(
        membership_id=str(membership.id),
        user_id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        avatar_url=effective_avatar_url(user),
        role_ids=[str(role_id) for role_id in role_ids or []],
        is_active=user.is_active,
        is_self=user.id == ctx.user.id,
        two_factor_enabled=two_factor_enabled,
    )


async def ensure_a_role_manager_remains(ctx: RequestContext) -> None:
    """Reject any mutation that would leave nobody able to administer roles.

    Called **after** the mutation is flushed, so it sees the world the caller is proposing; the
    ``AppError`` unwinds ``require_context``, which rolls the transaction back. This replaces the
    old "never demote the last owner" rule: the moment ``membership_roles`` decides who may do
    what, counting ``memberships.role == 'owner'`` answers the wrong question — an org whose last
    owner becomes an admin has lost nothing, and an org whose last admin becomes a member has lost
    everything.

    Four mutation shapes reach it; the other two (delete a role, untick the permission) live in
    ``app/core/permissions/router.py`` and call the same function.
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
    # One grouped query for the whole team, not one per member (docs/PERFORMANCE.md).
    held: dict[uuid.UUID, list[uuid.UUID]] = {}
    for membership_id, role_id in await ctx.session.execute(
        select(MembershipRole.membership_id, MembershipRole.role_id).where(
            MembershipRole.org_id == ctx.org.id
        )
    ):
        held.setdefault(membership_id, []).append(role_id)
    # Same rule for 2FA state: one grouped query over the team's user ids (a confirmed row per
    # user), not a lookup per member.
    user_ids = [u.id for _, u in rows]
    secured: set[uuid.UUID] = set(
        (
            await ctx.session.execute(
                select(twofactor.UserTwoFactor.user_id).where(
                    twofactor.UserTwoFactor.user_id.in_(user_ids or [uuid.uuid4()]),
                    twofactor.UserTwoFactor.confirmed_at.is_not(None),
                )
            )
        ).scalars()
    )
    return [
        _member_read(ctx, m, u, held.get(m.id, []), two_factor_enabled=u.id in secured)
        for m, u in rows
    ]


@router.get(
    "/lookup",
    response_model=list[MemberLookup],
    dependencies=[
        no_permission_required("name/email of colleagues, for pickers; open to every member")
    ],
)
async def lookup_members(
    permission: str | None = Query(
        None,
        description=(
            "Only members who hold this permission at some scope — e.g. `tasks.task.write` for "
            "an assignee picker, `leave.request.approve` for an approver picker. Omit for "
            "everyone in the org."
        ),
    ),
    ctx: RequestContext = Depends(require_context),
) -> list[MemberLookup]:
    """Name/email of org members, for assignee/approver pickers. Open to every member.

    Filtering by ``permission`` is what stops a picker from offering people who could never do
    the thing being picked. It is one indexed, ``DISTINCT`` query: a user holding two granting
    roles must not appear twice.
    """
    stmt = (
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.org_id == ctx.org.id)
        .order_by(User.full_name.asc().nulls_last(), User.email.asc())
    )
    if permission is not None:
        if permission not in set(permission_keys()):
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"permission": "errors.validation"},
            )
        stmt = stmt.where(User.id.in_(permission_holder_ids(ctx.org.id, permission)))

    rows = (await ctx.session.execute(stmt)).scalars().all()
    return [
        MemberLookup(
            user_id=str(u.id),
            full_name=u.full_name,
            email=u.email,
            avatar_url=effective_avatar_url(u),
        )
        for u in rows
    ]


@router.post(
    "/invite",
    response_model=MemberRead,
    status_code=201,
    dependencies=[require_permission("members.member.write")],
)
async def invite_member(
    payload: MemberInvite,
    request: Request,
    ctx: RequestContext = Depends(require_context),
    user_manager=Depends(get_user_manager),  # noqa: ANN001 — FastAPI Users' provider
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

    role_key = _system_role_key_or_422(payload.role)
    _guard_owner_grant(ctx, role_key)
    membership = await create_membership(ctx.session, ctx.org.id, user.id, role_key)
    logger.info("Invited %s to org %s as %s", email, ctx.org.slug, role_key)
    await audit.record(
        ctx.session,
        org_id=ctx.org.id,
        actor=ctx.user,
        action="membership.invited",
        role_key=role_key,
        target_user_id=user.id,
    )
    member = _member_read(ctx, membership, user)
    if payload.send_email:
        # The welcome mail is a set-password link riding the reset-token flow (#161). A
        # missing transport is reported, never silently swallowed — the settings hint that
        # pointed at a flow that didn't exist is exactly the failure mode to avoid.
        # The instance-provided transport counts as configured (epic #199) — the send seam
        # falls back to it for an org without its own row.
        if not await email_configured(ctx.session, ctx.org.id):
            member.invite_email_sent = False
            member.invite_email_error = "errors.email_not_configured"
        else:
            request.state.password_email_kind = "invite"
            try:
                await user_manager.forgot_password(user, request)
                sent, send_error = getattr(
                    request.state, "password_email_result", (True, None)
                )
                member.invite_email_sent = sent
                member.invite_email_error = send_error
            except Exception:  # noqa: BLE001 — the invite itself must stand
                logger.exception("Invite email for %s failed", email)
                member.invite_email_sent = False
    return member


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
    role_key = _system_role_key_or_422(payload.role)
    _guard_owner_grant(ctx, role_key)
    target = await role_by_key(ctx.session, ctx.org.id, role_key)
    if target is None:
        raise AppError("not_found", "errors.not_found", status_code=404)

    held_system_links_with_keys = list(
        await ctx.session.execute(
            select(MembershipRole, RoleRow.key)
            .join(RoleRow, RoleRow.id == MembershipRole.role_id)
            .where(
                MembershipRole.org_id == ctx.org.id,
                MembershipRole.membership_id == membership.id,
                RoleRow.is_system.is_(True),
            )
        )
    )
    if all(link.role_id != target.id for link, _ in held_system_links_with_keys):
        for link, _ in held_system_links_with_keys:
            await ctx.session.delete(link)
        ctx.session.add(
            MembershipRole(org_id=ctx.org.id, membership_id=membership.id, role_id=target.id)
        )
    # The audit's "from" value: the highest-privilege system role they held (display only).
    previous = collapse_to_legacy_role(
        [link_key for _, link_key in held_system_links_with_keys]
    )
    await ctx.session.flush()
    await ensure_a_role_manager_remains(ctx)
    if previous != target.key:
        await audit.record(
            ctx.session,
            org_id=ctx.org.id,
            actor=ctx.user,
            action="membership.roles_changed",
            role_id=target.id,
            role_key=target.key,
            target_user_id=membership.user_id,
            detail={"from": previous, "to": target.key},
        )

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
    await audit.record(
        ctx.session,
        org_id=ctx.org.id,
        actor=ctx.user,
        action="membership.revoked",
        target_user_id=membership.user_id,
    )


@router.put(
    "/{membership_id}/roles",
    response_model=EffectivePermissions,
    dependencies=[require_permission("settings.roles.manage")],
)
async def set_member_roles(
    membership_id: uuid.UUID,
    payload: MembershipRolesUpdate,
    ctx: RequestContext = Depends(require_context),
) -> EffectivePermissions:
    """Replace a membership's whole role set in one save. A user may hold several roles.

    Custom-role-only memberships are legal since the legacy column dropped (issue #56); an empty
    set is still refused — a membership holding nothing would authenticate into a wall of 403s.
    """
    membership = await _membership_or_404(ctx, membership_id)
    role_ids = [uuid.UUID(value) for value in payload.role_ids]
    roles = (
        await ctx.session.execute(
            select(RoleRow).where(
                RoleRow.org_id == ctx.org.id, RoleRow.id.in_(role_ids or [uuid.uuid4()])
            )
        )
    ).scalars().all()
    if len(roles) != len(set(role_ids)):
        raise AppError("not_found", "errors.not_found", status_code=404)
    if not roles:
        raise AppError("validation", "errors.validation", status_code=422)

    before = set(await membership_role_ids(ctx.session, ctx.org.id, membership.id))
    await set_membership_roles(ctx.session, ctx.org.id, membership, role_ids)
    await ensure_a_role_manager_remains(ctx)
    await audit.record(
        ctx.session,
        org_id=ctx.org.id,
        actor=ctx.user,
        action="membership.roles_changed",
        target_user_id=membership.user_id,
        detail={
            "added": sorted(str(r) for r in set(role_ids) - before),
            "removed": sorted(str(r) for r in before - set(role_ids)),
        },
    )
    return await _effective(ctx, membership)


@router.delete(
    "/{membership_id}/two-factor",
    status_code=204,
    dependencies=[require_permission("members.member.write")],
)
async def reset_member_two_factor(
    membership_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    """Reset a member's 2FA — the lost-phone escape hatch (docs/TWOFACTOR.md).

    Deletes the enrollment outright (secret, backup codes, SMS number), so the account is a
    plain password login again until the member re-enrolls; no secret is ever *read*. The user
    identity is global (§5), so this genuinely clears their 2FA everywhere — but the reach is
    tenant-scoped where it matters: the target is addressed by *membership*, and an admin of
    another org has no membership id of theirs to name (404). Audited, like every trust change.
    """
    membership = await _membership_or_404(ctx, membership_id)
    row = await twofactor.row_for(ctx.session, membership.user_id)
    if row is None:
        raise AppError("not_found", "errors.not_found", status_code=404)
    await ctx.session.delete(row)
    await audit.record(
        ctx.session,
        org_id=ctx.org.id,
        actor=ctx.user,
        action="membership.two_factor_reset",
        target_user_id=membership.user_id,
    )


@router.get(
    "/{membership_id}/permissions",
    response_model=EffectivePermissions,
    dependencies=[require_permission("members.member.read")],
)
async def member_permissions(
    membership_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> EffectivePermissions:
    """A member's effective permissions — the union over every role they hold.

    Your *own* set arrives with ``/meta/me``; this is the manager's view of somebody else's.
    """
    return await _effective(ctx, await _membership_or_404(ctx, membership_id))


async def _effective(ctx: RequestContext, membership: Membership) -> EffectivePermissions:
    role_ids = await membership_role_ids(ctx.session, ctx.org.id, membership.id)
    permissions = await effective_permissions(ctx.session, ctx.org.id, membership.id)
    return EffectivePermissions(
        membership_id=str(membership.id),
        user_id=str(membership.user_id),
        role_ids=[str(role_id) for role_id in role_ids],
        permissions=permissions.keys(),
    )
