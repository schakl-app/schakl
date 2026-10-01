"""REST endpoints for meta_ads under ``/api/v1/meta-ads``. Business-licensed — see LICENSE.

Deny-by-default: every route declares one of the five ``meta_ads.*`` permissions (§15). The
service refines where the rule depends on the payload — a create that carries a budget asks
for ``budget.write`` as well, and switching something on asks for ``ads.activate`` — so the
route's key is the *least* a caller must hold, never the most.

**These routes are the MCP surface** (CLAUDE.md §12): a handler is named for what an agent
would ask for, and the permission a key must carry is the one declared here.

Every path names the ad account by **schakl's** id. Meta's ids — of a campaign, an ad set, an
ad — ride in ``…_meta_id`` parameters and are resolved inside that account before anything is
sent to them: another account's campaign is a 404, never a call.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query, status

from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.errors import AppError
from app.integrations.meta.models import MetaAsset
from app.integrations.meta_ads import policy as rules
from app.integrations.meta_ads.models import MetaAdsDecision, MetaAdsPolicy
from app.integrations.meta_ads.schemas import (
    MetaAdsAccountLive,
    MetaAdsAccountRead,
    MetaAdsAdCreate,
    MetaAdsAdRead,
    MetaAdsAdSetCreate,
    MetaAdsAdSetRead,
    MetaAdsAdSetUpdate,
    MetaAdsAdUpdate,
    MetaAdsBoost,
    MetaAdsBoostResult,
    MetaAdsBudgetWrite,
    MetaAdsCampaignCreate,
    MetaAdsCampaignRead,
    MetaAdsCampaignUpdate,
    MetaAdsCreativeCreate,
    MetaAdsCreativeRead,
    MetaAdsDecisionCreate,
    MetaAdsDecisionPage,
    MetaAdsDecisionRead,
    MetaAdsImageRead,
    MetaAdsImageUpload,
    MetaAdsInsightsRead,
    MetaAdsLevel,
    MetaAdsPolicyRead,
    MetaAdsPolicyValues,
    MetaAdsPolicyWrite,
    MetaAdsResult,
    MetaAdsSettingsRead,
    MetaAdsSettingsWrite,
    MetaAdsStatusWrite,
)
from app.integrations.meta_ads.service import MetaAdsService, _cents, _stamp

router = APIRouter(prefix="/meta-ads", tags=["meta_ads"])

_READ = "meta_ads.account.read"
_WRITE = "meta_ads.campaign.write"
_BUDGET = "meta_ads.budget.write"
_ACTIVATE = "meta_ads.ads.activate"
_POLICY = "meta_ads.policy.manage"


# --- shapes ---------------------------------------------------------------------------------- #
def _account(
    row: MetaAsset, names: dict[uuid.UUID, str], caps: dict[uuid.UUID, dict[str, bool]]
) -> MetaAdsAccountRead:
    mine = caps.get(row.id, {})
    return MetaAdsAccountRead(
        id=row.id,
        meta_id=row.external_id,
        name=row.name,
        company_id=row.company_id,
        company_name=names.get(row.company_id) if row.company_id else None,
        currency=row.currency,
        timezone=row.timezone,
        account_status=row.account_status,
        # A token whose scopes were never read is not judged on them.
        can_read=bool(mine.get("ads_read", True)) or bool(mine.get("ads_write", False)),
        can_write=bool(mine.get("ads_write", True)),
        status=row.status,
        last_error=row.last_error,
        ads_manager_url=(
            f"https://adsmanager.facebook.com/adsmanager/manage/campaigns?act={row.external_id}"
        ),
    )


def _values(source: Any) -> MetaAdsPolicyValues:
    increase = getattr(source, "max_budget_increase", None)
    return MetaAdsPolicyValues(
        max_daily_budget_cents=getattr(source, "max_daily_budget", None),
        max_lifetime_budget_cents=getattr(source, "max_lifetime_budget", None),
        max_budget_increase_pct=int(increase * 100) if increase is not None else None,
        banned_phrases=list(getattr(source, "banned_phrases", None) or []),
        dsa_beneficiary=getattr(source, "dsa_beneficiary", None),
        dsa_payor=getattr(source, "dsa_payor", None),
        steering=str(getattr(source, "steering", "") or ""),
    )


def _policy(
    account_id: uuid.UUID | None, own: MetaAdsPolicy | None, house: MetaAdsPolicy | None
) -> MetaAdsPolicyRead:
    layer = house if account_id is None else own
    effective = rules.resolve(own if account_id is not None else None, house)
    return MetaAdsPolicyRead(
        account_id=account_id,
        own=_values(layer) if layer is not None else MetaAdsPolicyValues(),
        effective=_values(effective),
        house_steering=effective.house_steering,
    )


def _campaign(row: dict[str, Any]) -> MetaAdsCampaignRead:
    return MetaAdsCampaignRead(
        meta_id=str(row.get("id") or ""),
        name=str(row.get("name") or ""),
        objective=str(row.get("objective") or "") or None,
        status=str(row.get("status") or "") or None,
        effective_status=str(row.get("effective_status") or "") or None,
        daily_budget_cents=_cents(row.get("daily_budget")),
        lifetime_budget_cents=_cents(row.get("lifetime_budget")),
        budget_remaining_cents=_cents(row.get("budget_remaining")),
        bid_strategy=str(row.get("bid_strategy") or "") or None,
        special_ad_categories=[str(c) for c in (row.get("special_ad_categories") or [])],
        start_time=_stamp(row.get("start_time")),
        stop_time=_stamp(row.get("stop_time")),
        created_time=_stamp(row.get("created_time")),
        updated_time=_stamp(row.get("updated_time")),
    )


def _adset(row: dict[str, Any]) -> MetaAdsAdSetRead:
    targeting = row.get("targeting")
    return MetaAdsAdSetRead(
        meta_id=str(row.get("id") or ""),
        campaign_meta_id=str(row.get("campaign_id") or "") or None,
        name=str(row.get("name") or ""),
        status=str(row.get("status") or "") or None,
        effective_status=str(row.get("effective_status") or "") or None,
        daily_budget_cents=_cents(row.get("daily_budget")),
        lifetime_budget_cents=_cents(row.get("lifetime_budget")),
        budget_remaining_cents=_cents(row.get("budget_remaining")),
        billing_event=str(row.get("billing_event") or "") or None,
        optimization_goal=str(row.get("optimization_goal") or "") or None,
        bid_strategy=str(row.get("bid_strategy") or "") or None,
        bid_amount_cents=_cents(row.get("bid_amount")),
        targeting=targeting if isinstance(targeting, dict) else {},
        dsa_beneficiary=str(row.get("dsa_beneficiary") or "") or None,
        dsa_payor=str(row.get("dsa_payor") or "") or None,
        start_time=_stamp(row.get("start_time")),
        end_time=_stamp(row.get("end_time")),
    )


def _ad(row: dict[str, Any]) -> MetaAdsAdRead:
    creative = row.get("creative")
    creative_id = None
    if isinstance(creative, dict):
        creative_id = str(creative.get("id") or creative.get("creative_id") or "") or None
    issues = row.get("issues_info")
    return MetaAdsAdRead(
        meta_id=str(row.get("id") or ""),
        adset_meta_id=str(row.get("adset_id") or "") or None,
        campaign_meta_id=str(row.get("campaign_id") or "") or None,
        name=str(row.get("name") or ""),
        status=str(row.get("status") or "") or None,
        effective_status=str(row.get("effective_status") or "") or None,
        creative_meta_id=creative_id,
        issues=[i for i in issues if isinstance(i, dict)] if isinstance(issues, list) else [],
        created_time=_stamp(row.get("created_time")),
    )


def _decision(row: MetaAdsDecision) -> MetaAdsDecisionRead:
    return MetaAdsDecisionRead(
        id=row.id,
        account_id=row.asset_id,
        subject_type=row.subject_type,
        subject_meta_id=row.subject_id,
        subject_name=row.subject_name,
        decision=row.decision,
        reason=row.reason,
        applied=row.applied,
        payload=dict(row.payload or {}),
        decided_by_name=row.decided_by_name,
        impersonator_name=row.impersonator_name,
        created_at=row.created_at,
    )


def _statuses(raw: str | None) -> list[str] | None:
    values = [part.strip().upper() for part in (raw or "").split(",") if part.strip()]
    return values or None


# --- settings and policy ---------------------------------------------------------------------- #
@router.get(
    "/settings",
    response_model=MetaAdsSettingsRead,
    dependencies=[require_permission(_POLICY)],
)
async def get_meta_ads_settings(
    ctx: RequestContext = Depends(require_context),
) -> MetaAdsSettingsRead:
    """Whether anything may be changed in an ad account at all."""
    row = await MetaAdsService(ctx).settings_row()
    return MetaAdsSettingsRead(writes_enabled=row.writes_enabled if row else True)


@router.put(
    "/settings",
    response_model=MetaAdsSettingsRead,
    dependencies=[require_permission(_POLICY)],
)
async def save_meta_ads_settings(
    payload: MetaAdsSettingsWrite, ctx: RequestContext = Depends(require_context)
) -> MetaAdsSettingsRead:
    row = await MetaAdsService(ctx).save_settings(writes_enabled=payload.writes_enabled)
    return MetaAdsSettingsRead(writes_enabled=row.writes_enabled)


@router.get(
    "/policy",
    response_model=MetaAdsPolicyRead,
    dependencies=[require_permission(_READ)],
)
async def get_meta_ads_house_policy(
    ctx: RequestContext = Depends(require_context),
) -> MetaAdsPolicyRead:
    """The agency's own guardrails: what applies to every ad account that says nothing else."""
    own, house = await MetaAdsService(ctx).policy_rows(None)
    return _policy(None, own, house)


