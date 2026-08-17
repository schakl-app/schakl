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
from datetime import UTC, datetime

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
from app.core.permissions.catalog import (
    PRIVILEGE_ORDER,
    ROLE_CLIENT,
    ROLE_OWNER,
    permission_keys,
)
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
from app.core.portal import portal_user_ids
from app.core.scope import resolve_company_scope_details
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
    #: The conjunction ``account_active`` documents: still a member here *and* not disabled
    #: instance-wide. A screen asks one question and gets one answer.
    is_active: bool
    #: When they were taken off the team, for the roster's "Gedeactiveerd op …". ``None`` for an
    #: account that is active, **and** for one disabled through ``users.is_active`` alone — those
    #: two are different facts and an admin reading the row should be able to tell them apart.
    deactivated_at: datetime | None = None
    is_self: bool
    #: The member's account demands a second factor at login — what makes the admin's
    #: "reset 2FA" action (a lost-phone escape hatch) appear only where it means something.
    two_factor_enabled: bool = False
    #: A ``client``-role membership whose company horizon resolves to **nothing** (#274): they
    #: can see no company, so every company-scoped read and write answers 404 no matter which
    #: permissions their role carries. Without this the admin's only feedback is the customer
    #: saying "not found", and granting more permissions looks like the fix. ``False`` for
    #: staff — the horizon floor is the client role's alone (#252).
    company_scope_empty: bool = False
    #: Set only on the invite response (#161): whether the welcome mail went out, and the
    #: i18n key saying why not (e.g. no transport configured) so the admin knows to act.
    invite_email_sent: bool | None = None
    invite_email_error: str | None = None


class MemberLookup(BaseModel):
    """Minimal member identity for pickers (assignee, approver) — safe for any staff role.

    ``email`` is nullable because an **external (client) login** is answered without it: a
    client reads staff *names* off their own screens and has no use for the agency's address
    book. Optional rather than a second schema, so one shape serves both callers and a
    consumer that falls back to the address gets ``None`` instead of somebody's mailbox.
    """

    user_id: str
    full_name: str | None
    email: str | None
    avatar_url: str | None = None
    #: The account can still be signed into. A deactivated colleague is **returned anyway** and
    #: flagged here, because the two things a picker owes them are opposites: they must not be
    #: suggested beside the people who are still here (that is how work gets assigned to somebody
    #: who cannot open it), and they must still be *nameable* — a task assigned before they left
    #: has to render their name rather than an empty box, and a filter has to be able to ask for
    #: their old rows. Filtering them out of the payload would answer the first and break the
    #: second, so the split is the picker's (`$lib/core/members`, §9's lifecycle rule) and this
    #: field is what lets it be made. Defaulted ``True`` so a client reading an older response
    #: shape is never told the whole roster has left.
    is_active: bool = True


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


class MemberAccountUpdate(BaseModel):
    """What Instellingen → Gebruikers → Bewerken may change about a colleague's account.

    Both fields are optional and **absent means leave alone** (§18): the dialog opens over one
    member and shows both, but a partial caller — the ⋯ Deactiveren item, which posts only the
    status — must not blank a name by omitting it.

    ``full_name`` is nullable on purpose, so absent and ``null`` have to be told apart by
    ``model_fields_set`` rather than by truthiness: an explicit ``null`` — or the blank string an
    emptied input actually posts — clears the name back to "we don't know it", and the account
    reads as its e-mail address again. ``email`` is absent by design; see the endpoint.
    """

    full_name: str | None = None
    #: ``True`` = works here, ``False`` = has left. Not a free-text status: an account has one
    #: bit, which is the whole of the members' lifecycle vocabulary (`$lib/core/members`).
    active: bool | None = None


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
    company_scope_empty: bool = False,
) -> MemberRead:
    return MemberRead(
        membership_id=str(membership.id),
        user_id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        avatar_url=effective_avatar_url(user),
        role_ids=[str(role_id) for role_id in role_ids or []],
        is_active=account_active(membership, user),
        deactivated_at=membership.deactivated_at,
        is_self=user.id == ctx.user.id,
        two_factor_enabled=two_factor_enabled,
        company_scope_empty=company_scope_empty,
    )


