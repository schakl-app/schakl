"""REST endpoints for mollie under ``/api/v1/mollie`` (issue #267, CLAUDE.md §6, §9).

Deliberately small. Every route here is about **the credential**, gated on the one permission
this module introduces (§15, deny-by-default). What is conspicuously absent is a payment route:
starting a checkout and reading its state live on the *invoice*
(``/api/v1/invoicing/invoices/{id}/payment-intents``) because that is what they are about, and
because the day a second provider ships, the screen that spends them must not have to learn a
second URL. The callback lives there for the same reason — one webhook route serves every
provider, parsing delegated through the seam (``app.core.payments``).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.integrations.mollie.schemas import (
    MollieAccountCreate,
    MollieAccountRead,
    MollieAccountUpdate,
    MollieAccountVerifyResult,
)
from app.integrations.mollie.service import MollieAccountService

router = APIRouter(prefix="/mollie", tags=["mollie"])

_MANAGE = "mollie.settings.manage"


@router.get(
    "/accounts",
    response_model=list[MollieAccountRead],
    dependencies=[require_permission(_MANAGE)],
)
async def list_accounts(
    ctx: RequestContext = Depends(require_context),
) -> list[MollieAccountRead]:
    """Connected Mollie keys. The key itself is never part of the response."""
    return [MollieAccountRead(**row) for row in await MollieAccountService(ctx).list_accounts()]


@router.post(
    "/accounts",
    response_model=MollieAccountRead,
    status_code=201,
    dependencies=[require_permission(_MANAGE)],
)
async def create_account(
    payload: MollieAccountCreate, ctx: RequestContext = Depends(require_context)
) -> MollieAccountRead:
    """Store a credential. Creating does not verify it — ``/verify`` is the explicit probe, so
    a typo is reported on the settings screen rather than as a failed save."""
    service = MollieAccountService(ctx)
    account = await service.create_account(payload)
    return MollieAccountRead(**service.serialize(account))


@router.patch(
    "/accounts/{account_id}",
    response_model=MollieAccountRead,
    dependencies=[require_permission(_MANAGE)],
)
async def update_account(
    account_id: uuid.UUID,
    payload: MollieAccountUpdate,
    ctx: RequestContext = Depends(require_context),
) -> MollieAccountRead:
    """Rename, re-link or rotate. An omitted ``api_key`` keeps the stored one."""
    service = MollieAccountService(ctx)
    account = await service.update_account(account_id, payload)
    return MollieAccountRead(**service.serialize(account))


@router.post(
    "/accounts/{account_id}/verify",
    response_model=MollieAccountVerifyResult,
    dependencies=[require_permission(_MANAGE)],
)
async def verify_account(
    account_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> MollieAccountVerifyResult:
    """Ask Mollie whether this key works, and which methods it can take.

    Answers ``200`` with ``ok=false`` for a rejected credential rather than an error status:
    the probe succeeded, its answer was no, and the row keeps Mollie's own words on it.
    """
    return await MollieAccountService(ctx).verify(account_id)


@router.delete(
    "/accounts/{account_id}",
    status_code=204,
    dependencies=[require_permission(_MANAGE)],
)
async def delete_account(
    account_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> None:
    await MollieAccountService(ctx).delete_account(account_id)