@router.put(
    "/policy",
    response_model=MetaAdsPolicyRead,
    dependencies=[require_permission(_POLICY)],
)
async def save_meta_ads_house_policy(
    payload: MetaAdsPolicyWrite, ctx: RequestContext = Depends(require_context)
) -> MetaAdsPolicyRead:
    service = MetaAdsService(ctx)
    await service.save_policy(None, payload.model_dump(exclude_unset=True))
    own, house = await service.policy_rows(None)
    return _policy(None, own, house)


# --- accounts -------------------------------------------------------------------------------- #
@router.get(
    "/accounts",
    response_model=list[MetaAdsAccountRead],
    dependencies=[require_permission(_READ)],
)
async def list_meta_ads_accounts(
    company_id: uuid.UUID | None = Query(default=None),
    active_only: bool = Query(default=True),
    ctx: RequestContext = Depends(require_context),
) -> list[MetaAdsAccountRead]:
    """Every linked Meta ad account this caller may see — **start here**.

    Every other tool takes one of these ``id`` values. An account is linked to a client in
    Instellingen → Meta; one attached to no client is the agency's own.
    """
    service = MetaAdsService(ctx)
    rows = await service.list_accounts(company_id=company_id, active_only=active_only)
    names = await service.company_names(rows)
    caps = await service.scopes_for(rows)
    return [_account(row, names, caps) for row in rows]


