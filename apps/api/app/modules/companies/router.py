"""REST endpoints for companies under ``/api/v1/companies`` (CLAUDE.md §6, §9)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.config import settings
from app.core.models import OrgSettings
from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.modules.companies.schemas import CompanyCreate, CompanyRead, CompanyUpdate
from app.modules.companies.service import CompanyService
from app.schemas import Page, PanelData

router = APIRouter(prefix="/companies", tags=["companies"])


async def _enabled_modules(ctx: RequestContext) -> list[str]:
    org_settings = await ctx.session.scalar(
        select(OrgSettings).where(OrgSettings.org_id == ctx.org.id)
    )
    if org_settings and org_settings.enabled_modules:
        return list(org_settings.enabled_modules)
    return list(settings.enabled_modules)


@router.get(
    "",
    response_model=Page[CompanyRead],
    dependencies=[require_permission("companies.company.read")],
)
async def list_companies(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    q: str | None = Query(None, max_length=200),
    mine: bool = Query(False, description="Only clients I'm assigned to (primary or not)"),
    sort: str | None = Query(None, description="name | status | created_at | updated_at, '-' desc"),
    hours: bool = Query(
        False, description="Include the budget roll-up; costs three grouped queries"
    ),
    count: bool = Query(True, description="Compute total; set false for name-only lookups"),
    ctx: RequestContext = Depends(require_context),
) -> Page[CompanyRead]:
    items, total = await CompanyService(ctx).list(
        limit=limit, offset=offset, q=q, mine=mine, sort=sort, hours=hours, count=count
    )
    return Page(
        items=[CompanyRead.model_validate(c) for c in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "",
    response_model=CompanyRead,
    status_code=201,
    dependencies=[require_permission("companies.company.write")],
)
async def create_company(
    payload: CompanyCreate,
    ctx: RequestContext = Depends(require_context),
) -> CompanyRead:
    company = await CompanyService(ctx).create(payload)
    return CompanyRead.model_validate(company)


@router.get(
    "/{company_id}",
    response_model=CompanyRead,
    dependencies=[require_permission("companies.company.read")],
)
async def get_company(
    company_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> CompanyRead:
    company = await CompanyService(ctx).get(company_id)
    return CompanyRead.model_validate(company)


@router.patch(
    "/{company_id}",
    response_model=CompanyRead,
    dependencies=[require_permission("companies.company.write")],
)
async def update_company(
    company_id: uuid.UUID,
    payload: CompanyUpdate,
    ctx: RequestContext = Depends(require_context),
) -> CompanyRead:
    company = await CompanyService(ctx).update(company_id, payload)
    return CompanyRead.model_validate(company)


@router.delete(
    "/{company_id}",
    status_code=204,
    dependencies=[require_permission("companies.company.delete")],
)
async def delete_company(
    company_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await CompanyService(ctx).delete(company_id)


@router.get(
    "/{company_id}/panels",
    response_model=list[PanelData],
    dependencies=[require_permission("companies.company.read")],
)
async def company_panels(
    company_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> list[PanelData]:
    """Compose the detail-view panels contributed by every enabled module (the hub)."""
    # Import here to avoid a module→registry import cycle at load time.
    from app.registry import registry

    # Ensure the company exists / is in-tenant before composing panels.
    await CompanyService(ctx).get(company_id)

    enabled = await _enabled_modules(ctx)
    panels: list[PanelData] = []
    for spec in registry.panels_for("company", enabled):
        data = await spec.provider(ctx, company_id)
        panels.append(
            PanelData(
                key=spec.key,
                title_key=spec.title_key,
                position=spec.position,
                data=data,
            )
        )
    return panels
