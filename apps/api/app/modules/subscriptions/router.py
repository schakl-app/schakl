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
    SubscriptionTemplateCreate,
    SubscriptionTemplateRead,
    SubscriptionTemplateUpdate,
    SubscriptionTypeCreate,
    SubscriptionTypeRead,
    SubscriptionTypeUpdate,
    SubscriptionUpdate,
)
from app.modules.subscriptions.service import (
    SubscriptionService,
    SubscriptionTemplateService,
    SubscriptionTypeService,
)
from app.schemas import Page

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


# --- subscription types (issue #142) ----------------------------------------- #
# Declared before ``/{subscription_id}`` so "types" never matches the id path param.
@router.get(
    "/types",
    response_model=list[SubscriptionTypeRead],
    dependencies=[require_permission("subscriptions.subscription.read")],
)
async def list_subscription_types(
    include_inactive: bool = Query(False),
    ctx: RequestContext = Depends(require_context),
) -> list[SubscriptionTypeRead]:
    items = await SubscriptionTypeService(ctx).list(include_inactive=include_inactive)
    return [SubscriptionTypeRead.model_validate(t) for t in items]


@router.post(
    "/types",
    response_model=SubscriptionTypeRead,
    status_code=201,
    dependencies=[require_permission("subscriptions.type.manage")],
)
async def create_subscription_type(
    payload: SubscriptionTypeCreate,
    ctx: RequestContext = Depends(require_context),
) -> SubscriptionTypeRead:
    sub_type = await SubscriptionTypeService(ctx).create(payload)
    return SubscriptionTypeRead.model_validate(sub_type)


@router.patch(
    "/types/{type_id}",
    response_model=SubscriptionTypeRead,
    dependencies=[require_permission("subscriptions.type.manage")],
)
async def update_subscription_type(
    type_id: uuid.UUID,
    payload: SubscriptionTypeUpdate,
    ctx: RequestContext = Depends(require_context),
) -> SubscriptionTypeRead:
    sub_type = await SubscriptionTypeService(ctx).update(type_id, payload)
    return SubscriptionTypeRead.model_validate(sub_type)


@router.delete(
    "/types/{type_id}",
    status_code=204,
    dependencies=[require_permission("subscriptions.type.manage")],
)
async def delete_subscription_type(
    type_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await SubscriptionTypeService(ctx).delete(type_id)


# --- subscription templates (issue #142) -------------------------------------- #
@router.get(
    "/templates",
    response_model=list[SubscriptionTemplateRead],
    dependencies=[require_permission("subscriptions.subscription.read")],
)
async def list_subscription_templates(
    ctx: RequestContext = Depends(require_context),
) -> list[SubscriptionTemplateRead]:
    items = await SubscriptionTemplateService(ctx).list()
    return [SubscriptionTemplateRead.model_validate(t) for t in items]


@router.post(
    "/templates",
    response_model=SubscriptionTemplateRead,
    status_code=201,
    dependencies=[require_permission("subscriptions.template.manage")],
)
async def create_subscription_template(
    payload: SubscriptionTemplateCreate,
    ctx: RequestContext = Depends(require_context),
) -> SubscriptionTemplateRead:
    template = await SubscriptionTemplateService(ctx).create(payload)
    return SubscriptionTemplateRead.model_validate(template)


@router.patch(
    "/templates/{template_id}",
    response_model=SubscriptionTemplateRead,
    dependencies=[require_permission("subscriptions.template.manage")],
)
async def update_subscription_template(
    template_id: uuid.UUID,
    payload: SubscriptionTemplateUpdate,
    ctx: RequestContext = Depends(require_context),
) -> SubscriptionTemplateRead:
    template = await SubscriptionTemplateService(ctx).update(template_id, payload)
    return SubscriptionTemplateRead.model_validate(template)


@router.delete(
    "/templates/{template_id}",
    status_code=204,
    dependencies=[require_permission("subscriptions.template.manage")],
)
async def delete_subscription_template(
    template_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await SubscriptionTemplateService(ctx).delete(template_id)


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
    subscription_type_id: uuid.UUID | None = Query(None),
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
        subscription_type_id=subscription_type_id,
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