@router.get(
    "/accounts/{account_id}",
    response_model=MetaAdsAccountRead,
    dependencies=[require_permission(_READ)],
)
async def get_meta_ads_account(
    account_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> MetaAdsAccountRead:
    """One linked ad account, as it is stored here. Costs Meta nothing."""
    service = MetaAdsService(ctx)
    row = await service.get_account(account_id)
    return _account(row, await service.company_names([row]), await service.scopes_for([row]))


@router.get(
    "/accounts/{account_id}/live",
    response_model=MetaAdsAccountLive,
    dependencies=[require_permission(_READ)],
)
async def get_meta_ads_account_live(
    account_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> MetaAdsAccountLive:
    """What Meta says about the account now: its status, what it has spent, its cap — and
    how much of the hourly API allowance is used."""
    return MetaAdsAccountLive(**await MetaAdsService(ctx).live_account(account_id))


@router.get(
    "/accounts/{account_id}/policy",
    response_model=MetaAdsPolicyRead,
    dependencies=[require_permission(_READ)],
)
async def get_meta_ads_policy(
    account_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> MetaAdsPolicyRead:
    """The guardrails that apply to this account, and which of them are its own."""
    service = MetaAdsService(ctx)
    account = await service.get_account(account_id)
    own, house = await service.policy_rows(account.id)
    return _policy(account.id, own, house)


@router.put(
    "/accounts/{account_id}/policy",
    response_model=MetaAdsPolicyRead,
    dependencies=[require_permission(_POLICY)],
)
async def save_meta_ads_policy(
    account_id: uuid.UUID,
    payload: MetaAdsPolicyWrite,
    ctx: RequestContext = Depends(require_context),
) -> MetaAdsPolicyRead:
    service = MetaAdsService(ctx)
    await service.save_policy(account_id, payload.model_dump(exclude_unset=True))
    own, house = await service.policy_rows(account_id)
    return _policy(account_id, own, house)


@router.delete(
    "/accounts/{account_id}/policy",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_permission(_POLICY)],
)
async def clear_meta_ads_policy(
    account_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> None:
    """Drop this account's own guardrails. The house policy applies again."""
    await MetaAdsService(ctx).clear_policy(account_id)


# --- reads ----------------------------------------------------------------------------------- #
@router.get(
    "/accounts/{account_id}/campaigns",
    response_model=list[MetaAdsCampaignRead],
    dependencies=[require_permission(_READ)],
)
async def list_meta_ads_campaigns(
    account_id: uuid.UUID,
    status_: str | None = Query(
        default=None,
        alias="status",
        description="A comma-separated set of Meta's effective statuses, e.g. ACTIVE,PAUSED.",
    ),
    ctx: RequestContext = Depends(require_context),
) -> list[MetaAdsCampaignRead]:
    """The account's campaigns, with their budgets in cents."""
    rows = await MetaAdsService(ctx).list_objects(
        account_id, "campaigns", statuses=_statuses(status_)
    )
    return [_campaign(row) for row in rows]


@router.get(
    "/accounts/{account_id}/adsets",
    response_model=list[MetaAdsAdSetRead],
    dependencies=[require_permission(_READ)],
)
async def list_meta_ads_adsets(
    account_id: uuid.UUID,
    campaign_meta_id: str | None = Query(default=None, max_length=64),
    status_: str | None = Query(default=None, alias="status"),
    ctx: RequestContext = Depends(require_context),
) -> list[MetaAdsAdSetRead]:
    """The account's ad sets: who each one targets, what it may spend, and whose name the
    EU's transparency rules put on it."""
    rows = await MetaAdsService(ctx).list_objects(
        account_id, "adsets", statuses=_statuses(status_), campaign_meta_id=campaign_meta_id
    )
    return [_adset(row) for row in rows]


@router.get(
    "/accounts/{account_id}/ads",
    response_model=list[MetaAdsAdRead],
    dependencies=[require_permission(_READ)],
)
async def list_meta_ads_ads(
    account_id: uuid.UUID,
    campaign_meta_id: str | None = Query(default=None, max_length=64),
    adset_meta_id: str | None = Query(default=None, max_length=64),
    status_: str | None = Query(default=None, alias="status"),
    ctx: RequestContext = Depends(require_context),
) -> list[MetaAdsAdRead]:
    """The account's ads, with what Meta's review made of each (``effective_status``,
    ``issues``) — the place a disapproved ad is found."""
    rows = await MetaAdsService(ctx).list_objects(
        account_id,
        "ads",
        statuses=_statuses(status_),
        campaign_meta_id=campaign_meta_id,
        adset_meta_id=adset_meta_id,
    )
    return [_ad(row) for row in rows]


@router.get(
    "/accounts/{account_id}/creatives",
    response_model=list[MetaAdsCreativeRead],
    dependencies=[require_permission(_READ)],
)
async def list_meta_ads_creatives(
    account_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> list[MetaAdsCreativeRead]:
    rows = await MetaAdsService(ctx).list_objects(account_id, "adcreatives")
    return [
        MetaAdsCreativeRead(
            meta_id=str(row.get("id") or ""),
            name=str(row.get("name") or ""),
            status=str(row.get("status") or "") or None,
            object_story_id=str(row.get("object_story_id") or "") or None,
            thumbnail_url=str(row.get("thumbnail_url") or "") or None,
        )
        for row in rows
    ]


@router.get(
    "/accounts/{account_id}/insights",
    response_model=MetaAdsInsightsRead,
    dependencies=[require_permission(_READ)],
)
async def meta_ads_insights(
    account_id: uuid.UUID,
    level: MetaAdsLevel = Query(default="campaign"),
    period: str | None = Query(
        default=None,
        max_length=32,
        description="A period token: 30d, month, last_month, 2026-07, 2026-Q3, or "
        "2026-08-01..2026-08-14. Ignored when both dates are given.",
    ),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    daily: bool = Query(default=False, description="One row per day instead of one per span."),
    campaign_meta_id: str | None = Query(default=None, max_length=64),
    ctx: RequestContext = Depends(require_context),
) -> MetaAdsInsightsRead:
    """Spend, reach, clicks and results — per campaign, ad set or ad, over a span.

    ``totals`` is Meta's own figure for the whole span, never a sum of the rows: ``ctr``,
    ``cpc`` and ``reach`` cannot be added up. The last two days are provisional, which
    ``warnings`` says.
    """
    return MetaAdsInsightsRead(
        **await MetaAdsService(ctx).insights(
            account_id,
            level=level,
            period=period,
            date_from=date_from,
            date_to=date_to,
            daily=daily,
            campaign_meta_id=campaign_meta_id,
        )
    )


@router.get(
    "/accounts/{account_id}/decisions",
    response_model=MetaAdsDecisionPage,
    dependencies=[require_permission(_READ)],
)
async def list_meta_ads_decisions(
    account_id: uuid.UUID,
    subject_meta_id: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: RequestContext = Depends(require_context),
) -> MetaAdsDecisionPage:
    """What was changed in this account from here, by whom, and why — newest first.

    Read this **before proposing a change**: it is the only place that says a budget was
    raised last Tuesday because the client asked, or that a campaign was looked at and
    deliberately left running.
    """
    rows, total = await MetaAdsService(ctx).decisions(
        account_id, subject_meta_id=subject_meta_id, limit=limit, offset=offset
    )
    return MetaAdsDecisionPage(
        items=[_decision(row) for row in rows], total=total, limit=limit, offset=offset
    )


@router.post(
    "/accounts/{account_id}/decisions",
    response_model=MetaAdsDecisionRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission(_WRITE)],
)
async def record_meta_ads_decision(
    account_id: uuid.UUID,
    payload: MetaAdsDecisionCreate,
    ctx: RequestContext = Depends(require_context),
) -> MetaAdsDecisionRead:
    """Record that something was looked at and **left as it is**, and why. Changes nothing
    at Meta — which is exactly why it has to be written down here."""
    row = await MetaAdsService(ctx).record_kept(
        account_id,
        subject_type=payload.subject_type,
        subject_meta_id=payload.subject_meta_id,
        reason=payload.reason,
    )
    return _decision(row)


# --- writes ---------------------------------------------------------------------------------- #
@router.post(
    "/accounts/{account_id}/campaigns",
    response_model=MetaAdsResult,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission(_WRITE)],
)
async def create_meta_ads_campaign(
    account_id: uuid.UUID,
    payload: MetaAdsCampaignCreate,
    ctx: RequestContext = Depends(require_context),
) -> MetaAdsResult:
    """Create a campaign. **It is created paused and spends nothing** until somebody holding
    the activate permission switches it on.

    A campaign that carries a budget also needs the budget permission. ``validate_only``
    asks Meta whether it would accept this and creates nothing.
    """
    return MetaAdsResult(**await MetaAdsService(ctx).create_campaign(account_id, payload))


@router.patch(
    "/accounts/{account_id}/campaigns/{campaign_meta_id}",
    response_model=MetaAdsResult,
    dependencies=[require_permission(_WRITE)],
)
async def update_meta_ads_campaign(
    account_id: uuid.UUID,
    campaign_meta_id: str,
    payload: MetaAdsCampaignUpdate,
    ctx: RequestContext = Depends(require_context),
) -> MetaAdsResult:
    """Rename, reschedule, pause or archive a campaign. Its budget and switching it on are
    separate tools with separate permissions."""
    return MetaAdsResult(
        **await MetaAdsService(ctx).update_object(
            account_id, "campaigns", campaign_meta_id, payload
        )
    )


@router.post(
    "/accounts/{account_id}/adsets",
    response_model=MetaAdsResult,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission(_WRITE)],
)
async def create_meta_ads_adset(
    account_id: uuid.UUID,
    payload: MetaAdsAdSetCreate,
    ctx: RequestContext = Depends(require_context),
) -> MetaAdsResult:
    """Create an ad set: who sees the ads, and what they may cost. **Created paused.**

    For an audience in the EU the two names the Digital Services Act requires are filled in
    from the account's policy, its defaults at Meta or the client's legal name — and the
    create is refused, naming the field, when nothing says who they are.
    """
    return MetaAdsResult(**await MetaAdsService(ctx).create_adset(account_id, payload))


@router.patch(
    "/accounts/{account_id}/adsets/{adset_meta_id}",
    response_model=MetaAdsResult,
    dependencies=[require_permission(_WRITE)],
)
async def update_meta_ads_adset(
    account_id: uuid.UUID,
    adset_meta_id: str,
    payload: MetaAdsAdSetUpdate,
    ctx: RequestContext = Depends(require_context),
) -> MetaAdsResult:
    return MetaAdsResult(
        **await MetaAdsService(ctx).update_object(account_id, "adsets", adset_meta_id, payload)
    )


@router.post(
    "/accounts/{account_id}/images",
    response_model=MetaAdsImageRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission(_WRITE)],
)
async def upload_meta_ads_image(
    account_id: uuid.UUID,
    payload: MetaAdsImageUpload,
    ctx: RequestContext = Depends(require_context),
) -> MetaAdsImageRead:
    """Upload an image to the ad account, base64 in JSON, and get the ``hash`` a creative
    names it by."""
    return MetaAdsImageRead(**await MetaAdsService(ctx).upload_image(account_id, payload))


