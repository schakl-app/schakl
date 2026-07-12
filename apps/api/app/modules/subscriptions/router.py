"""REST endpoints for subscriptions under ``/api/v1/subscriptions`` (issue #30)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.modules.subscriptions.schemas import (
    PriceRead,
    SubscriptionCreate,
    SubscriptionRead,
    SubscriptionSummary,
    SubscriptionUpdate,
)
from app.modules.subscriptions.service import SubscriptionService
from app.schemas import Page

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


@router.get(
    "",
    response_model=Page[SubscriptionRead],
    dependencies=[require_permission("subscriptions.subscription.read")],
)
async def list_subscriptions(
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    company_id: uuid.UUID | None = Query(None),
    status: str | None = Query(None),
    sort: str | None = Query(None, description="name | status | next_invoice_date | start_date"),
    entity_type: str | None = Query(None, description="with entity_id: linked-entity filter"),
    entity_id: uuid.UUID | None = Query(None),
    usage: bool = Query(False, description="include current-period usage per row"),
    ctx: RequestContext = Depends(require_context),
) -> Page[SubscriptionRead]:
    items, total = await SubscriptionService(ctx).list(
        limit=limit,
        offset=offset,
        company_id=company_id,
        status=status,
        sort=sort,
        entity_type=entity_type,
        entity_id=entity_id,
        usage=usage,
    )
    return Page(
        items=[SubscriptionRead.model_validate(s) for s in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/summary",
    response_model=SubscriptionSummary,
    dependencies=[require_permission("subscriptions.subscription.read")],
)
async def summary(ctx: RequestContext = Depends(require_context)) -> SubscriptionSummary:
    """MRR/ARR + the invoices due within a month, for Overzicht → Omzet."""
    return SubscriptionSummary.model_validate(await SubscriptionService(ctx).summary())


@router.get(
    "/{subscription_id}",
    response_model=SubscriptionRead,
    dependencies=[require_permission("subscriptions.subscription.read")],
)
async def get_subscription(
    subscription_id: uuid.UUID,
    usage: bool = Query(False, description="Include current-period included-hours usage"),
    ctx: RequestContext = Depends(require_context),
) -> SubscriptionRead:
    sub = await SubscriptionService(ctx).get(subscription_id, usage=usage)
    return SubscriptionRead.model_validate(sub)


@router.get(
    "/{subscription_id}/prices",
    response_model=list[PriceRead],
    dependencies=[require_permission("subscriptions.subscription.read")],
)
async def price_history(
    subscription_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> list[PriceRead]:
    """The append-only price history, newest first."""
    rows = await SubscriptionService(ctx).price_history(subscription_id)
    return [PriceRead.model_validate(r) for r in rows]


@router.post(
    "",
    response_model=SubscriptionRead,
    status_code=201,
    dependencies=[require_permission("subscriptions.subscription.write")],
)
async def create_subscription(
    payload: SubscriptionCreate,
    ctx: RequestContext = Depends(require_context),
) -> SubscriptionRead:
    sub = await SubscriptionService(ctx).create(payload)
    return SubscriptionRead.model_validate(sub)


@router.patch(
    "/{subscription_id}",
    response_model=SubscriptionRead,
    dependencies=[require_permission("subscriptions.subscription.write")],
)
async def update_subscription(
    subscription_id: uuid.UUID,
    payload: SubscriptionUpdate,
    ctx: RequestContext = Depends(require_context),
) -> SubscriptionRead:
    sub = await SubscriptionService(ctx).update(subscription_id, payload)
    return SubscriptionRead.model_validate(sub)


@router.delete(
    "/{subscription_id}",
    status_code=204,
    dependencies=[require_permission("subscriptions.subscription.delete")],
)
async def delete_subscription(
    subscription_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await SubscriptionService(ctx).delete(subscription_id)
