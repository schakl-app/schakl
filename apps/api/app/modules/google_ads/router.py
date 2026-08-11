"""REST endpoints for google_ads under ``/api/v1/google-ads``. Business-licensed — see LICENSE.

Deny-by-default: every route declares one of the eight ``google_ads.*`` permissions (§15). No
route here carries ``no_permission_required`` and none should — ``test_rbac_deny_by_default``'s
exemption list has no google-ads entry, and needing one would mean something on this surface is
readable without a grant.

**These routes are the MCP surface.** Every ``/api/v1`` operation becomes an MCP tool, generated
from this app's own OpenAPI document and proxied in-process back through ``require_context``
(CLAUDE.md §12) — so the tool name is the handler's name shortened at ``_api_v1_``, and the
permission a key must carry is the one declared right here. That is why the handlers are named
for what an agent would ask for (``list_google_ads_accounts``, not ``list_accounts``, which
would collide with cloudflare's and fall back to the unreadable full operation id).
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query, Response, status

from app.core.googleads import format_customer_id
from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.modules.google_ads.models import GoogleAdsAccount
from app.modules.google_ads.reads import GoogleAdsReadService
from app.modules.google_ads.schemas import (
    GoogleAdsAccountCreate,
    GoogleAdsAccountRead,
    GoogleAdsAccountUpdate,
    GoogleAdsAvailableAccount,
    GoogleAdsKeywordIdeaRequest,
    GoogleAdsPickerRead,
    GoogleAdsQueryRead,
    GoogleAdsQueryRequest,
    GoogleAdsReport,
    GoogleAdsSettingsRead,
    GoogleAdsSettingsWrite,
    GoogleAdsSnapshotRead,
)
from app.modules.google_ads.service import GoogleAdsService

router = APIRouter(prefix="/google-ads", tags=["google_ads"])


def _read(row: GoogleAdsAccount, company_name: str | None = None) -> GoogleAdsAccountRead:
    """The response shape, written out.

    Deliberately **not** a sweep over ``row.__table__.columns``: that reads every column the
    table has, including the ones the schema does not want — and ``updated_at`` carries
    ``onupdate=func.now()``, so SQLAlchemy expires it after a flush and touching it from this
    synchronous helper fires a refresh SELECT with no greenlet to run it in. Enumerating is also
    what stops the response quietly growing a field the next migration adds (#304's rule, in the
    other direction: a payload expressed as "everything except" leaks whatever comes next).
    """
    return GoogleAdsAccountRead(
        id=row.id,
        customer_id=row.customer_id,
        customer_id_formatted=format_customer_id(row.customer_id),
        login_customer_id=row.login_customer_id,
        company_id=row.company_id,
        company_name=company_name,
        connection_id=row.connection_id,
        descriptive_name=row.descriptive_name,
        currency_code=row.currency_code,
        time_zone=row.time_zone,
        is_manager=row.is_manager,
        test_account=row.test_account,
        conversion_tracking_status=row.conversion_tracking_status,
        optimization_score=(
            float(row.optimization_score) if row.optimization_score is not None else None
        ),
        active=row.active,
        status=row.status,
        last_error=row.last_error,
        last_verified_at=row.last_verified_at,
        last_synced_at=row.last_synced_at,
    )


# --- settings (the credential — the highest-blast-radius surface here) ------------------- #
@router.get(
    "/settings",
    response_model=GoogleAdsSettingsRead,
    dependencies=[require_permission("google_ads.settings.manage")],
)
async def get_google_ads_settings(
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsSettingsRead:
    """The org's Ads configuration. The developer token is never part of the response."""
    from app.config import settings as app_settings

    service = GoogleAdsService(ctx)
    row = await service.settings_row()
    return GoogleAdsSettingsRead(
        developer_token_configured=bool(row and row.developer_token_encrypted),
        env_token_configured=bool((app_settings.google_ads_developer_token or "").strip()),
        default_login_customer_id=row.default_login_customer_id if row else None,
        writes_enabled=row.writes_enabled if row else True,
    )