@router.post(
    "/accounts/{account_id}/creatives",
    response_model=MetaAdsResult,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission(_WRITE)],
)
async def create_meta_ads_creative(
    account_id: uuid.UUID,
    payload: MetaAdsCreativeCreate,
    ctx: RequestContext = Depends(require_context),
) -> MetaAdsResult:
    """Create what an ad shows: a link with words and a picture, or a post that is already
    live (``meta_post_id``). The Page it runs as must be the same client's as the ad account.

    A creative's words cannot be changed afterwards — that is Meta's rule — so changing
    copy means a new creative and pointing the ad at it.
    """
    return MetaAdsResult(**await MetaAdsService(ctx).create_creative(account_id, payload))


@router.post(
    "/accounts/{account_id}/ads",
    response_model=MetaAdsResult,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission(_WRITE)],
)
async def create_meta_ads_ad(
    account_id: uuid.UUID,
    payload: MetaAdsAdCreate,
    ctx: RequestContext = Depends(require_context),
) -> MetaAdsResult:
    """Create an ad from an ad set and a creative. **Created paused**; once switched on it
    passes Meta's review before it runs."""
    return MetaAdsResult(**await MetaAdsService(ctx).create_ad(account_id, payload))


@router.patch(
    "/accounts/{account_id}/ads/{ad_meta_id}",
    response_model=MetaAdsResult,
    dependencies=[require_permission(_WRITE)],
)
async def update_meta_ads_ad(
    account_id: uuid.UUID,
    ad_meta_id: str,
    payload: MetaAdsAdUpdate,
    ctx: RequestContext = Depends(require_context),
) -> MetaAdsResult:
    return MetaAdsResult(
        **await MetaAdsService(ctx).update_object(account_id, "ads", ad_meta_id, payload)
    )


