"""Client-portal logins: invite, re-enable, disable, and sign in as (issues #193, #296).

Everything here works against a :class:`~app.core.portal.PortalSubject` — the person a login
belongs to — never against a module's model. The subject provider is registered by whoever
owns that row (today ``contacts``), so this module names no other module's internals
(CLAUDE.md §6) and a second kind of client login would need no change here.

Design rules that survived the move out of ``contacts``:

* **An email collision is a hard, explained error.** Never silently attach the client role to
  an address that already has an account — it may be a staff login, or another org's.
* **Disable is reversible.** Off refuses login (``is_active``) but keeps the subject, the
  history and the user row; re-enabling reuses all three.
* **Every flip lands on the subject's own activity trail** (§16), under the acting user's name.
* **Signing in as the client is not member management.** It carries its own permission, the
  target can only be a subject-linked login, and both start and stop are recorded. It may never
  hand the caller a capability they lack — but that is held by ``require_context`` capping the
  session to the target's permissions *intersected with the impersonator's*, not by refusing
  here; a subset cannot escalate, and refusing coupled every grant to the tenant-editable
  ``client`` role to who was allowed to impersonate at all.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime

from fastapi import Request
from pwdlib import PasswordHash
from sqlalchemy import func, select

from app.core.activity import ActivityService
from app.core.auth.models import User
from app.core.email.service import get_row as email_settings_row
from app.core.impersonation import KIND_PORTAL, issue_grant
from app.core.models import Membership
from app.core.permissions import ROLE_CLIENT
from app.core.permissions.models import MembershipRole, RolePermission
from app.core.permissions.permset import PermissionSet
from app.core.permissions.service import create_membership
from app.core.portal import (
    PortalSubject,
    portal_subject_provider,
    portal_subject_types,
)
from app.core.tenancy import RequestContext
from app.errors import AppError
from app.modules.portal.schemas import PortalLoginState, PortalStatus

logger = logging.getLogger("schakl.portal")

_password_hash = PasswordHash.recommended()


class PortalService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx

    # ----------------------------------------------------------------- #
    # Subjects
    # ----------------------------------------------------------------- #
    def _provider(self, entity_type: str):  # noqa: ANN202 — the core Protocol
        provider = portal_subject_provider(entity_type)
        if provider is None:
            # No enabled module offers this kind of subject. 404 rather than 422: from the
            # caller's side "contact" and "aardvark" are equally not-here, and saying which
            # one the instance *could* have would describe the deployment, not their request.
            raise AppError("not_found", "errors.not_found", status_code=404)
        return provider

    async def _subject_or_404(
        self, entity_type: str, subject_id: uuid.UUID
    ) -> PortalSubject:
        subject = await self._provider(entity_type).load(self.ctx, subject_id)
        if subject is None:
            # Includes "outside your company horizon" — the same 404 every other surface gives
            # (§15: a horizon must never confirm that a row it hides exists).
            raise AppError("not_found", "errors.not_found", status_code=404)
        return subject

    async def _linked_user(self, subject: PortalSubject) -> User | None:
        if subject.user_id is None:
            return None
        return await self.ctx.session.get(User, subject.user_id)

    @staticmethod
    def _status(user: User | None) -> PortalStatus:
        if user is None:
            return "none"
        if not user.is_active:
            return "disabled"
        # Setting the password through the emailed link verifies the mailbox (UserManager
        # marks it); until then the invite is out but the account was never used.
        return "active" if user.is_verified else "invited"

    def _state(
        self, subject: PortalSubject, user: User | None, **extra: object
    ) -> PortalLoginState:
        return PortalLoginState(
            entity_type=subject.entity_type,
            subject_id=str(subject.id),
            status=self._status(user),
            email=user.email if user else None,
            invite_email=subject.email,
            **extra,  # type: ignore[arg-type]
        )

    async def _record(
        self, subject: PortalSubject, action: str, payload: dict[str, object]
    ) -> None:
        await ActivityService(self.ctx).record(
            subject.entity_type, subject.id, action, payload
        )

    # ----------------------------------------------------------------- #
    # The login itself
    # ----------------------------------------------------------------- #
    async def state(
        self, entity_type: str, subject_id: uuid.UUID
    ) -> PortalLoginState:
        subject = await self._subject_or_404(entity_type, subject_id)
        return self._state(subject, await self._linked_user(subject))

    async def enable(
        self,
        entity_type: str,
        subject_id: uuid.UUID,
        request: Request,
        user_manager,  # noqa: ANN001 — FastAPI Users' provider
    ) -> PortalLoginState:
        subject = await self._subject_or_404(entity_type, subject_id)
        user = await self._linked_user(subject)
        if user is not None:
            # Re-enable: the account, membership and history are all still there.
            if not user.is_active:
                user.is_active = True
                await self.ctx.session.flush()
                await self._record(subject, "portal_enabled", {"email": user.email})
            return self._state(subject, user)

        if not subject.email:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"email": "errors.portal_email_required"},
            )
        existing = await self.ctx.session.scalar(
            select(User).where(func.lower(User.email) == subject.email)
        )
        if existing is not None:
            # The address already belongs to an account (a staff member's, or another org's).
            # Never silently attach the client role to it — a hard, explained error (#193).
            raise AppError("conflict", "errors.portal_email_in_use", status_code=409)

        user = User(
            id=uuid.uuid4(),
            email=subject.email,
            full_name=subject.display_name,
            hashed_password=_password_hash.hash(secrets.token_urlsafe(24)),
            is_active=True,
            is_verified=False,
        )
        self.ctx.session.add(user)
        await self.ctx.session.flush()
        await create_membership(self.ctx.session, self.ctx.org.id, user.id, ROLE_CLIENT)
        await self._provider(entity_type).attach(self.ctx, subject.id, user.id)
        await self._record(subject, "portal_enabled", {"email": subject.email})
        state = self._state(subject, user)
        await self._send_invite(user, request, user_manager, state)
        return state

    async def resend(
        self,
        entity_type: str,
        subject_id: uuid.UUID,
        request: Request,
        user_manager,  # noqa: ANN001
    ) -> PortalLoginState:
        subject = await self._subject_or_404(entity_type, subject_id)
        user = await self._linked_user(subject)
        if user is None or not user.is_active:
            raise AppError("not_found", "errors.not_found", status_code=404)
        state = self._state(subject, user)
        await self._send_invite(user, request, user_manager, state)
        await self._record(subject, "portal_invite_resent", {"email": user.email})
        return state

    async def disable(
        self, entity_type: str, subject_id: uuid.UUID
    ) -> PortalLoginState:
        subject = await self._subject_or_404(entity_type, subject_id)
        user = await self._linked_user(subject)
        if user is None:
            raise AppError("not_found", "errors.not_found", status_code=404)
        if user.is_active:
            user.is_active = False
            await self.ctx.session.flush()
            await self._record(subject, "portal_disabled", {"email": user.email})
        return self._state(subject, user)

    # ----------------------------------------------------------------- #
    # Signing in as the client (#296)
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
        self, entity_type: str, subject_id: uuid.UUID, minutes: int
    ) -> tuple[str, datetime, User]:
        """Mint a portal impersonation grant for this subject's login, and record it.

        Returns ``(token, expires_at, target)``; the route sets the cookie. The subject is loaded
        through its owner's repository, so a membership restricted to a company group (#191) can
        only ever reach the clients it may see — an out-of-horizon subject answers 404 here
        exactly as it does everywhere else.
        """
        # No nesting. An impersonated session is the *target's*, and letting it open a second
        # grant would launder one identity into another with only the first one recorded.
        if self.ctx.impersonated_by is not None:
            raise AppError("conflict", "errors.impersonation_nested", status_code=409)

        subject = await self._subject_or_404(entity_type, subject_id)
        user = await self._linked_user(subject)
        if user is None or not user.is_active:
            # No login, or a disabled one — there is nothing to sign in as.
            raise AppError("not_found", "errors.not_found", status_code=404)

        if await self._target_permissions(user.id) is None:
            # No membership: there is no session to enter.
            raise AppError("not_found", "errors.not_found", status_code=404)
        # Impersonation may hand you another view of the data; it may never hand you a capability
        # you don't have. That invariant now holds *inside* the session — ``require_context`` runs
        # a portal impersonation as the target **capped by the impersonator**
        # (``PermissionSet.narrowed_to``), and a subset cannot escalate. So this no longer
        # refuses: a member without an invoice read signs in and simply does not see invoices,
        # instead of being told the whole session is forbidden. Refusing was the indirect way of
        # saying it, and it coupled two things that should not be coupled — every grant to the
        # tenant-editable ``client`` role silently shrank the set of staff who could impersonate
        # at all (#266 gave clients an invoice read and locked out every member without one).
        #
        # ``is_superuser`` stays a hard refusal: it is a different authorization axis entirely
        # (§5), not a permission, so no intersection bounds it.
        if user.is_superuser:
            raise AppError(
                "forbidden", "errors.impersonation_escalation", status_code=403
            )

        token, expires_at = issue_grant(
            self.ctx.user, user.id, self.ctx.org.id, minutes, kind=KIND_PORTAL
        )
        await self._record(
            subject,
            "portal_impersonation_started",
            {"email": user.email, "expires_at": expires_at.isoformat()},
        )
        return token, expires_at, user

    async def stop_impersonation(self) -> bool:
        """End the caller's own portal impersonation; ``True`` if one was running.

        Runs *as the impersonated client* — that is the whole point of the session it is ending
        — so it can declare no permission (a client holds none, and trapping someone inside an
        impersonation is the one outcome a stop button may never have). It records against the
        subject linked to the effective user, which is exactly the caller's own row, resolved
        horizon-blind for the reason ``PortalSubjectProvider.for_user`` documents.
        """
        impersonator = self.ctx.impersonated_by
        if impersonator is None or self.ctx.impersonation_kind != KIND_PORTAL:
            # Nothing of ours to end (a stale button, or an instance impersonation, which the
            # instance console stops on its own trail). The cookie still goes — idempotent.
            return False
        # One login belongs to one subject, but *which kind* is not stated on the session — so
        # ask each registered kind and stop at the first hit. One kind exists today.
        for entity_type in portal_subject_types():
            provider = portal_subject_provider(entity_type)
            if provider is None:  # pragma: no cover — the list comes from the registry
                continue
            subject = await provider.for_user(self.ctx, self.ctx.user.id)
            if subject is not None:
                await self._record(
                    subject, "portal_impersonation_stopped", {"email": self.ctx.user.email}
                )
                break
        return True

    async def _send_invite(
        self,
        user: User,
        request: Request,
        user_manager,  # noqa: ANN001 — FastAPI Users' provider
        state: PortalLoginState,
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