@router.put(
    "/settings",
    response_model=GoogleAdsSettingsRead,
    dependencies=[require_permission("google_ads.settings.manage")],
)
async def save_google_ads_settings(
    payload: GoogleAdsSettingsWrite, ctx: RequestContext = Depends(require_context)
) -> GoogleAdsSettingsRead:
    await GoogleAdsService(ctx).save_settings(
        developer_token=payload.developer_token,
        # Absent and explicit-null are different answers and the payload alone cannot tell them
        # apart — only ``model_fields_set`` can (CLAUDE.md §18).
        developer_token_set="developer_token" in payload.model_fields_set,
        default_login_customer_id=payload.default_login_customer_id,
        writes_enabled=payload.writes_enabled,
    )
    return await get_google_ads_settings(ctx)


# --- accounts ---------------------------------------------------------------------------- #
@router.get(
    "/accounts",
    response_model=list[GoogleAdsAccountRead],
    dependencies=[require_permission("google_ads.account.read")],
)
async def list_google_ads_accounts(
    company_id: uuid.UUID | None = Query(default=None),
    active_only: bool = Query(default=False),
    ctx: RequestContext = Depends(require_context),
) -> list[GoogleAdsAccountRead]:
    """Every linked Google Ads account this caller may see — **start here**.

    The list an agent needs before anything else: it names the accounts, and every other tool
    takes one of these ids. Company-scoped logins see only the accounts of the clients in their
    horizon, and an account attached to no client (the agency's own) stays visible to all.
    """
    rows = await GoogleAdsService(ctx).list_accounts(company_id=company_id, active_only=active_only)
    return [_read(row) for row in rows]