@router.put(
    "/accounts/{account_id}/campaigns/{campaign_meta_id}/budget",
    response_model=MetaAdsResult,
    dependencies=[require_permission(_BUDGET)],
)
async def set_meta_ads_campaign_budget(
    account_id: uuid.UUID,
    campaign_meta_id: str,
    payload: MetaAdsBudgetWrite,
    ctx: RequestContext = Depends(require_context),
) -> MetaAdsResult:
    """Set a campaign's daily or lifetime budget, in **cents**.

    Judged against the account's guardrails first: a refusal names the limit
    (``details.limit``) and what was asked (``details.value``). Lowering a budget is never
    refused.
    """
    return MetaAdsResult(
        **await MetaAdsService(ctx).set_budget(account_id, "campaigns", campaign_meta_id, payload)
    )


@router.put(
    "/accounts/{account_id}/adsets/{adset_meta_id}/budget",
    response_model=MetaAdsResult,
    dependencies=[require_permission(_BUDGET)],
)
async def set_meta_ads_adset_budget(
    account_id: uuid.UUID,
    adset_meta_id: str,
    payload: MetaAdsBudgetWrite,
    ctx: RequestContext = Depends(require_context),
) -> MetaAdsResult:
    """Set an ad set's daily or lifetime budget, in **cents**. Meta allows four changes an
    hour; the fifth is refused here, with the number."""
    return MetaAdsResult(
        **await MetaAdsService(ctx).set_budget(account_id, "adsets", adset_meta_id, payload)
    )


