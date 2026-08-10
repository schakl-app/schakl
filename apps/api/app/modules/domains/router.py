"""REST endpoints for domains under ``/api/v1/domains`` (issue #90, CLAUDE.md §6, §9).

Deny-by-default: every route declares a ``domains.domain.*`` permission (§15).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.modules.domains.schemas import (
    DomainCreate,
    DomainRead,
    DomainUpdate,
    TldPriceGroup,
    TldPriceIncreaseRequest,
    TldPriceIncreaseResult,
    TldPriceRow,
    TldPriceUpsert,
)
from app.modules.domains.service import DomainService
from app.schemas import Page

router = APIRouter(prefix="/domains", tags=["domains"])


@router.get(
    "",
    response_model=Page[DomainRead],
    dependencies=[require_permission("domains.domain.read")],
)
async def list_domains(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    company_id: uuid.UUID | None = Query(None),
    q: str | None = Query(None, max_length=200),
    sort: str | None = Query(
        None,
        description=(
            "name | company | status | registrar | dns | dnssec | email_enabled"
            " | start_date | next_invoice_date | created_at | updated_at, '-' desc"
        ),
    ),
    invoiceable: bool | None = Query(
        None,
        description=(
            "Filter on the *resolved* billing answer (#298), not the stored flag:"
            " false lists what is registered elsewhere and therefore never invoiced."
        ),
    ),
    status: str | None = Query(
        None,
        max_length=50,
        description="active | redirect | parked | expired | inactive",
    ),
    registrar_provider_id: uuid.UUID | None = Query(None),
    dns_provider_id: uuid.UUID | None = Query(None),
    count: bool = Query(True, description="Compute total; set false for name-only lookups"),
    meta: bool = Query(
        True,
        description=(
            "Resolve the display fields a picker discards — client/provider names, party labels,"
            " the register facts and the resolved price. False leaves them at their empty values."
        ),
    ),
    ctx: RequestContext = Depends(require_context),
) -> Page[DomainRead]:
    items, total = await DomainService(ctx).list(
        limit=limit,
        offset=offset,
        company_id=company_id,
        q=q,
        sort=sort,
        invoiceable=invoiceable,
        status=status,
        registrar_provider_id=registrar_provider_id,
        dns_provider_id=dns_provider_id,
        count=count,
        meta=meta,
    )
    return Page(
        items=[DomainRead.model_validate(d) for d in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "",
    response_model=DomainRead,
    status_code=201,
    dependencies=[require_permission("domains.domain.write")],
)
async def create_domain(
    payload: DomainCreate,
    ctx: RequestContext = Depends(require_context),
) -> DomainRead:
    domain = await DomainService(ctx).create(payload)
    return DomainRead.model_validate(domain)


# --- TLD price list (#250) — literal segments, so declared before ``/{domain_id}`` ----- #


@router.get(
    "/tld-prices",
    response_model=list[TldPriceGroup],
    dependencies=[require_permission("domains.tld_price.read")],
)
async def list_tld_prices(
    ctx: RequestContext = Depends(require_context),
) -> list[TldPriceGroup]:
    """The per-TLD price list: current, scheduled and past rows, plus unpriced TLDs."""
    return await DomainService(ctx).tld_price_groups()


@router.post(
    "/tld-prices",
    response_model=TldPriceRow,
    dependencies=[require_permission("domains.tld_price.manage")],
)
async def set_tld_price(
    payload: TldPriceUpsert,
    ctx: RequestContext = Depends(require_context),
) -> TldPriceRow:
    """Append a price row for a TLD (a same-day row is corrected in place)."""
    return await DomainService(ctx).set_tld_price(payload)


@router.post(
    "/tld-prices/price-increase/preview",
    response_model=TldPriceIncreaseResult,
    dependencies=[require_permission("domains.tld_price.manage")],
)
async def preview_tld_price_increase(
    payload: TldPriceIncreaseRequest,
    ctx: RequestContext = Depends(require_context),
) -> TldPriceIncreaseResult:
    """What a price change would do — nothing is written (#231's preview-then-apply)."""
    return await DomainService(ctx).tld_price_increase(payload, apply=False)


@router.post(
    "/tld-prices/price-increase",
    response_model=TldPriceIncreaseResult,
    dependencies=[require_permission("domains.tld_price.manage")],
)
async def apply_tld_price_increase(
    payload: TldPriceIncreaseRequest,
    ctx: RequestContext = Depends(require_context),
) -> TldPriceIncreaseResult:
    """Apply a price change: one history row per TLD, effective ``valid_from``."""
    return await DomainService(ctx).tld_price_increase(payload, apply=True)


@router.delete(
    "/tld-prices/{price_id}",
    status_code=204,
    dependencies=[require_permission("domains.tld_price.manage")],
)
async def delete_tld_price(
    price_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    """Remove one history row (undo a scheduled increase or a mistake)."""
    await DomainService(ctx).delete_tld_price(price_id)


@router.get(
    "/{domain_id}",
    response_model=DomainRead,
    dependencies=[require_permission("domains.domain.read")],
)
async def get_domain(
    domain_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> DomainRead:
    domain = await DomainService(ctx).get(domain_id)
    return DomainRead.model_validate(domain)


@router.patch(
    "/{domain_id}",
    response_model=DomainRead,
    dependencies=[require_permission("domains.domain.write")],
)
async def update_domain(
    domain_id: uuid.UUID,
    payload: DomainUpdate,
    ctx: RequestContext = Depends(require_context),
) -> DomainRead:
    domain = await DomainService(ctx).update(domain_id, payload)
    return DomainRead.model_validate(domain)


@router.delete(
    "/{domain_id}",
    status_code=204,
    dependencies=[require_permission("domains.domain.delete")],
)
async def delete_domain(
    domain_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await DomainService(ctx).delete(domain_id)


@router.post(
    "/{domain_id}/refresh",
    response_model=DomainRead,
    dependencies=[require_permission("domains.domain.write")],
)
async def refresh_domain_dns(
    domain_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> DomainRead:
    """Re-query public DNS for this domain's nameservers + DNSSEC now (#92)."""
    domain = await DomainService(ctx).refresh_dns(domain_id)
    return DomainRead.model_validate(domain)