@router.get(
    "/accounts/available",
    response_model=GoogleAdsPickerRead,
    dependencies=[require_permission("google_ads.settings.manage")],
)
async def list_available_google_ads_accounts(
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsPickerRead:
    """Accounts the caller's own Google grant can reach, manager hierarchies expanded.

    Live — this is the one read that calls Google on every request, because a picker showing a
    stale account list is how someone links an account that was closed last month.
    """
    result = await GoogleAdsService(ctx).available_accounts()
    return GoogleAdsPickerRead(
        accounts=[
            GoogleAdsAvailableAccount(
                **{
                    **option.__dict__,
                    "customer_id_formatted": format_customer_id(option.customer_id),
                }
            )
            for option in result.accounts
        ],
        warnings=list(result.warnings),
    )


@router.get(
    "/accounts/{account_id}",
    response_model=GoogleAdsAccountRead,
    dependencies=[require_permission("google_ads.account.read")],
)
async def get_google_ads_account(
    account_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> GoogleAdsAccountRead:
    return _read(await GoogleAdsService(ctx).get_account(account_id))


@router.post(
    "/accounts",
    response_model=GoogleAdsAccountRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission("google_ads.settings.manage")],
)
async def link_google_ads_account(
    payload: GoogleAdsAccountCreate, ctx: RequestContext = Depends(require_context)
) -> GoogleAdsAccountRead:
    """Link an Ads account to a client.

    The linking user's Google connection is stamped on the row: it is the grant that will sync
    it, and an account whose connection is later removed goes dormant and asks to be
    reconnected rather than silently syncing as somebody else.
    """
    from app.modules.google import client as google_client

    connection = await google_client.connection_for(ctx.session, ctx.org.id, ctx.user.id)
    row = await GoogleAdsService(ctx).attach(
        customer_id=payload.customer_id,
        company_id=payload.company_id,
        login_customer_id=payload.login_customer_id,
        connection_id=connection.id if connection else None,
        descriptive_name=payload.descriptive_name,
        currency_code=payload.currency_code,
    )
    return _read(row)


@router.patch(
    "/accounts/{account_id}",
    response_model=GoogleAdsAccountRead,
    dependencies=[require_permission("google_ads.settings.manage")],
)
async def update_google_ads_account(
    account_id: uuid.UUID,
    payload: GoogleAdsAccountUpdate,
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsAccountRead:
    service = GoogleAdsService(ctx)
    row = await service.get_account(account_id)
    await service.update_account(
        row,
        company_id=payload.company_id,
        login_customer_id=payload.login_customer_id,
        active=payload.active,
        company_id_set="company_id" in payload.model_fields_set,
    )
    return _read(row)


@router.delete(
    "/accounts/{account_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_permission("google_ads.settings.manage")],
)
async def unlink_google_ads_account(
    account_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> Response:
    """Deactivate the link. The row survives: history hangs off it, and a re-link must find the
    same account rather than collide with its own unique constraint."""
    await GoogleAdsService(ctx).unlink(account_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/accounts/{account_id}/verify",
    response_model=GoogleAdsAccountRead,
    dependencies=[require_permission("google_ads.settings.manage")],
)
async def verify_google_ads_account(
    account_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> GoogleAdsAccountRead:
    """Ask Google what it says about this account, and record the answer either way.

    Never raises for a refusal: the outcome *is* the payload (``status``, ``last_error``), which
    is what lets a screen say "the grant was revoked" instead of showing a red toast with no
    detail. A success clears the flag it may have set last time.
    """
    return _read(await GoogleAdsService(ctx).verify(account_id))


# --- reads ------------------------------------------------------------------------------------ #
#
# Every one of these is an MCP tool. Three things follow, and they are why the signatures look
# the way they do:
#
# * **The docstring is the tool description an agent reads to decide whether to call it.** It
#   says what the read answers and what it does not, because a model cannot see this file.
# * **The parameters are the tool's arguments**, so they are named for the question rather than
#   for the GAQL: ``period`` and ``campaigns``, not ``segments_date`` and ``campaign_id_in``.
# * **`warnings` on the response is load-bearing**, not decoration. Truncation, a shortened
#   change window and a geo read that fell back to country level are reported there and nowhere
#   else.


def _period_params(
    period: str | None = Query(
        default=None,
        description=(
            "A named span: 30d, 90d, month, last_month, quarter, last_quarter, 2026-07, "
            "2026-Q3. Resolved in the account's own timezone and always ending yesterday. "
            "Ignored when date_from and date_to are both given."
        ),
    ),
    date_from: date | None = Query(default=None, description="YYYY-MM-DD, inclusive."),
    date_to: date | None = Query(default=None, description="YYYY-MM-DD, inclusive."),
) -> tuple[str | None, date | None, date | None]:
    return period, date_from, date_to


@router.get(
    "/accounts/{account_id}/snapshot",
    response_model=GoogleAdsSnapshotRead,
    dependencies=[require_permission("google_ads.account.read")],
)
async def google_ads_snapshot(
    account_id: uuid.UUID,
    window: tuple[str | None, date | None, date | None] = Depends(_period_params),
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsSnapshotRead:
    """Account totals plus every campaign for the period — **start an analysis here**.

    Answers "how is this account doing": what it spent, what it got, which campaigns are
    responsible, what each is bidding toward, and how much of the available impressions it is
    losing to budget versus to rank. Costs are in the account's own currency and CTR is a
    fraction (0.0453 = 4,53 %).
    """
    return await GoogleAdsReadService(ctx).snapshot(account_id, *window)


@router.get(
    "/accounts/{account_id}/campaigns",
    response_model=GoogleAdsReport,
    dependencies=[require_permission("google_ads.account.read")],
)
async def google_ads_campaigns(
    account_id: uuid.UUID,
    window: tuple[str | None, date | None, date | None] = Depends(_period_params),
    campaigns: list[str] | None = Query(
        default=None, description="Filter by campaign name, case-insensitive substring match."
    ),
    include_removed: bool = Query(
        default=False,
        description=(
            "Include removed campaigns. Off by default: a list where a third of the rows cannot "
            "be acted on is a worse answer to 'what are we running'. Turn it on to ask what was "
            "spent on things since removed."
        ),
    ),
    limit: int | None = Query(default=None, ge=1),
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsReport:
    """Campaign performance and settings, most expensive first.

    Impression-share fields are null on Display, Video and Performance Max campaigns because
    Google does not report them there — which is not the same claim as 0 % visibility.
    """
    return await GoogleAdsReadService(ctx).campaigns(
        account_id, *window, campaigns=campaigns, include_removed=include_removed, limit=limit
    )


@router.get(
    "/accounts/{account_id}/ad-groups",
    response_model=GoogleAdsReport,
    dependencies=[require_permission("google_ads.account.read")],
)
async def google_ads_ad_groups(
    account_id: uuid.UUID,
    window: tuple[str | None, date | None, date | None] = Depends(_period_params),
    campaigns: list[str] | None = Query(default=None),
    include_removed: bool = Query(default=False),
    limit: int | None = Query(default=None, ge=1),
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsReport:
    """Ad-group performance, most expensive first. One level below campaigns."""
    return await GoogleAdsReadService(ctx).ad_groups(
        account_id, *window, campaigns=campaigns, include_removed=include_removed, limit=limit
    )


@router.get(
    "/accounts/{account_id}/keywords",
    response_model=GoogleAdsReport,
    dependencies=[require_permission("google_ads.account.read")],
)
async def google_ads_keywords(
    account_id: uuid.UUID,
    window: tuple[str | None, date | None, date | None] = Depends(_period_params),
    campaigns: list[str] | None = Query(default=None),
    include_removed: bool = Query(default=False),
    limit: int | None = Query(default=None, ge=1),
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsReport:
    """Positive keywords with match type, bid and Quality Score, most expensive first.

    An absent quality_score means Google has not computed one yet (too few impressions), not a
    score of zero. For what is *excluded*, use the negatives read; for what people actually
    typed, use search terms.
    """
    return await GoogleAdsReadService(ctx).keywords(
        account_id, *window, campaigns=campaigns, include_removed=include_removed, limit=limit
    )


@router.get(
    "/accounts/{account_id}/negatives",
    response_model=GoogleAdsReport,
    dependencies=[require_permission("google_ads.account.read")],
)
async def google_ads_negatives(
    account_id: uuid.UUID,
    limit: int | None = Query(default=None, ge=1),
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsReport:
    """Every negative keyword: ad-group level, campaign level, and shared negative lists.

    Three resources in one answer, because Google models them as three things and an agency asks
    one question. Each row carries a `level` saying which it came from. Configuration, so there
    is no period and no metrics: an exclusion either exists or it does not.
    """
    return await GoogleAdsReadService(ctx).negatives(account_id, limit=limit)


@router.get(
    "/accounts/{account_id}/search-terms",
    response_model=GoogleAdsReport,
    dependencies=[require_permission("google_ads.account.read")],
)
async def google_ads_search_terms(
    account_id: uuid.UUID,
    window: tuple[str | None, date | None, date | None] = Depends(_period_params),
    campaigns: list[str] | None = Query(default=None),
    min_cost: float | None = Query(
        default=None, ge=0, description="Only terms that cost at least this, in account currency."
    ),
    min_clicks: int | None = Query(default=None, ge=0),
    limit: int | None = Query(default=None, ge=1),
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsReport:
    """What people actually typed, most expensive first.

    `match_status` says what has already been decided about each term: ADDED (it is a keyword),
    EXCLUDED (it is a negative), ADDED_EXCLUDED, or NONE. This is **raw and unclassified** — the
    API labels nothing as a candidate negative, and a term costing money with no conversions may
    still be a term worth keeping.
    """
    return await GoogleAdsReadService(ctx).search_terms(
        account_id,
        *window,
        campaigns=campaigns,
        min_cost=min_cost,
        min_clicks=min_clicks,
        limit=limit,
    )


@router.get(
    "/accounts/{account_id}/ads",
    response_model=GoogleAdsReport,
    dependencies=[require_permission("google_ads.account.read")],
)
async def google_ads_ads(
    account_id: uuid.UUID,
    window: tuple[str | None, date | None, date | None] = Depends(_period_params),
    campaigns: list[str] | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1),
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsReport:
    """Ads with their ad strength and policy approval status, most expensive first."""
    return await GoogleAdsReadService(ctx).ads(
        account_id, *window, campaigns=campaigns, limit=limit
    )


@router.get(
    "/accounts/{account_id}/devices",
    response_model=GoogleAdsReport,
    dependencies=[require_permission("google_ads.account.read")],
)
async def google_ads_devices(
    account_id: uuid.UUID,
    window: tuple[str | None, date | None, date | None] = Depends(_period_params),
    campaigns: list[str] | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1),
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsReport:
    """Performance per device, per campaign, plus an account-wide rollup in `extra.device_totals`.

    A large cost-per-conversion gap between devices *within one campaign* is the strongest
    signal this read produces.
    """
    return await GoogleAdsReadService(ctx).devices(
        account_id, *window, campaigns=campaigns, limit=limit
    )


@router.get(
    "/accounts/{account_id}/geo",
    response_model=GoogleAdsReport,
    dependencies=[require_permission("google_ads.account.read")],
)
async def google_ads_geo(
    account_id: uuid.UUID,
    window: tuple[str | None, date | None, date | None] = Depends(_period_params),
    campaigns: list[str] | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1),
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsReport:
    """Where the people who saw the ads physically were — not where the campaign targets.

    That difference is the point: traffic from outside the targeted area is what this read
    exists to surface. **Check `extra.granularity` before using region or city** — some accounts
    cannot segment below country, and the read falls back rather than failing.
    """
    return await GoogleAdsReadService(ctx).geo(
        account_id, *window, campaigns=campaigns, limit=limit
    )


@router.get(
    "/accounts/{account_id}/conversions",
    response_model=GoogleAdsReport,
    dependencies=[require_permission("google_ads.account.read")],
)
async def google_ads_conversion_health(
    account_id: uuid.UUID,
    window: tuple[str | None, date | None, date | None] = Depends(_period_params),
    limit: int | None = Query(default=None, ge=1),
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsReport:
    """What this account optimises toward, and what each conversion action actually recorded.

    Answers "is the money being steered by something real". `primary_for_goal` and
    `counts_toward_conversions` are the two fields that decide whether an action influences
    bidding at all. This is Google Ads *configuration* and measured counts — it says nothing
    about whether those conversions became customers.
    """
    return await GoogleAdsReadService(ctx).conversions(account_id, *window, limit=limit)


@router.get(
    "/accounts/{account_id}/changes",
    response_model=GoogleAdsReport,
    dependencies=[require_permission("google_ads.account.read")],
)
async def google_ads_changes(
    account_id: uuid.UUID,
    window: tuple[str | None, date | None, date | None] = Depends(_period_params),
    limit: int | None = Query(default=None, ge=1),
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsReport:
    """What was changed in the account, with each field's old and new value.

    Two hard limits, both reported in the response: Google keeps change history for **30 days
    only** (`extra.effective_period` shows what was really read), and **automatic changes made
    by Google itself — Smart Bidding above all — appear nowhere in it**. Do not build an audit
    trail on this alone.
    """
    return await GoogleAdsReadService(ctx).changes(account_id, *window, limit=limit)


@router.get(
    "/accounts/{account_id}/recommendations",
    response_model=GoogleAdsReport,
    dependencies=[require_permission("google_ads.account.read")],
)
async def google_ads_recommendations(
    account_id: uuid.UUID,
    limit: int | None = Query(default=None, ge=1),
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsReport:
    """Google's own suggestions for this account, with the impact it projects for each.

    Advice rather than data, and worth reading before inferring the same thing from metrics.
    Dismissed recommendations are excluded — somebody already decided about those.
    """
    return await GoogleAdsReadService(ctx).recommendations(account_id, limit=limit)


@router.post(
    "/accounts/{account_id}/keyword-ideas",
    response_model=GoogleAdsReport,
    dependencies=[require_permission("google_ads.account.read")],
)
async def google_ads_keyword_ideas(
    account_id: uuid.UUID,
    payload: GoogleAdsKeywordIdeaRequest,
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsReport:
    """Keyword ideas with search volume and competition, from seed terms or a landing page.

    A POST because it takes a body, not because it writes anything: nothing in the account
    changes. Volumes are Google's own estimates and are banded, not exact.
    """
    return await GoogleAdsReadService(ctx).keyword_ideas(account_id, payload)


@router.post(
    "/accounts/{account_id}/query",
    response_model=GoogleAdsQueryRead,
    dependencies=[require_permission("google_ads.query.run")],
)
async def google_ads_query(
    account_id: uuid.UUID,
    payload: GoogleAdsQueryRequest,
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsQueryRead:
    """Run a GAQL query against this account — the escape hatch for questions the other tools
    do not answer.

    Read-only by construction: GAQL has no write syntax. The customer is taken from the linked
    account in the path and can never be named in the query, so no query reaches an advertiser
    this workspace has not linked. A LIMIT is imposed if you do not give one and clamped if it
    is too large, and a query selecting metrics must bound `segments.date`.

    Example: `SELECT campaign.name, metrics.cost_micros FROM campaign
    WHERE segments.date DURING LAST_30_DAYS ORDER BY metrics.cost_micros DESC`
    """
    return await GoogleAdsReadService(ctx).query(account_id, payload)