def _kind(kind: str) -> str:
    if kind not in ("campaigns", "adsets", "ads"):
        raise AppError("not_found", "errors.not_found", status_code=404)
    return kind


@router.post(
    "/accounts/{account_id}/{kind}/{meta_id}/activate",
    response_model=MetaAdsResult,
    dependencies=[require_permission(_ACTIVATE)],
)
async def activate_meta_ads_object(
    account_id: uuid.UUID,
    kind: str,
    meta_id: str,
    payload: MetaAdsStatusWrite,
    ctx: RequestContext = Depends(require_context),
) -> MetaAdsResult:
    """Switch a campaign, ad set or ad **on**. From here it can spend the client's money.

    ``kind`` is ``campaigns``, ``adsets`` or ``ads``. An ad runs only while its ad set and
    its campaign are on too, so building everything paused and switching the campaign on
    last is the order that spends nothing by accident.
    """
    return MetaAdsResult(
        **await MetaAdsService(ctx).set_status(account_id, _kind(kind), meta_id, "ACTIVE", payload)
    )


@router.post(
    "/accounts/{account_id}/{kind}/{meta_id}/pause",
    response_model=MetaAdsResult,
    dependencies=[require_permission(_WRITE)],
)
async def pause_meta_ads_object(
    account_id: uuid.UUID,
    kind: str,
    meta_id: str,
    payload: MetaAdsStatusWrite,
    ctx: RequestContext = Depends(require_context),
) -> MetaAdsResult:
    """Pause a campaign, ad set or ad. It stops spending; nothing is deleted."""
    return MetaAdsResult(
        **await MetaAdsService(ctx).set_status(account_id, _kind(kind), meta_id, "PAUSED", payload)
    )


@router.post(
    "/accounts/{account_id}/boost",
    response_model=MetaAdsBoostResult,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission(_WRITE)],
)
async def boost_meta_post(
    account_id: uuid.UUID,
    payload: MetaAdsBoost,
    ctx: RequestContext = Depends(require_context),
) -> MetaAdsBoostResult:
    """Put a budget behind a post that is already live: a campaign, an ad set, a creative
    and an ad, made in that order and **all paused**. Needs the budget permission too.

    Not atomic — Meta has no transaction. If a later step is refused, the answer says which
    (``failed_step``) and names what was already made, all of it paused.
    """
    return MetaAdsBoostResult(**await MetaAdsService(ctx).boost(account_id, payload))