def account_active(membership: Membership, user: User) -> bool:
    """Can this person still work here — the one definition, read by every surface.

    Two bits, and they answer different questions. ``memberships.deactivated_at`` is the org's:
    "they have left". ``users.is_active`` is the instance's: "this account is disabled
    everywhere", which only an instance owner (or, before this endpoint existed, somebody with a
    SQL prompt) sets. Either one refuses, so a screen only ever needs the conjunction — which is
    why the *field* stayed ``is_active`` when the column arrived beside it: every consumer of
    ``MemberRead`` / ``MemberLookup`` (the picker split, the roster badge, ``$lib/core/members``)
    kept working without being touched.
    """
    return user.is_active and membership.deactivated_at is None


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
    """The team, for Instellingen → Gebruikers — **staff only**.

    A contact-linked portal membership (#193) is managed from its contact's portal section;
    listing it here invites role/2FA/revoke actions that belong there. Directly-invited
    ``client``-role members (no contact link) stay listed — hiding them would orphan them.
    """
    rows = (
        await ctx.session.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.org_id == ctx.org.id)
            .order_by(User.email.asc())
        )
    ).all()
    portal = await portal_user_ids(ctx.session, ctx.org.id, {u.id for _, u in rows})
    rows = [(m, u) for m, u in rows if u.id not in portal]
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
    # Which client-role memberships see no company at all (#274). Resolved through the scope
    # seam, so it is the *same* answer their own requests get — but only for the memberships
    # that hold the client role, which is the only role the floor applies to and a handful of
    # rows at most here (contact-linked portal logins were filtered out above). Staff are never
    # resolved: that would be a query per member for an answer that is always ``False``.
    client_role_ids = {
        role_id
        for (role_id,) in await ctx.session.execute(
            select(RoleRow.id).where(RoleRow.org_id == ctx.org.id, RoleRow.key == ROLE_CLIENT)
        )
    }
    scope_empty: set[uuid.UUID] = set()
    for m, _ in rows:
        if client_role_ids.isdisjoint(held.get(m.id, [])):
            continue
        # The ``isdisjoint`` above *is* the client-role floor's own query, already answered
        # from ``held`` — so hand it in rather than making the resolver re-run an ``EXISTS``
        # per client member (#290).
        resolution = await resolve_company_scope_details(
            ctx.session, ctx.org.id, m.id, holds_client=True
        )
        # ``None`` is *unrestricted*, not empty — the two must never collapse into one falsy
        # check (a client always resolves to a set, but the seam's contract is the seam's).
        if resolution.scope is not None and not resolution.scope:
            scope_empty.add(m.id)
    return [
        _member_read(
            ctx,
            m,
            u,
            held.get(m.id, []),
            two_factor_enabled=u.id in secured,
            company_scope_empty=m.id in scope_empty,
        )
        for m, u in rows
    ]


