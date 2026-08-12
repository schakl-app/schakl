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

import logging
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Response, status

from app.core.googleads import format_customer_id
from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.modules.google_ads.decisions import GoogleAdsDecisionService, GoogleAdsPolicyService
from app.modules.google_ads.models import GoogleAdsAccount
from app.modules.google_ads.reads import GoogleAdsReadService
from app.modules.google_ads.reporting import Slice
from app.modules.google_ads.schemas import (
    GoogleAdsAccountBrief,
    GoogleAdsAccountCreate,
    GoogleAdsAccountRead,
    GoogleAdsAccountUpdate,
    GoogleAdsAdCreate,
    GoogleAdsAdGroupCreate,
    GoogleAdsAdGroupUpdate,
    GoogleAdsAdUpdate,
    GoogleAdsAvailableAccount,
    GoogleAdsBudgetCreate,
    GoogleAdsBudgetUpdate,
    GoogleAdsCampaignCreate,
    GoogleAdsCampaignUpdate,
    GoogleAdsDecisionCreate,
    GoogleAdsDecisionPage,
    GoogleAdsDecisionRead,
    GoogleAdsKeywordIdeaRequest,
    GoogleAdsKeywordsAdd,
    GoogleAdsKeywordsRemove,
    GoogleAdsKeywordUpdate,
    GoogleAdsMutationRead,
    GoogleAdsNegativeListCreate,
    GoogleAdsNegativesAdd,
    GoogleAdsNegativesRemove,
    GoogleAdsPickerRead,
    GoogleAdsPolicyRead,
    GoogleAdsPolicyWrite,
    GoogleAdsQueryRead,
    GoogleAdsQueryRequest,
    GoogleAdsReport,
    GoogleAdsSettingsRead,
    GoogleAdsSettingsWrite,
    GoogleAdsSnapshotRead,
    GoogleAdsTrendRead,
)
from app.modules.google_ads.service import GoogleAdsService
from app.modules.google_ads.writes import GoogleAdsWriteService

logger = logging.getLogger("schakl.googleads")

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
    from app.core.jobs import enqueue
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
    # Fill thirteen months in the background, so a year-over-year comparison works the day after
    # linking rather than a year after. Deferred so this transaction has committed before the
    # job reads the row, and keyed so re-linking does not queue a second one. A queue miss is
    # not fatal — the nightly run catches up — so it is logged rather than failing the link the
    # user actually asked for.
    try:
        await enqueue(
            "google_ads_backfill_account",
            str(ctx.org.id),
            str(row.id),
            _defer_by=timedelta(seconds=5),
            _job_id=f"google-ads-backfill-{row.id}",
        )
    except Exception:  # noqa: BLE001 — a nicety this request rides on, never its purpose
        logger.warning("could not enqueue google ads backfill for account %s", row.id)
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


def _slice_params(
    q: str | None = Query(
        default=None,
        description=(
            "Free text, matched case-insensitively against the row's own readable fields — the "
            "campaign or ad-group name, the keyword, the search term, the place. Applied to the "
            "whole list before the page is taken, so page 2 of a search is page 2 of the search."
        ),
    ),
    limit: int | None = Query(
        default=None,
        ge=1,
        description=(
            "How many rows this page holds. Omit for the rest of the list, which is what a "
            "caller with no pager means. Never more than the read's own ceiling."
        ),
    ),
    offset: int = Query(
        default=0, ge=0, description="Where the page starts. `total_rows` is what it runs to."
    ),
) -> Slice:
    """The filter and the page, as one argument.

    Separate from ``_period_params`` because they are separate questions: the period says which
    days Google is asked about and costs a call, while these three say which part of the answer
    to hand back and cost nothing. Every list read takes them, so a screen that grew past a
    screenful gains real paging rather than a "showing the first 500" apology (CLAUDE.md §9).
    """
    return Slice(search=q, offset=offset, limit=limit)


