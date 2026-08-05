"""Client-portal login routes — ``/api/v1/portal`` (issues #193, #296).

Addressed by **subject**, not by contact: ``/portal/logins/{entity_type}/{subject_id}``. The
entity type is the one the owning module registered (``contact``), which is what keeps this
module from naming ``contacts`` anywhere — including in a URL. An unregistered type answers
404 in the service, exactly like an unknown id.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, Response

from app.core.auth.users import get_user_manager
from app.core.entitlements import license_exempt
from app.core.impersonation import (
    IMPERSONATION_COOKIE,
    clear_grant_cookie,
    set_grant_cookie,
)
from app.core.permissions.deps import no_permission_required, require_permission
from app.core.tenancy import RequestContext, require_context
from app.modules.portal.permissions import PORTAL_IMPERSONATE
from app.modules.portal.schemas import (
    PortalImpersonateRequest,
    PortalImpersonateResponse,
    PortalLoginState,
)
from app.modules.portal.service import PortalService

router = APIRouter(prefix="/portal", tags=["portal"])

#: Managing a client login is member management — see ``permissions.py`` for why it did not
#: become a key of its own.
_MANAGE = "members.member.write"

_SUBJECT = "/logins/{entity_type}/{subject_id}"


# Declared before the ``/logins/…`` routes: the literal path here is unambiguous, and keeping
# it first means a future ``{entity_type}`` value can never quietly start swallowing it.
@router.post(
    "/impersonation/stop",
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
@license_exempt(
    "the way OUT of an impersonation. The module's write gate would 402 this the moment the "
    "licence lapsed, stranding whoever was inside a client's session — and an escape hatch is "
    "not a thing anyone should have to buy. It mutates no licensed data: it clears a cookie."
)
async def stop_portal_impersonation(
    response: Response, ctx: RequestContext = Depends(require_context)
) -> None:
    await PortalService(ctx).stop_impersonation()
    clear_grant_cookie(response)


@router.get(
    _SUBJECT,
    response_model=PortalLoginState,
    dependencies=[require_permission(_MANAGE)],
)
async def portal_login_state(
    entity_type: str,
    subject_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> PortalLoginState:
    return await PortalService(ctx).state(entity_type, subject_id)


@router.post(
    _SUBJECT,
    response_model=PortalLoginState,
    dependencies=[require_permission(_MANAGE)],
)
async def enable_portal_login(
    entity_type: str,
    subject_id: uuid.UUID,
    request: Request,
    ctx: RequestContext = Depends(require_context),
    user_manager=Depends(get_user_manager),  # noqa: ANN001 — FastAPI Users' provider
) -> PortalLoginState:
    """Invite this subject to the portal, or re-enable a login that was switched off."""
    return await PortalService(ctx).enable(entity_type, subject_id, request, user_manager)


@router.post(
    f"{_SUBJECT}/resend",
    response_model=PortalLoginState,
    dependencies=[require_permission(_MANAGE)],
)
async def resend_portal_invite(
    entity_type: str,
    subject_id: uuid.UUID,
    request: Request,
    ctx: RequestContext = Depends(require_context),
    user_manager=Depends(get_user_manager),  # noqa: ANN001
) -> PortalLoginState:
    return await PortalService(ctx).resend(entity_type, subject_id, request, user_manager)


@router.delete(
    _SUBJECT,
    response_model=PortalLoginState,
    dependencies=[require_permission(_MANAGE)],
)
async def disable_portal_login(
    entity_type: str,
    subject_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> PortalLoginState:
    return await PortalService(ctx).disable(entity_type, subject_id)


@router.post(
    f"{_SUBJECT}/impersonate",
    response_model=PortalImpersonateResponse,
    dependencies=[require_permission(PORTAL_IMPERSONATE)],
)
async def impersonate_portal_login(
    entity_type: str,
    subject_id: uuid.UUID,
    payload: PortalImpersonateRequest,
    response: Response,
    ctx: RequestContext = Depends(require_context),
) -> PortalImpersonateResponse:
    """Sign in as this subject's portal login, time-boxed and on their own trail (#296)."""
    token, expires_at, target = await PortalService(ctx).impersonate(
        entity_type, subject_id, payload.minutes
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
