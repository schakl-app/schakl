"""REST endpoints for domains under ``/api/v1/domains`` (issue #90, CLAUDE.md §6, §9).

Deny-by-default: every route declares a ``domains.domain.*`` permission (§15).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.modules.domains.schemas import DomainCreate, DomainRead, DomainUpdate
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
            " | created_at | updated_at, '-' desc"
        ),
    ),
    ctx: RequestContext = Depends(require_context),
) -> Page[DomainRead]:
    items, total = await DomainService(ctx).list(
        limit=limit, offset=offset, company_id=company_id, q=q, sort=sort
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