def _status_param(
    status: str | None = Query(
        default=None,
        description=(
            "Only rows with this Google status: ENABLED, PAUSED or REMOVED. REMOVED implies "
            "`include_removed`, because a filter that always answers nothing is not a filter."
        ),
    ),
    view: Slice = Depends(_slice_params),
) -> Slice:
    """The same slice, carrying a status — for the reads whose rows actually have one.

    Declared as its own dependency rather than a field on every read's parameters: asking a
    negative-keyword list or a change history to filter by status would answer nothing at all,
    silently, which is worse than not offering the control.
    """
    return replace(view, status=status)


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
    "/accounts/{account_id}/trend",
    response_model=GoogleAdsTrendRead,
    dependencies=[require_permission("google_ads.account.read")],
)
async def google_ads_trend(
    account_id: uuid.UUID,
    window: tuple[str | None, date | None, date | None] = Depends(_period_params),
    compare: str | None = Query(
        default=None,
        description=(
            "What to compare against: 'year' (the same period a year earlier, the default) or "
            "'previous' (the period immediately before)."
        ),
    ),
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsTrendRead:
    """A period against its comparison, with the change per metric already computed.

    Answered from schakl's own nightly mirror — **this makes no call to Google**, so it is fast,
    costs no API quota and works when Google is down. The trade is that it only knows what has
    been synced: `missing_days` says how many days of the window have no stored row, which means
    "not synced yet", never "no spend".

    The comparison defaults to the same period a year earlier, because that is the comparison
    seasonality survives — a campsite's July has nothing to say to its June. Both windows' dates
    are in the payload, so a percentage is always checkable.
    """
    return await GoogleAdsReadService(ctx).trend(account_id, *window, compare=compare)


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
    view: Slice = Depends(_status_param),
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsReport:
    """Campaign performance and settings, most expensive first.

    Impression-share fields are null on Display, Video and Performance Max campaigns because
    Google does not report them there — which is not the same claim as 0 % visibility.
    """
    return await GoogleAdsReadService(ctx).campaigns(
        account_id, *window, campaigns=campaigns, include_removed=include_removed, view=view
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
    view: Slice = Depends(_status_param),
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsReport:
    """Ad-group performance, most expensive first. One level below campaigns."""
    return await GoogleAdsReadService(ctx).ad_groups(
        account_id, *window, campaigns=campaigns, include_removed=include_removed, view=view
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
    view: Slice = Depends(_status_param),
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsReport:
    """Positive keywords with match type, bid and Quality Score, most expensive first.

    An absent quality_score means Google has not computed one yet (too few impressions), not a
    score of zero. For what is *excluded*, use the negatives read; for what people actually
    typed, use search terms.
    """
    return await GoogleAdsReadService(ctx).keywords(
        account_id, *window, campaigns=campaigns, include_removed=include_removed, view=view
    )


@router.get(
    "/accounts/{account_id}/negatives",
    response_model=GoogleAdsReport,
    dependencies=[require_permission("google_ads.account.read")],
)
async def google_ads_negatives(
    account_id: uuid.UUID,
    view: Slice = Depends(_slice_params),
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsReport:
    """Every negative keyword: ad-group level, campaign level, and shared negative lists.

    Three resources in one answer, because Google models them as three things and an agency asks
    one question. Each row carries a `level` saying which it came from. Configuration, so there
    is no period and no metrics: an exclusion either exists or it does not.
    """
    return await GoogleAdsReadService(ctx).negatives(account_id, view=view)


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
    view: Slice = Depends(_slice_params),
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
        view=view,
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
    view: Slice = Depends(_status_param),
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsReport:
    """Ads with their ad strength and policy approval status, most expensive first."""
    return await GoogleAdsReadService(ctx).ads(account_id, *window, campaigns=campaigns, view=view)


@router.get(
    "/accounts/{account_id}/devices",
    response_model=GoogleAdsReport,
    dependencies=[require_permission("google_ads.account.read")],
)
async def google_ads_devices(
    account_id: uuid.UUID,
    window: tuple[str | None, date | None, date | None] = Depends(_period_params),
    campaigns: list[str] | None = Query(default=None),
    view: Slice = Depends(_slice_params),
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsReport:
    """Performance per device, per campaign, plus an account-wide rollup in `extra.device_totals`.

    A large cost-per-conversion gap between devices *within one campaign* is the strongest
    signal this read produces.
    """
    return await GoogleAdsReadService(ctx).devices(
        account_id, *window, campaigns=campaigns, view=view
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
    view: Slice = Depends(_slice_params),
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsReport:
    """Where the people who saw the ads physically were — not where the campaign targets.

    That difference is the point: traffic from outside the targeted area is what this read
    exists to surface. **Check `extra.granularity` before using region or city** — some accounts
    cannot segment below country, and the read falls back rather than failing.
    """
    return await GoogleAdsReadService(ctx).geo(account_id, *window, campaigns=campaigns, view=view)


@router.get(
    "/accounts/{account_id}/conversions",
    response_model=GoogleAdsReport,
    dependencies=[require_permission("google_ads.account.read")],
)
async def google_ads_conversion_health(
    account_id: uuid.UUID,
    window: tuple[str | None, date | None, date | None] = Depends(_period_params),
    view: Slice = Depends(_slice_params),
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsReport:
    """What this account optimises toward, and what each conversion action actually recorded.

    Answers "is the money being steered by something real". `primary_for_goal` and
    `counts_toward_conversions` are the two fields that decide whether an action influences
    bidding at all. This is Google Ads *configuration* and measured counts — it says nothing
    about whether those conversions became customers.
    """
    return await GoogleAdsReadService(ctx).conversions(account_id, *window, view=view)


@router.get(
    "/accounts/{account_id}/changes",
    response_model=GoogleAdsReport,
    dependencies=[require_permission("google_ads.account.read")],
)
async def google_ads_changes(
    account_id: uuid.UUID,
    window: tuple[str | None, date | None, date | None] = Depends(_period_params),
    view: Slice = Depends(_slice_params),
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsReport:
    """What was changed in the account, with each field's old and new value.

    Two hard limits, both reported in the response: Google keeps change history for **30 days
    only** (`extra.effective_period` shows what was really read), and **automatic changes made
    by Google itself — Smart Bidding above all — appear nowhere in it**. Do not build an audit
    trail on this alone.
    """
    return await GoogleAdsReadService(ctx).changes(account_id, *window, view=view)


@router.get(
    "/accounts/{account_id}/recommendations",
    response_model=GoogleAdsReport,
    dependencies=[require_permission("google_ads.account.read")],
)
async def google_ads_recommendations(
    account_id: uuid.UUID,
    view: Slice = Depends(_slice_params),
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsReport:
    """Google's own suggestions for this account, with the impact it projects for each.

    Advice rather than data, and worth reading before inferring the same thing from metrics.
    Dismissed recommendations are excluded — somebody already decided about those.
    """
    return await GoogleAdsReadService(ctx).recommendations(account_id, view=view)


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


# --- the policy, and what has already been decided ------------------------------------------- #
#
# Phase 4: the records that turn the read surface from a data pipe into something an agent can
# reason with. Reading the log is `account.read` — it is context every proposal needs, and the
# curated tools subtract it. *Recording* a standing decision is `policy.manage`, because it
# changes what will be proposed next time, which the permission's own docstring says is not a
# read. The write routes below record their own decisions under their own keys: recording is a
# side effect of a write the caller was already allowed to make, never its own grant (§16).


def _policy_read(row: Any, resolved: Any, account_id: uuid.UUID | None) -> GoogleAdsPolicyRead:
    stored = row is not None
    return GoogleAdsPolicyRead(
        account_id=account_id,
        stored=stored,
        protected_terms=list(getattr(row, "protected_terms", []) or []),
        banned_phrases=list(getattr(row, "banned_phrases", []) or []),
        always_exclude=list(getattr(row, "always_exclude", []) or []),
        max_daily_budget=_number(getattr(row, "max_daily_budget", None)),
        max_budget_increase_pct=_number(getattr(row, "max_budget_increase_pct", None)),
        max_cpc=_number(getattr(row, "max_cpc", None)),
        waste_min_cost=_number(getattr(row, "waste_min_cost", None)),
        waste_min_clicks=getattr(row, "waste_min_clicks", None),
        steering=getattr(row, "steering", "") or "",
        ad_copy_rules=getattr(row, "ad_copy_rules", "") or "",
        resolved=resolved.as_payload(),
    )


def _number(raw: Any) -> float | None:
    """A ``Numeric`` column as a float. ``None`` stays ``None`` — it means *inherit*, not zero."""
    return None if raw is None else float(raw)


@router.get(
    "/policy",
    response_model=GoogleAdsPolicyRead,
    dependencies=[require_permission("google_ads.policy.manage")],
)
async def get_google_ads_house_policy(
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsPolicyRead:
    """The agency's own standing Ads rules, applied to every account that does not override them.

    Protected terms and banned phrases here are **added** to each account's rather than replaced
    by them; the numeric ceilings are inherited and an account may set its own. `resolved` shows
    what an account with no policy of its own would get.
    """
    service = GoogleAdsPolicyService(ctx)
    return _policy_read(await service.get(None), await service.resolve(None), None)


@router.put(
    "/policy",
    response_model=GoogleAdsPolicyRead,
    dependencies=[require_permission("google_ads.policy.manage")],
)
async def save_google_ads_house_policy(
    payload: GoogleAdsPolicyWrite, ctx: RequestContext = Depends(require_context)
) -> GoogleAdsPolicyRead:
    """Set the agency's standing rules. Fields you leave out are not touched."""
    service = GoogleAdsPolicyService(ctx)
    await service.save(None, payload.model_dump(include=payload.model_fields_set))
    return _policy_read(await service.get(None), await service.resolve(None), None)


@router.get(
    "/accounts/{account_id}/policy",
    response_model=GoogleAdsPolicyRead,
    dependencies=[require_permission("google_ads.policy.manage")],
)
async def get_google_ads_policy(
    account_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> GoogleAdsPolicyRead:
    """The rules that bind writes to this account — **read this before proposing anything**.

    `resolved` is what is actually enforced: the agency's house policy and this account's, folded
    together. `protected_terms` may never be excluded, `banned_phrases` may not appear in ad copy,
    and the three ceilings refuse a budget or a bid outright. The prose fields are advice, and the
    agency's and the account's are kept apart because they are different kinds of claim.
    """
    service = GoogleAdsPolicyService(ctx)
    await GoogleAdsService(ctx).get_account(account_id)
    return _policy_read(
        await service.get(account_id), await service.resolve(account_id), account_id
    )


@router.put(
    "/accounts/{account_id}/policy",
    response_model=GoogleAdsPolicyRead,
    dependencies=[require_permission("google_ads.policy.manage")],
)
async def save_google_ads_policy(
    account_id: uuid.UUID,
    payload: GoogleAdsPolicyWrite,
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsPolicyRead:
    """Set this account's own rules.

    A field left out of the request is not touched; a field sent explicitly as `null` goes back to
    inheriting the agency's house value. Those are different instructions and the payload alone
    cannot tell them apart, which is why only what you send is read.
    """
    service = GoogleAdsPolicyService(ctx)
    await service.save(account_id, payload.model_dump(include=payload.model_fields_set))
    return _policy_read(
        await service.get(account_id), await service.resolve(account_id), account_id
    )


@router.get(
    "/accounts/{account_id}/decisions",
    response_model=GoogleAdsDecisionPage,
    dependencies=[require_permission("google_ads.account.read")],
)
async def list_google_ads_decisions(
    account_id: uuid.UUID,
    subject_type: str | None = Query(default=None),
    decision: str | None = Query(default=None),
    include_withdrawn: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    count: bool = Query(default=True, description="Set false to skip the total."),
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsDecisionPage:
    """What has already been decided about this account — **check this before proposing a change**.

    Newest first, and the newest row about a subject is the one that stands: a term excluded in
    March and un-excluded in June has two entries and the June one wins. Withdrawn and expired
    entries no longer stand.

    The point of this list is that a recommendation is not made twice. A search term somebody
    deliberately kept, with the reason written down, is not a candidate for exclusion next month.
    """
    await GoogleAdsService(ctx).get_account(account_id)
    rows, total = await GoogleAdsDecisionService(ctx).page(
        account_id,
        limit=limit,
        offset=offset,
        subject_type=subject_type,
        decision=decision,
        include_withdrawn=include_withdrawn,
        count=count,
    )
    return GoogleAdsDecisionPage(
        items=[GoogleAdsDecisionRead.model_validate(row) for row in rows], total=total
    )


@router.post(
    "/accounts/{account_id}/decisions",
    response_model=GoogleAdsDecisionRead | None,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission("google_ads.policy.manage")],
)
async def record_google_ads_decision(
    account_id: uuid.UUID,
    payload: GoogleAdsDecisionCreate,
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsDecisionRead | None:
    """Write down a judgement that changes nothing in Google.

    The one this exists for is `kept`: "we looked at this search term and chose not to exclude
    it". Nothing in Google records that, which is exactly why the same term is proposed again next
    month, and the month after.

    Returns `null` when the same decision already stood — nothing was appended, and saying so is
    more useful than claiming a write.
    """
    await GoogleAdsService(ctx).get_account(account_id)
    ctx.require("google_ads.policy.manage")
    row = await GoogleAdsDecisionService(ctx).record(
        account_id,
        subject_type=payload.subject_type,
        subject=payload.subject,
        decision=payload.decision,
        scope=payload.scope,
        reason=payload.reason,
        expires_on=payload.expires_on,
        source="manual",
    )
    return GoogleAdsDecisionRead.model_validate(row) if row is not None else None


@router.delete(
    "/accounts/{account_id}/decisions/{decision_id}",
    response_model=GoogleAdsDecisionRead,
    dependencies=[require_permission("google_ads.policy.manage")],
)
async def withdraw_google_ads_decision(
    account_id: uuid.UUID,
    decision_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsDecisionRead:
    """Unsay a decision. The row survives, marked withdrawn and by whom.

    A delete would take the reason with it, and "we decided this and then changed our minds" is
    the sentence this log exists to be able to make.
    """
    await GoogleAdsService(ctx).get_account(account_id)
    row = await GoogleAdsDecisionService(ctx).withdraw(account_id, decision_id)
    return GoogleAdsDecisionRead.model_validate(row)


# --- the write surface -------------------------------------------------------------------------- #
#
# Phase 5. Four permissions, not one, because this surface is reached over MCP by an agent holding
# an API key and a key carries permission *scopes*: split, an agency can mint a key that tidies
# search terms overnight and can never touch a budget. Beside them stands one instance-wide kill
# switch (`google_ads_settings.writes_enabled`) — the permission decides who, the switch decides
# whether, and an owner who has just watched an agent do something surprising needs one lever.
#
# Every one of these takes `validate_only`. It is the real dry run: Google validates against the
# actual account structure and applies nothing, which a test account cannot do because it serves
# no ads and therefore holds no campaigns worth validating against.


def _mutation(outcome: Any, account: GoogleAdsAccount) -> GoogleAdsMutationRead:
    return GoogleAdsMutationRead(
        account=GoogleAdsAccountBrief(
            id=account.id,
            customer_id=account.customer_id,
            customer_id_formatted=format_customer_id(account.customer_id),
            descriptive_name=account.descriptive_name,
            company_id=account.company_id,
        ),
        resource=outcome.resource,
        validate_only=outcome.validate_only,
        requested=outcome.requested,
        applied=outcome.applied,
        results=outcome.results,
        skipped=outcome.skipped,
        warnings=list(dict.fromkeys(outcome.warnings)),
        fetched_at=datetime.now(UTC),
    )


@router.post(
    "/accounts/{account_id}/budgets",
    response_model=GoogleAdsMutationRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission("google_ads.budget.write")],
)
async def create_google_ads_budget(
    account_id: uuid.UUID,
    payload: GoogleAdsBudgetCreate,
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsMutationRead:
    """Create a daily budget, in the account's own currency.

    A budget on its own spends nothing — a campaign has to be attached to it. Note what does
    *not* bound this: the policy's relative ceiling is a claim about a change, and a new budget
    has no previous amount, so unless the account sets `max_daily_budget` the only limit here is
    the permission. Check `GET /policy` first.
    """
    outcome, account = await GoogleAdsWriteService(ctx).create_budget(
        account_id,
        name=payload.name,
        amount=payload.amount,
        shared=payload.shared,
        validate_only=payload.validate_only,
    )
    return _mutation(outcome, account)


@router.patch(
    "/accounts/{account_id}/budgets/{budget_id}",
    response_model=GoogleAdsMutationRead,
    dependencies=[require_permission("google_ads.budget.write")],
)
async def update_google_ads_budget(
    account_id: uuid.UUID,
    budget_id: str,
    payload: GoogleAdsBudgetUpdate,
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsMutationRead:
    """Change a daily budget — the one write where being wrong has already cost money.

    Two limits apply: an absolute ceiling if the policy sets one, and how far a single change may
    *raise* the budget (by default it may at most double). A decrease is never refused.

    If the budget is shared, changing it moves **every campaign using it**, and the response says
    so in `warnings`. Read the campaigns list first if you are not sure.
    """
    outcome, account = await GoogleAdsWriteService(ctx).update_budget(
        account_id,
        budget_id,
        amount=payload.amount,
        name=payload.name,
        validate_only=payload.validate_only,
    )
    return _mutation(outcome, account)


@router.post(
    "/accounts/{account_id}/campaigns",
    response_model=GoogleAdsMutationRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission("google_ads.campaign.write")],
)
async def create_google_ads_campaign(
    account_id: uuid.UUID,
    payload: GoogleAdsCampaignCreate,
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsMutationRead:
    """Create a campaign. It is always **paused** and spends nothing until somebody enables it.

    Google's own default for a new campaign is ENABLED; this route overrides that, because a
    campaign created here has no ad groups, no keywords and no ads yet.

    It needs a `budget_id` that already exists — creating a budget is a separate act behind a
    separate permission, and it is also what keeps this atomic: two mutations cannot be one
    transaction, so a campaign that failed after its budget succeeded would leave an orphan.
    """
    outcome, account = await GoogleAdsWriteService(ctx).create_campaign(
        account_id,
        name=payload.name,
        budget_id=payload.budget_id,
        channel=payload.channel,
        target_content_network=payload.target_content_network,
        validate_only=payload.validate_only,
    )
    return _mutation(outcome, account)


@router.patch(
    "/accounts/{account_id}/campaigns/{campaign_id}",
    response_model=GoogleAdsMutationRead,
    dependencies=[require_permission("google_ads.campaign.write")],
)
async def update_google_ads_campaign(
    account_id: uuid.UUID,
    campaign_id: str,
    payload: GoogleAdsCampaignUpdate,
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsMutationRead:
    """Pause, resume, remove or rename a campaign. `status` is ENABLED, PAUSED or REMOVED.

    REMOVED is permanent at Google: a removed campaign cannot be brought back, only recreated.
    It does not move a campaign to a different budget — that changes what it spends, so it is a
    budget decision and lives behind the budget permission.
    """
    outcome, account = await GoogleAdsWriteService(ctx).update_campaign(
        account_id,
        campaign_id,
        status=payload.status,
        name=payload.name,
        validate_only=payload.validate_only,
    )
    return _mutation(outcome, account)


@router.post(
    "/accounts/{account_id}/ad-groups",
    response_model=GoogleAdsMutationRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission("google_ads.campaign.write")],
)
async def create_google_ads_ad_group(
    account_id: uuid.UUID,
    payload: GoogleAdsAdGroupCreate,
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsMutationRead:
    """Create an ad group inside an existing campaign. Paused, like everything created here."""
    outcome, account = await GoogleAdsWriteService(ctx).create_ad_group(
        account_id,
        name=payload.name,
        campaign_id=payload.campaign_id,
        cpc_bid=payload.cpc_bid,
        validate_only=payload.validate_only,
    )
    return _mutation(outcome, account)


@router.patch(
    "/accounts/{account_id}/ad-groups/{ad_group_id}",
    response_model=GoogleAdsMutationRead,
    dependencies=[require_permission("google_ads.campaign.write")],
)
async def update_google_ads_ad_group(
    account_id: uuid.UUID,
    ad_group_id: str,
    payload: GoogleAdsAdGroupUpdate,
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsMutationRead:
    """Pause, resume, remove, rename or re-bid an ad group.

    `cpc_bid` only does anything where the campaign bids manually; under an automated strategy
    Google ignores it, and this route does not pretend otherwise.
    """
    outcome, account = await GoogleAdsWriteService(ctx).update_ad_group(
        account_id,
        ad_group_id,
        status=payload.status,
        name=payload.name,
        cpc_bid=payload.cpc_bid,
        validate_only=payload.validate_only,
    )
    return _mutation(outcome, account)


@router.post(
    "/accounts/{account_id}/keywords",
    response_model=GoogleAdsMutationRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission("google_ads.keyword.write")],
)
async def add_google_ads_keywords(
    account_id: uuid.UUID,
    payload: GoogleAdsKeywordsAdd,
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsMutationRead:
    """Add positive keywords to one ad group. `match_type` is EXACT, PHRASE or BROAD.

    Sent as one batch with partial failure on, so a keyword Google refuses does not take the
    others down with it: `results` carries one entry per keyword with its own outcome, and
    `skipped` carries the ones the policy refused before Google saw them.
    """
    outcome, account = await GoogleAdsWriteService(ctx).add_keywords(
        account_id,
        ad_group_id=payload.ad_group_id,
        keywords=payload.keywords,
        validate_only=payload.validate_only,
    )
    return _mutation(outcome, account)


@router.patch(
    "/accounts/{account_id}/keywords",
    response_model=GoogleAdsMutationRead,
    dependencies=[require_permission("google_ads.keyword.write")],
)
async def update_google_ads_keyword(
    account_id: uuid.UUID,
    payload: GoogleAdsKeywordUpdate,
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsMutationRead:
    """Pause, resume, remove or re-bid one keyword.

    Its **text and match type cannot be changed** — Google marks them immutable. Correcting a
    keyword means removing it and adding the new one, which is two decisions.
    """
    outcome, account = await GoogleAdsWriteService(ctx).update_keyword(
        account_id,
        ad_group_id=payload.ad_group_id,
        criterion_id=payload.criterion_id,
        status=payload.status,
        cpc_bid=payload.cpc_bid,
        validate_only=payload.validate_only,
    )
    return _mutation(outcome, account)


@router.post(
    "/accounts/{account_id}/keywords/remove",
    response_model=GoogleAdsMutationRead,
    dependencies=[require_permission("google_ads.keyword.write")],
)
async def remove_google_ads_keywords(
    account_id: uuid.UUID,
    payload: GoogleAdsKeywordsRemove,
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsMutationRead:
    """Remove keywords from an ad group, by criterion id.

    A POST rather than a DELETE because it takes a list in the body, and because removing forty
    keywords one request at a time is how a tool call becomes a rate limit.
    """
    outcome, account = await GoogleAdsWriteService(ctx).remove_keywords(
        account_id,
        ad_group_id=payload.ad_group_id,
        criterion_ids=payload.criterion_ids,
        validate_only=payload.validate_only,
    )
    return _mutation(outcome, account)


@router.post(
    "/accounts/{account_id}/negatives",
    response_model=GoogleAdsMutationRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission("google_ads.negative.write")],
)
async def add_google_ads_negatives(
    account_id: uuid.UUID,
    payload: GoogleAdsNegativesAdd,
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsMutationRead:
    """Exclude search terms — and record the ones you deliberately did **not** exclude.

    `terms` are written to Google at `level` (`ad_group`, `campaign` or `shared_set`) under
    `parent_id`. `keep` writes nothing: it records the other half of the same review, so those
    terms are not proposed again next month. Send both from one pass over a search-terms list.

    A term the account's policy protects is **skipped**, not applied, and `skipped` names the
    protected term it would have blocked. Blocking is judged the way Google matches — under the
    proposed exclusion's own match type — so an EXACT negative that cannot reach a protected term
    is allowed.
    """
    outcome, account = await GoogleAdsWriteService(ctx).add_negatives(
        account_id,
        level=payload.level,
        parent_id=payload.parent_id,
        terms=payload.terms,
        keep=payload.keep,
        validate_only=payload.validate_only,
    )
    return _mutation(outcome, account)


@router.post(
    "/accounts/{account_id}/negatives/remove",
    response_model=GoogleAdsMutationRead,
    dependencies=[require_permission("google_ads.negative.write")],
)
async def remove_google_ads_negatives(
    account_id: uuid.UUID,
    payload: GoogleAdsNegativesRemove,
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsMutationRead:
    """Take exclusions back off, by criterion id, at the level they were added."""
    outcome, account = await GoogleAdsWriteService(ctx).remove_negatives(
        account_id,
        level=payload.level,
        parent_id=payload.parent_id,
        criterion_ids=payload.criterion_ids,
        validate_only=payload.validate_only,
    )
    return _mutation(outcome, account)


@router.post(
    "/accounts/{account_id}/negative-lists",
    response_model=GoogleAdsMutationRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission("google_ads.negative.write")],
)
async def create_google_ads_negative_list(
    account_id: uuid.UUID,
    payload: GoogleAdsNegativeListCreate,
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsMutationRead:
    """Create a shared negative-keyword list and optionally attach it to campaigns.

    Two operations that cannot be one transaction. If the attach half fails the list still exists,
    blocks nothing, and re-running attaches it — `warnings` says so. Add terms to it afterwards
    with `POST /negatives` at level `shared_set`.
    """
    outcome, account = await GoogleAdsWriteService(ctx).create_negative_list(
        account_id,
        name=payload.name,
        campaign_ids=payload.campaign_ids,
        validate_only=payload.validate_only,
    )
    return _mutation(outcome, account)


@router.post(
    "/accounts/{account_id}/ads",
    response_model=GoogleAdsMutationRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission("google_ads.campaign.write")],
)
async def create_google_ads_ad(
    account_id: uuid.UUID,
    payload: GoogleAdsAdCreate,
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsMutationRead:
    """Create a responsive search ad. It is **paused** until somebody reads it and enables it.

    3–15 headlines of at most 30 characters, 2–4 descriptions of at most 90, at least one final
    URL. Those limits are checked here, so a refusal names the field rather than an operation
    index. Anything the account's policy lists as a banned phrase is refused outright.
    """
    outcome, account = await GoogleAdsWriteService(ctx).create_ad(
        account_id,
        ad_group_id=payload.ad_group_id,
        headlines=payload.headlines,
        descriptions=payload.descriptions,
        final_urls=payload.final_urls,
        path1=payload.path1,
        path2=payload.path2,
        validate_only=payload.validate_only,
    )
    return _mutation(outcome, account)


@router.patch(
    "/accounts/{account_id}/ads",
    response_model=GoogleAdsMutationRead,
    dependencies=[require_permission("google_ads.campaign.write")],
)
async def update_google_ads_ad(
    account_id: uuid.UUID,
    payload: GoogleAdsAdUpdate,
    ctx: RequestContext = Depends(require_context),
) -> GoogleAdsMutationRead:
    """Pause, resume or remove one ad.

    Status only. An ad's creative is immutable at Google — its performance history belongs to its
    text — so changing a headline means creating a new ad and removing this one.
    """
    outcome, account = await GoogleAdsWriteService(ctx).update_ad(
        account_id,
        ad_group_id=payload.ad_group_id,
        ad_id=payload.ad_id,
        status=payload.status,
        validate_only=payload.validate_only,
    )
    return _mutation(outcome, account)