def staff_select(org_id: uuid.UUID, *, include_clients: bool = False, active_only: bool = False):  # noqa: ANN201
    """The org's **staff** accounts, name-ordered — the one definition of "a colleague".

    Extracted from ``lookup_members`` when a second caller appeared (#382: the AI candidate
    shortlist, which offers assignees to a dictated task). The client-role exclusion is the
    part worth having exactly one copy of: a portal contact holds a membership too, and a
    second hand-written version of this join is how one of them comes to offer clients as
    assignees months after the other stopped.

    ``active_only`` is the same argument one predicate later. "Who still works here" is now two
    columns (``account_active``), so a caller that hand-wrote ``User.is_active.is_(True)`` was
    right about the question and half-right about the answer the moment a membership could be
    deactivated on its own. It stays **off** by default, because the picker endpoint's whole
    contract is that a departed colleague is still nameable (§9's lifecycle rule); a caller that
    is generating *new* work — never a list, never a filter — opts in.
    """
    stmt = (
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.org_id == org_id)
        .order_by(func.lower(User.full_name).asc().nulls_last(), func.lower(User.email).asc())
    )
    if active_only:
        stmt = stmt.where(User.is_active.is_(True), Membership.deactivated_at.is_(None))
    if not include_clients:
        stmt = stmt.where(
            Membership.id.in_(
                select(MembershipRole.membership_id)
                .join(RoleRow, RoleRow.id == MembershipRole.role_id)
                .where(MembershipRole.org_id == org_id, RoleRow.key != ROLE_CLIENT)
            )
        )
    return stmt


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
    include_clients: bool = Query(
        False,
        description=(
            "Also return client-role memberships (portal users). Off by default: every picker "
            "built on this endpoint means *staff*."
        ),
    ),
    ctx: RequestContext = Depends(require_context),
) -> list[MemberLookup]:
    """Name/email of org **staff**, for assignee/approver pickers. Open to every member.

    A portal-enabled contact holds a membership too (`client` system role, issue #221), but a
    client is not a colleague: by default only memberships holding at least one non-``client``
    role appear, so portal users never surface as assignees. ``include_clients=true`` is the
    explicit opt-in for a picker that genuinely means "everyone with an account".

    Filtering by ``permission`` is what stops a picker from offering people who could never do
    the thing being picked. It is one indexed, ``DISTINCT`` query: a user holding two granting
    roles must not appear twice.

    A **deactivated** account stays in the answer, carrying ``is_active=False``. Whether it is
    offered is the picker's decision and not this endpoint's — §9's lifecycle rule, the one the
    client and project pickers already follow: behind the search, wearing its status, never
    absent. Dropping the row here would take that choice away from every caller at once and
    blank the name on every task the person was holding when they left.
    """
    stmt = staff_select(ctx.org.id, include_clients=include_clients)
    if permission is not None:
        if permission not in set(permission_keys()):
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"permission": "errors.validation"},
            )
        stmt = stmt.where(User.id.in_(permission_holder_ids(ctx.org.id, permission)))

    # ``deactivated_at`` rides the statement rather than being asked for per row: the flag this
    # endpoint promises is the conjunction of two columns and only one of them is on ``User``.
    rows = (await ctx.session.execute(stmt.add_columns(Membership.deactivated_at))).all()
    # An **external (client) login** gets the names and not the addresses (§15). This endpoint
    # declares no permission — "open to every member" — and a portal contact holds a membership
    # too, so a client was handed every employee's e-mail address the moment any client-reachable
    # screen loaded its pickers. The docstring above reasoned carefully about which memberships
    # come *out* and not at all about who may ask.
    #
    # Names stay, because they are drawn on screens a client is meant to read: the assignee of a
    # task ticked visible, the author of a contact moment, the account manager on their own
    # company. Withholding those would blank a dozen legitimate labels to fix a leak that is not
    # in them. The address is the part a client has no use for and an outsider does — so it is
    # ``None`` here rather than absent, and the schema says so.
    hide_email = ctx.is_portal
    return [
        MemberLookup(
            user_id=str(u.id),
            full_name=u.full_name,
            email=None if hide_email else u.email,
            avatar_url=effective_avatar_url(u),
            is_active=u.is_active and deactivated_at is None,
        )
        for u, deactivated_at in rows
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
        select(Membership).where(Membership.org_id == ctx.org.id, Membership.user_id == user.id)
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
                sent, send_error = getattr(request.state, "password_email_result", (True, None))
                member.invite_email_sent = sent
                member.invite_email_error = send_error
            except Exception:  # noqa: BLE001 — the invite itself must stand
                logger.exception("Invite email for %s failed", email)
                member.invite_email_sent = False
    return member


async def _membership_or_404(ctx: RequestContext, membership_id: uuid.UUID) -> Membership:
    membership = await ctx.session.scalar(
        select(Membership).where(Membership.id == membership_id, Membership.org_id == ctx.org.id)
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
    previous = collapse_to_legacy_role([link_key for _, link_key in held_system_links_with_keys])
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


@router.patch(
    "/{membership_id}/account",
    response_model=MemberRead,
    dependencies=[require_permission("members.member.write")],
)
async def update_member_account(
    membership_id: uuid.UUID,
    payload: MemberAccountUpdate,
    ctx: RequestContext = Depends(require_context),
) -> MemberRead:
    """Edit a colleague's account: their name, and whether they still work here.

    This is the control the product was missing, and its absence had a cost worth writing down.
    Off-boarding offered only "Toegang intrekken", which deletes the membership — so an agency
    with a departing colleague either kept a live login for someone who had left, or deleted the
    row and watched a thousand hours of their work go nameless on every screen. Neither is what
    anybody meant by "they don't work here any more".

    Deactivating keeps everything and ends only the access. The name still renders on every hour,
    task, contactmoment and activity line; the roles, contract, rooster and tarief stay on the
    row; the account drops out of the pickers that offer *new* work and stays findable behind
    every search and filter (§9's lifecycle rule, the one clients and projects already follow).
    One press of Activeren undoes it.

    Absent means leave alone (§18): the dialog opens over one member, but a form that posts a
    field it did not show is how a rename quietly reactivates somebody.

    Three refusals:

    - **Not yourself.** ``cannot_deactivate_self`` — for the reason ``cannot_remove_self`` exists,
      minus the drama: the request would succeed and the next one would 403.
    - **Not the last administrator.** ``ensure_a_role_manager_remains`` counts only accounts that
      can actually sign in (see ``role_manager_count``), so deactivating the last owner is refused
      exactly as revoking them is. Applied *after* the flush, so the guard sees the world being
      proposed and the ``AppError`` rolls it back.
    - **The e-mail address is not editable here** — it is not on the schema at all. It is the
      account's identity across the whole instance and the key an OIDC login matches on, so a
      tenant screen renaming it can silently detach somebody's Google sign-in. A typo is fixed by
      inviting the right address and revoking the wrong one, which is the rare case this whole
      endpoint exists to make *unnecessary* for the common one.

    Deactivating writes only this org's column. **Reactivating** may also lift
    ``users.is_active``, under two narrow conditions stated at the call site — that column is the
    instance's answer and, separately, the client portal's own "login enabled" flag, so the two
    principals it belongs to are the two exemptions.
    """
    membership = await _membership_or_404(ctx, membership_id)
    user = await ctx.session.get(User, membership.user_id)
    if user is None:  # pragma: no cover — the FK makes this unreachable
        raise AppError("not_found", "errors.not_found", status_code=404)

    if "full_name" in payload.model_fields_set:
        renamed = (payload.full_name or "").strip() or None
        if renamed != user.full_name:
            previous, user.full_name = user.full_name, renamed
            await audit.record(
                ctx.session,
                org_id=ctx.org.id,
                actor=ctx.user,
                action="membership.renamed",
                target_user_id=user.id,
                detail={"from": previous, "to": renamed},
            )

    if payload.active is not None and payload.active is not account_active(membership, user):
        if membership.user_id == ctx.user.id:
            raise AppError(
                "cannot_deactivate_self", "errors.cannot_deactivate_self", status_code=400
            )
        if payload.active:
            membership.deactivated_at = None
            membership.deactivated_by_user_id = None
            # Clearing our own column is not enough to make the roster's "Actief" true. An
            # account can also be off through ``users.is_active`` — every account deactivated
            # before this endpoint existed is, because a SQL prompt was the only way — and
            # leaving that set would print Actief over somebody who still cannot sign in.
            #
            # So it is lifted, under two narrow conditions, and both are about *whose bit it is*.
            # Not a superuser: instance administration is its own authorization axis (§5) and no
            # tenant screen overrules it. Not a ``client`` membership: the portal uses the very
            # same column as its "login enabled" flag, so lifting it there would switch a client
            # login the agency disabled back on from a screen that does not even list it. What
            # remains — a staff account of this org, disabled by hand — is exactly the case an
            # admin pressing Activeren means, and the residual cross-org caveat is stated in
            # ``docs/UX.md``: on a multi-org instance this re-enables a colleague's login in
            # their other org too, which is why it takes an explicit press and never rides along
            # with a rename.
            if (
                not user.is_active
                and not user.is_superuser
                and not await _holds_client_role(ctx, membership.id)
            ):
                user.is_active = True
        else:
            membership.deactivated_at = datetime.now(UTC)
            membership.deactivated_by_user_id = ctx.user.id
        await ctx.session.flush()
        await ensure_a_role_manager_remains(ctx)
        await audit.record(
            ctx.session,
            org_id=ctx.org.id,
            actor=ctx.user,
            action="membership.activated" if payload.active else "membership.deactivated",
            target_user_id=user.id,
        )

    await ctx.session.flush()
    held = await membership_role_ids(ctx.session, ctx.org.id, membership.id)
    row = await twofactor.row_for(ctx.session, user.id)
    return _member_read(
        ctx,
        membership,
        user,
        list(held),
        two_factor_enabled=twofactor.is_active(row),
    )


async def _holds_client_role(ctx: RequestContext, membership_id: uuid.UUID) -> bool:
    """Is this an external login? The seeded ``client`` role is the definition (#274)."""
    return (
        await ctx.session.scalar(
            select(MembershipRole.membership_id)
            .join(RoleRow, RoleRow.id == MembershipRole.role_id)
            .where(
                MembershipRole.org_id == ctx.org.id,
                MembershipRole.membership_id == membership_id,
                RoleRow.key == ROLE_CLIENT,
            )
            .limit(1)
        )
    ) is not None


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
        (
            await ctx.session.execute(
                select(RoleRow).where(
                    RoleRow.org_id == ctx.org.id, RoleRow.id.in_(role_ids or [uuid.uuid4()])
                )
            )
        )
        .scalars()
        .all()
    )
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
