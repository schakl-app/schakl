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
"""

from __future__ import annotations

import logging
import secrets
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pwdlib import PasswordHash
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.activity import ActivityService
from app.core.auth.models import User
from app.core.auth.users import get_user_manager
from app.core.email.service import get_row as email_settings_row
from app.core.models import Membership
from app.core.permissions import ROLE_CLIENT
from app.core.permissions.deps import require_permission
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
            state.invite_email_sent = True
        except Exception:  # noqa: BLE001 — the enable itself must stand
            logger.exception("Portal invite email for %s failed", user.email)
            state.invite_email_sent = False


# --------------------------------------------------------------------------- #
# Router — nested under /contacts/{contact_id}/portal
# --------------------------------------------------------------------------- #
portal_router = APIRouter(tags=["contacts-portal"])

_MANAGE = "members.member.write"


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
