"""REST endpoints for marketing under ``/api/v1/marketing`` (epic #134).

Every route declares a permission (deny-by-default, §15). Reads of a client's performance ride
``marketing.metrics.read``; managing links + listing pickable accounts rides
``marketing.link.manage``; the cross-client grid rides ``marketing.overview.read``.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.modules.marketing.models import MarketingSource
from app.modules.marketing.schemas import (
    AccountsResponse,
    CompanyMarketing,
    CompanySettingsRead,
    CompanySettingsUpdate,
    DrilldownResponse,
    LinkCreate,
    LinkRead,
    MarketingSettingsRead,
    MarketingSettingsWrite,
    MarketingSummary,
    OverviewResponse,
)
from app.modules.marketing.service import MarketingService, MarketingSettingsService

router = APIRouter(prefix="/marketing", tags=["marketing"])


# --- org settings (#134): the encrypted Google Ads developer token -------------------------- #
@router.get(
    "/settings",
    response_model=MarketingSettingsRead,
    dependencies=[require_permission("marketing.link.manage")],
)
async def get_settings(
    ctx: RequestContext = Depends(require_context),
) -> MarketingSettingsRead:
    """The org's marketing settings — reports whether an Ads developer token is configured; the
    token itself is write-only and never returned (the Google client-secret pattern)."""
    return await MarketingSettingsService(ctx).get()


@router.put(
    "/settings",
    response_model=MarketingSettingsRead,
    dependencies=[require_permission("marketing.link.manage")],
)
async def save_settings(
    payload: MarketingSettingsWrite,
    ctx: RequestContext = Depends(require_context),
) -> MarketingSettingsRead:
    """Store the encrypted Google Ads developer token (an empty value keeps the stored one)."""
    return await MarketingSettingsService(ctx).save(payload)


# --- links + pickers (#132) ------------------------------------------------------------------ #
@router.get(
    "/links",
    response_model=list[LinkRead],
    dependencies=[require_permission("marketing.metrics.read")],
)
async def list_links(
    company_id: uuid.UUID = Query(...),
    ctx: RequestContext = Depends(require_context),
) -> list[LinkRead]:
    return await MarketingService(ctx).list_links_read(company_id)


@router.post(
    "/links",
    response_model=LinkRead,
    status_code=201,
    dependencies=[require_permission("marketing.link.manage")],
)
async def create_link(
    payload: LinkCreate,
    ctx: RequestContext = Depends(require_context),
) -> LinkRead:
    return await MarketingService(ctx).create_link(payload)


@router.delete(
    "/links/{link_id}",
    status_code=204,
    dependencies=[require_permission("marketing.link.manage")],
)
async def unlink(
    link_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await MarketingService(ctx).deactivate_link(link_id)


@router.get(
    "/accounts",
    response_model=AccountsResponse,
    dependencies=[require_permission("marketing.link.manage")],
)
async def available_accounts(
    source: MarketingSource = Query(...),
    website_id: uuid.UUID | None = Query(
        None,
        description=(
            "Required for a source whose credential is per website (rankmath); ignored by"
            " every other source."
        ),
    ),
    ctx: RequestContext = Depends(require_context),
) -> AccountsResponse:
    """The accounts/properties/sites the caller's connection can reach for ``source``.

    Serves from a short Redis cache; a not-connected / missing-scope / revoked / not-configured
    state comes back as flags so the picker can *teach* rather than show a silently empty list
    (#132).

    ``website_id`` is additive and optional at the API boundary on purpose: a required
    parameter would 422 the four existing sources, which have no website to name. The source
    that needs it says so itself — ``rankmath`` answers ``configured=False`` without one, which
    is the same state the picker already draws for "no credential yet".
    """
    return await MarketingService(ctx).available_accounts(source, website_id)


# --- metrics: panel + tab (#133) ------------------------------------------------------------- #
@router.get(
    "/companies/{company_id}/metrics",
    response_model=CompanyMarketing,
    dependencies=[require_permission("marketing.metrics.read")],
)
async def company_metrics(
    company_id: uuid.UUID,
    range_days: int = Query(30, ge=1, le=400),
    period: str | None = Query(
        None,
        description=(
            "The span to report on: a trailing window (30d, 90d, 365d), a preset "
            "(month, last_month, quarter, last_quarter) or a named calendar period "
            "(2026-07, 2026-Q3). Wins over range_days; an unknown value falls back to 30d."
        ),
    ),
    ctx: RequestContext = Depends(require_context),
) -> CompanyMarketing:
    return await MarketingService(ctx).company_marketing(company_id, range_days, period)


@router.put(
    "/companies/{company_id}/settings",
    response_model=CompanySettingsRead,
    dependencies=[require_permission("marketing.link.manage")],
)
async def set_company_settings(
    company_id: uuid.UUID,
    payload: CompanySettingsUpdate,
    ctx: RequestContext = Depends(require_context),
) -> CompanySettingsRead:
    """Per-client marketing preferences: the curated tab layout (#192), the comparison this
    client's dashboard measures against (#312) and the legacy key-events toggle (#134).

    Configuration rides ``marketing.link.manage`` like linking. Hidden tiles stop being
    returned for this client — panel, tab and overview — until they're back on.

    ``compare`` is the one field where an explicit ``null`` differs from omitting it: it clears
    the override back to the org default, which is a choice the dashboard's select offers. Hence
    ``model_fields_set`` rather than a ``None`` check (CLAUDE.md §18).
    """
    return await MarketingService(ctx).set_company_settings(
        company_id,
        show_key_events=payload.show_key_events,
        layout=payload.layout,
        compare=payload.compare,
        compare_set="compare" in payload.model_fields_set,
    )


@router.get(
    "/companies/{company_id}/drilldown",
    response_model=DrilldownResponse,
    dependencies=[require_permission("marketing.metrics.read")],
)
async def drilldown(
    company_id: uuid.UUID,
    link_id: uuid.UUID = Query(...),
    kind: str = Query(...),
    range_days: int = Query(30, ge=1, le=400),
    period: str | None = Query(
        None,
        description=(
            "The span to report on: a trailing window (30d, 90d, 365d), a preset "
            "(month, last_month, quarter, last_quarter) or a named calendar period "
            "(2026-07, 2026-Q3). Wins over range_days; an unknown value falls back to 30d."
        ),
    ),
    ctx: RequestContext = Depends(require_context),
) -> DrilldownResponse:
    """A live tier-2 drill-down (top pages/queries/campaigns), Redis-cached ~1h."""
    return await MarketingService(ctx).drilldown(company_id, link_id, kind, range_days, period)


# --- My Day widget digest (#254) ------------------------------------------------------------- #
@router.get(
    "/summary",
    response_model=MarketingSummary,
    dependencies=[require_permission("marketing.metrics.read")],
)
async def summary(
    range_days: int = Query(30, ge=1, le=400),
    limit: int = Query(5, ge=1, le=20),
    period: str | None = Query(
        None,
        description=(
            "The span to report on: a trailing window (30d, 90d, 365d), a preset "
            "(month, last_month, quarter, last_quarter) or a named calendar period "
            "(2026-07, 2026-Q3). Wins over range_days; an unknown value falls back to 30d."
        ),
    ),
    ctx: RequestContext = Depends(require_context),
) -> MarketingSummary:
    """The dashboard widget's compact digest: top linked clients by their headline KPI, from
    stored data. Horizon-scoped like the per-company metrics read it summarizes — never wider
    than what the caller could fetch client-by-client."""
    return await MarketingService(ctx).summary(range_days, limit, period)


# --- cross-client overview (#133) ------------------------------------------------------------ #
@router.get(
    "/overview",
    response_model=OverviewResponse,
    dependencies=[require_permission("marketing.overview.read")],
)
async def overview(
    range_days: int = Query(30, ge=1, le=400),
    sort: str | None = Query(
        None,
        description="company_name | sessions | clicks | position | cost | conversions (- = desc)",
    ),
    period: str | None = Query(
        None,
        description=(
            "The span to report on: a trailing window (30d, 90d, 365d), a preset "
            "(month, last_month, quarter, last_quarter) or a named calendar period "
            "(2026-07, 2026-Q3). Wins over range_days; an unknown value falls back to 30d."
        ),
    ),
    ctx: RequestContext = Depends(require_context),
) -> OverviewResponse:
    """The morning-coffee grid: one row per linked client, from stored data, server-sorted."""
    return await MarketingService(ctx).overview(range_days, sort, period)
