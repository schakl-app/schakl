"""Client portal (issue #193): a contact gets a login and sees their companies' dashboards.

The pieces were already here — the seeded ``client`` system role, contacts with email,
``company_contacts``, FastAPI Users' invite machinery, per-tenant branding — this file wires
them together:

* **The link** is ``contacts.user_id``: it is what makes a membership a *portal* membership.
* **The horizon** (#191's third axis) comes from a second scope resolver registered here: a
  contact-linked membership sees exactly the companies the contact is linked to via
  ``company_contacts`` — live, so linking/unlinking widens/narrows the portal the same
  moment, and **never** ``None``: a portal login is never unrestricted.
* **The invite flow** mirrors the staff invite (``/members/invite``): create or re-activate
  the user, a ``client``-role membership, and a tenant-branded set-password mail riding the
  reset-token flow. An email collision with an existing account is a hard, explained error —
  never silently attach the client role to a staff login.
* Enable/disable is reversible: off refuses login (``is_active``) but keeps the contact, the
  history and the user row; re-enabling reuses them.

Everything is gated on ``members.member.write`` — managing logins is member management —
and every flip lands on the contact's activity trail (§16).

**Signing in as the contact** (#296) is the one thing here that is *not* member management, so
it carries its own permission (``contacts.portal.impersonate``). It reuses the platform's
impersonation grant (``app/core/impersonation.py``) with the ``portal`` kind: same host, same
tenant, so no cross-host handoff — the staff member's session cookie is already here and the
grant is simply set beside it. Three properties make it safe to hand a tenant at all:

* **It only ever reaches a portal login.** The target is ``contacts.user_id``, and that link is
  only ever created by the invite above, which refuses an address that already has an account.
  There is no input that names an arbitrary user.
* **It cannot gain the impersonator anything.** Permissions resolve for the *target* below the
  swap, and the target's set must already be covered by the caller's (``PermissionSet.covers``)
  — roles are tenant-editable, so "it's only a client" is not by itself a bound on what the
  client role holds.
* **It is never silent.** Start and stop both land on the contact's trail, and every write made
  while it runs carries the impersonator onto its own trail entry (§16).
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from pwdlib import PasswordHash
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.activity import ActivityService
from app.core.auth.models import User
from app.core.auth.users import get_user_manager
from app.core.email.service import get_row as email_settings_row
from app.core.impersonation import (
    IMPERSONATION_COOKIE,
    KIND_PORTAL,
    clear_grant_cookie,
    issue_grant,
    set_grant_cookie,
)
from app.core.models import Membership
from app.core.permissions import ROLE_CLIENT
from app.core.permissions.deps import no_permission_required, require_permission
from app.core.permissions.models import MembershipRole, RolePermission
from app.core.permissions.permset import PermissionSet
from app.core.permissions.service import create_membership
from app.core.tenancy import RequestContext, require_context
from app.errors import AppError
from app.modules.contacts.models import CompanyContact, Contact

logger = logging.getLogger("schakl.portal")

_password_hash = PasswordHash.recommended()

PortalStatus = Literal["none", "invited", "active", "disabled"]


# --------------------------------------------------------------------------- #
# Horizon resolver (#191 seam): a portal membership sees its contact's companies
# --------------------------------------------------------------------------- #
async def resolve_portal_company_scope(
    session: AsyncSession, org_id: uuid.UUID, membership_id: uuid.UUID
) -> frozenset[uuid.UUID] | None:
    rows = (
        await session.execute(
            select(CompanyContact.company_id)
            .select_from(Membership)
            .join(
                Contact,
                (Contact.user_id == Membership.user_id) & (Contact.org_id == org_id),
            )
            .outerjoin(
                CompanyContact,
                (CompanyContact.contact_id == Contact.id)
                & (CompanyContact.org_id == org_id),
            )
            .where(Membership.id == membership_id, Membership.org_id == org_id)
        )
    ).all()
    if not rows:
        # Not a contact-linked membership — this source doesn't restrict them.
        return None
    # Linked but attached to no company = an empty portal, not an unrestricted one.
    return frozenset(company_id for (company_id,) in rows if company_id is not None)


async def resolve_portal_users(
    session: AsyncSession, org_id: uuid.UUID, candidates: set[uuid.UUID]
) -> set[uuid.UUID]:
    """Which of ``candidates`` are contact-linked (portal) logins — the core seam's answerer
    (``app/core/portal.py``), used to keep staff notifications out of client inboxes."""
    rows = await session.execute(
        select(Contact.user_id).where(
            Contact.org_id == org_id, Contact.user_id.in_(candidates)
        )
    )
    return set(rows.scalars())


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class PortalState(BaseModel):
    status: PortalStatus = "none"
    email: str | None = None
    invite_email_sent: bool | None = None
    invite_email_error: str | None = None


class PortalImpersonateRequest(BaseModel):
    #: Clamped again server-side by ``SCHAKL_IMPERSONATION_MAX_MINUTES``; this is only the ask.
    minutes: int = Field(default=30, ge=1, le=24 * 60)


class PortalImpersonateResponse(BaseModel):
    cookie: str
    token: str
    expires_at: datetime
    #: Who the caller is about to become — so the confirmation is about a person, not an id.
    target_email: str
    target_name: str | None = None


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #
class PortalService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx

    async def _contact_or_404(self, contact_id: uuid.UUID) -> Contact:
        return await self.ctx.repo(Contact).get_or_404(contact_id)

    async def _linked_user(self, contact: Contact) -> User | None:
        if contact.user_id is None:
            return None
        return await self.ctx.session.get(User, contact.user_id)

    @staticmethod
    def _status(user: User | None) -> PortalStatus:
        if user is None:
            return "none"
        if not user.is_active:
            return "disabled"
        # Setting the password through the emailed link verifies the mailbox (UserManager
        # marks it); until then the invite is out but the account was never used.
        return "active" if user.is_verified else "invited"

    async def state(self, contact_id: uuid.UUID) -> PortalState:
        contact = await self._contact_or_404(contact_id)
        user = await self._linked_user(contact)
        return PortalState(status=self._status(user), email=user.email if user else None)

    async def enable(self, contact_id: uuid.UUID, request: Request, user_manager) -> PortalState:  # noqa: ANN001
        contact = await self._contact_or_404(contact_id)
        user = await self._linked_user(contact)
        if user is not None:
            # Re-enable: the account, membership and history are all still there.
            if not user.is_active:
                user.is_active = True
                await self.ctx.session.flush()
                await ActivityService(self.ctx).record(
                    "contact", contact.id, "portal_enabled", {"email": user.email}
                )
            return PortalState(status=self._status(user), email=user.email)

        email = (contact.email or "").strip().lower()
        if not email:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"email": "errors.portal_email_required"},
            )
        existing = await self.ctx.session.scalar(
            select(User).where(func.lower(User.email) == email)
        )
        if existing is not None:
            # The address already belongs to an account (a staff member's, or another org's).
            # Never silently attach the client role to it — a hard, explained error (#193).
            raise AppError("conflict", "errors.portal_email_in_use", status_code=409)

        display_name = f"{contact.first_name} {contact.last_name or ''}".strip()
        user = User(
            id=uuid.uuid4(),
            email=email,
            full_name=display_name or None,
            hashed_password=_password_hash.hash(secrets.token_urlsafe(24)),
            is_active=True,
            is_verified=False,
        )
        self.ctx.session.add(user)
        await self.ctx.session.flush()
        await create_membership(self.ctx.session, self.ctx.org.id, user.id, ROLE_CLIENT)
        contact.user_id = user.id
        await self.ctx.session.flush()
        await ActivityService(self.ctx).record(
            "contact", contact.id, "portal_enabled", {"email": email}
        )
        state = PortalState(status="invited", email=email)
        await self._send_invite(user, request, user_manager, state)
        return state

    async def resend(self, contact_id: uuid.UUID, request: Request, user_manager) -> PortalState:  # noqa: ANN001
        contact = await self._contact_or_404(contact_id)
        user = await self._linked_user(contact)
        if user is None or not user.is_active:
            raise AppError("not_found", "errors.not_found", status_code=404)
        state = PortalState(status=self._status(user), email=user.email)
        await self._send_invite(user, request, user_manager, state)
        await ActivityService(self.ctx).record(
            "contact", contact.id, "portal_invite_resent", {"email": user.email}
        )
        return state

    async def disable(self, contact_id: uuid.UUID) -> PortalState:
        contact = await self._contact_or_404(contact_id)
        user = await self._linked_user(contact)
        if user is None:
            raise AppError("not_found", "errors.not_found", status_code=404)
        if user.is_active:
            user.is_active = False
            await self.ctx.session.flush()
            await ActivityService(self.ctx).record(
                "contact", contact.id, "portal_disabled", {"email": user.email}
            )
        return PortalState(status="disabled", email=user.email)

    # ----------------------------------------------------------------- #
    # Signing in as the contact (#296)
    # ----------------------------------------------------------------- #
    async def _target_permissions(self, user_id: uuid.UUID) -> PermissionSet | None:
        """The target membership's effective permissions, or ``None`` if it has no membership.

        The same shape ``require_context`` resolves a caller's with — one statement, whatever the
        role count — because the answer is used for the same purpose: deciding what this login
        can do. ``array_agg(...).filter(...)`` is load-bearing for a role-less membership.
        """
        row = (
            await self.ctx.session.execute(
                select(
                    Membership.id,
                    func.array_agg(RolePermission.permission).filter(
                        RolePermission.permission.is_not(None)
                    ),
                )
                .outerjoin(MembershipRole, MembershipRole.membership_id == Membership.id)
                .outerjoin(
                    RolePermission, RolePermission.role_id == MembershipRole.role_id
                )
                .where(
                    Membership.org_id == self.ctx.org.id, Membership.user_id == user_id
                )
                .group_by(Membership.id)
            )
        ).first()
        if row is None:
            return None
        return PermissionSet.of(row[1])

    async def impersonate(
        self, contact_id: uuid.UUID, minutes: int
    ) -> tuple[str, datetime, User]:
        """Mint a portal impersonation grant for this contact's login, and record it.

        Returns ``(token, expires_at, target)``; the route sets the cookie. The contact is loaded
        through the tenant repository, so a membership restricted to a company group (#191) can
        only ever reach the contacts of its own clients — an out-of-horizon contact answers 404
        here exactly as it does everywhere else.
        """
        # No nesting. An impersonated session is the *target's*, and letting it open a second
        # grant would launder one identity into another with only the first one recorded.
        if self.ctx.impersonated_by is not None:
            raise AppError("conflict", "errors.impersonation_nested", status_code=409)

        contact = await self._contact_or_404(contact_id)
        user = await self._linked_user(contact)
        if user is None or not user.is_active:
            # No login, or a disabled one — there is nothing to sign in as.
            raise AppError("not_found", "errors.not_found", status_code=404)

        target_permissions = await self._target_permissions(user.id)
        if target_permissions is None:
            raise AppError("not_found", "errors.not_found", status_code=404)
        # Impersonation may hand you another view of the data; it may never hand you a capability
        # you don't have. A tenant can edit the client role freely, and an instance owner is a
        # different authorization axis entirely (§5) that no tenant permission may reach.
        if user.is_superuser or not self.ctx.permissions.covers(target_permissions):
            raise AppError(
                "forbidden", "errors.impersonation_escalation", status_code=403
            )

        token, expires_at = issue_grant(
            self.ctx.user, user.id, self.ctx.org.id, minutes, kind=KIND_PORTAL
        )
        await ActivityService(self.ctx).record(
            "contact",
            contact.id,
            "portal_impersonation_started",
            {"email": user.email, "expires_at": expires_at.isoformat()},
        )
        return token, expires_at, user

    async def stop_impersonation(self) -> bool:
        """End the caller's own portal impersonation; ``True`` if one was running.

        Runs *as the impersonated contact* — that is the whole point of the session it is ending
        — so it can declare no permission (a client holds none, and trapping someone inside an
        impersonation is the one outcome a stop button may never have). It records against the
        contact linked to the effective user, which is exactly the caller's own row.
        """
        impersonator = self.ctx.impersonated_by
        if impersonator is None or self.ctx.impersonation_kind != KIND_PORTAL:
            # Nothing of ours to end (a stale button, or an instance impersonation, which the
            # instance console stops on its own trail). The cookie still goes — idempotent.
            return False
        # Deliberately not through the repository: the horizon is an authorization narrowing, and
        # this row *is* the caller. A portal contact attached to no company has an empty horizon
        # (#193) and would not find itself, which would silently drop the stop from the trail.
        contact = await self.ctx.session.scalar(
            select(Contact).where(
                Contact.org_id == self.ctx.org.id, Contact.user_id == self.ctx.user.id
            )
        )
        if contact is not None:
            await ActivityService(self.ctx).record(
                "contact",
                contact.id,
                "portal_impersonation_stopped",
                {"email": self.ctx.user.email},
            )
        return True

    async def _send_invite(
        self,
        user: User,
        request: Request,
        user_manager,  # noqa: ANN001 — FastAPI Users' provider
        state: PortalState,
    ) -> None:
        """The tenant-branded set-password mail, riding the reset-token flow like the staff
        invite (#161). A missing transport is reported, never silently swallowed."""
        if await email_settings_row(self.ctx.session, self.ctx.org.id) is None:
            state.invite_email_sent = False
            state.invite_email_error = "errors.email_not_configured"
            return
        request.state.password_email_kind = "invite"
        try:
            await user_manager.forgot_password(user, request)
            sent, send_error = getattr(
                request.state, "password_email_result", (True, None)
            )
            state.invite_email_sent = sent
            state.invite_email_error = send_error
        except Exception:  # noqa: BLE001 — the enable itself must stand
            logger.exception("Portal invite email for %s failed", user.email)
            state.invite_email_sent = False


# --------------------------------------------------------------------------- #
# Router — nested under /contacts/{contact_id}/portal
# --------------------------------------------------------------------------- #
portal_router = APIRouter(tags=["contacts-portal"])

_MANAGE = "members.member.write"
#: Becoming the contact is its own capability, never implied by managing their login (#296).
_IMPERSONATE = "contacts.portal.impersonate"


# Declared before the ``/{contact_id}/…`` routes: both literal paths here are unambiguous
# (nothing else takes a third segment after ``portal``), and keeping them first means a future
# ``/{contact_id}/portal/{something}`` cannot quietly start swallowing them.
@portal_router.post(
    "/portal/impersonation/stop",
    status_code=204,
    dependencies=[
        no_permission_required(
            "ends the caller's OWN portal impersonation. It runs as the impersonated client, "
            "who by definition holds none of the agency's permissions — requiring one here "
            "would leave the only way out of an impersonation behind the very permission the "
            "impersonated account does not have. The grant in the request is the credential, "
            "and it authorizes nothing beyond ending itself."
        )
    ],
)
async def stop_portal_impersonation(
    response: Response, ctx: RequestContext = Depends(require_context)
) -> None:
    await PortalService(ctx).stop_impersonation()
    clear_grant_cookie(response)


@portal_router.post(
    "/{contact_id}/portal/impersonate",
    response_model=PortalImpersonateResponse,
    dependencies=[require_permission(_IMPERSONATE)],
)
async def impersonate_portal_login(
    contact_id: uuid.UUID,
    payload: PortalImpersonateRequest,
    response: Response,
    ctx: RequestContext = Depends(require_context),
) -> PortalImpersonateResponse:
    """Sign in as this contact's portal login, time-boxed and on the contact's trail (#296)."""
    token, expires_at, target = await PortalService(ctx).impersonate(
        contact_id, payload.minutes
    )
    # Set here *and* returned: the browser talks to the SSR web app, which sets its own cookie
    # from the body (the instance flow does the same), while a direct API caller gets it here.
    set_grant_cookie(response, token, expires_at)
    return PortalImpersonateResponse(
        cookie=IMPERSONATION_COOKIE,
        token=token,
        expires_at=expires_at,
        target_email=target.email,
        target_name=target.full_name,
    )


@portal_router.get(
    "/{contact_id}/portal",
    response_model=PortalState,
    dependencies=[require_permission(_MANAGE)],
)
async def portal_state(
    contact_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> PortalState:
    return await PortalService(ctx).state(contact_id)


@portal_router.post(
    "/{contact_id}/portal",
    response_model=PortalState,
    dependencies=[require_permission(_MANAGE)],
)
async def enable_portal(
    contact_id: uuid.UUID,
    request: Request,
    ctx: RequestContext = Depends(require_context),
    user_manager=Depends(get_user_manager),  # noqa: ANN001 — FastAPI Users' provider
) -> PortalState:
    return await PortalService(ctx).enable(contact_id, request, user_manager)


@portal_router.post(
    "/{contact_id}/portal/resend",
    response_model=PortalState,
    dependencies=[require_permission(_MANAGE)],
)
async def resend_portal_invite(
    contact_id: uuid.UUID,
    request: Request,
    ctx: RequestContext = Depends(require_context),
    user_manager=Depends(get_user_manager),  # noqa: ANN001
) -> PortalState:
    return await PortalService(ctx).resend(contact_id, request, user_manager)


@portal_router.delete(
    "/{contact_id}/portal",
    response_model=PortalState,
    dependencies=[require_permission(_MANAGE)],
)
async def disable_portal(
    contact_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> PortalState:
    return await PortalService(ctx).disable(contact_id)
