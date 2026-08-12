"""Wire shapes for the google_ads module. Business-licensed — see LICENSE.

Named with a ``GoogleAds`` prefix throughout. A generic Pydantic model name makes FastAPI
qualify **both** modules' components in the OpenAPI document, which silently renames another
module's schema in the generated TypeScript client — a diff nobody reviewing this module would
think to look at.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class GoogleAdsSettingsRead(BaseModel):
    """The org's Ads configuration. **Never carries the developer token itself** — only whether
    one is configured, and where the effective one comes from."""

    developer_token_configured: bool
    #: ``true`` when the deprecated ``SCHAKL_GOOGLE_ADS_DEVELOPER_TOKEN`` env var is what would
    #: answer. Shown so an admin staring at an empty field understands why Ads works anyway.
    env_token_configured: bool
    default_login_customer_id: str | None = None
    writes_enabled: bool = True


class GoogleAdsSettingsWrite(BaseModel):
    """An **empty string keeps the stored secret**; an explicit ``null`` clears it.

    The write-only-secret contract every credential screen here uses. A form that posts the
    field blank because the user did not retype it must not wipe a working credential.
    """

    developer_token: str | None = None
    default_login_customer_id: str | None = None
    writes_enabled: bool | None = None


class GoogleAdsAccountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_id: str
    #: ``123-456-7890`` — the form Google's own UI shows. Computed, so no screen re-implements it.
    customer_id_formatted: str
    login_customer_id: str | None = None
    company_id: uuid.UUID | None = None
    company_name: str | None = None
    connection_id: uuid.UUID | None = None
    descriptive_name: str
    currency_code: str | None = None
    time_zone: str | None = None
    is_manager: bool = False
    test_account: bool = False
    conversion_tracking_status: str | None = None
    optimization_score: float | None = None
    active: bool = True
    status: str
    #: Google's own sentence about the last failure, scrubbed of credentials. Not an i18n key —
    #: it is provider text, which is why it lives here and never in the error envelope (§9).
    last_error: str | None = None
    last_verified_at: datetime | None = None
    last_synced_at: datetime | None = None


class GoogleAdsAccountCreate(BaseModel):
    """Link an account the picker offered. ``customer_id`` is normalised on write, so the
    hyphenated form a human pastes and the bare form the picker sends are the same row."""

    customer_id: str = Field(min_length=1, max_length=32)
    company_id: uuid.UUID | None = None
    login_customer_id: str | None = Field(default=None, max_length=32)
    descriptive_name: str = Field(default="", max_length=255)
    currency_code: str | None = Field(default=None, max_length=3)


class GoogleAdsAccountUpdate(BaseModel):
    """Only what schakl *decided* is editable. The name, currency, timezone and manager flag are
    what Google last said, refreshed by verify — typing over them would make the row disagree
    with the account it describes and nothing would ever put it back."""

    company_id: uuid.UUID | None = None
    login_customer_id: str | None = Field(default=None, max_length=32)
    active: bool | None = None


class GoogleAdsAvailableAccount(BaseModel):
    customer_id: str
    customer_id_formatted: str
    descriptive_name: str
    login_customer_id: str | None = None
    currency_code: str | None = None
    hint: str
    #: The picker hides nothing — it *marks*. An account already linked to another client is
    #: exactly what someone needs to see when they are wondering why it is missing.
    already_linked: bool = False


class GoogleAdsPickerRead(BaseModel):
    accounts: list[GoogleAdsAvailableAccount] = Field(default_factory=list)
    #: **Read before drawing conclusions.** A manager whose child list was capped reports it
    #: here; a picker that lists 500 of 900 accounts and says nothing looks like 900 does not
    #: exist (CLAUDE.md §17).
    warnings: list[str] = Field(default_factory=list)


# --- the read surface ------------------------------------------------------------------------ #


class GoogleAdsPeriod(BaseModel):
    """The span a read covers. Both ends inclusive, resolved in the **account's** timezone."""

    date_from: date
    date_to: date
    days: int
    #: The token this came from (``30d``, ``last_month``, ``2026-07``), when one was used. Echoed
    #: so a named month and a trailing window that happen to cover the same days stay tellable
    #: apart — the rule #316 established for every other period in the product.
    token: str | None = None


class GoogleAdsMetrics(BaseModel):
    """The metric block every performance row carries.

    Two rules, and a client that breaks either produces reports that lie:

    * **``ctr`` and ``conversion_rate`` are fractions.** ``0.0453`` is 4,53 %. Multiply once,
      where it is displayed.
    * **A non-computable ratio is ``null``, never ``0``.** Zero is a measurement; ``null`` is
      the absence of one. Cost-per-conversion with no conversions is the second, and a layer
      that normalises it to zero makes every report downstream of it wrong in the same
      direction.
    """

    impressions: int = 0
    clicks: int = 0
    #: In the **account's** currency, which is on the envelope — never assumed to be EUR.
    cost: float = 0.0
    conversions: float = 0.0
    conversions_value: float = 0.0
    #: Every conversion action, including the ones excluded from bidding. The gap between this
    #: and ``conversions`` is what says whether the account optimises toward what it measures.
    all_conversions: float = 0.0
    ctr: float | None = None
    average_cpc: float | None = None
    conversion_rate: float | None = None
    cost_per_conversion: float | None = None
    value_per_conversion: float | None = None


class GoogleAdsImpressionShare(BaseModel):
    """Fractions again, and ``null`` where the campaign type does not report them at all.

    Only Search-like campaigns do. On a Display or Video campaign these arrive absent, which is
    **not** the same claim as 0 % visibility.
    """

    search_impression_share: float | None = None
    search_lost_is_budget: float | None = None
    search_lost_is_rank: float | None = None


class GoogleAdsAccountBrief(BaseModel):
    """Which account answered — on every read, so a response is never ambiguous about that."""

    id: uuid.UUID
    customer_id: str
    customer_id_formatted: str
    descriptive_name: str
    company_id: uuid.UUID | None = None


class GoogleAdsReport(BaseModel):
    """The shared envelope every read returns.

    ``warnings`` is not decoration and not cosmetic: truncation, a shortened change-history
    window, a geo read that fell back to country level and the provisional nature of recent
    figures are reported **here and nowhere else**. A caller that ignores it will eventually
    present a capped list as a complete one.
    """

    account: GoogleAdsAccountBrief
    period: GoogleAdsPeriod | None = None
    currency: str | None = None
    #: IANA name. Google aggregates a campaign's day in this zone, so a date range means nothing
    #: without it — and ``fetched_at`` below is UTC, so a response genuinely carries two clocks.
    account_timezone: str | None = None
    fetched_at: datetime
    #: How many rows came back — the size of ``rows``, which is one page of the answer.
    row_count: int = 0
    #: How many rows the filter matched in total. Two counts, not one, because they answer two
    #: questions: the first says how much came back and this says how much there was. A pager
    #: driven off ``row_count`` reads "1 to 50 of 50" on every page of a list of nine hundred —
    #: the truncated-total failure (#37), one layer up. They are equal when nobody paged.
    total_rows: int = 0
    #: Where this page starts in that set, echoed so a caller can tell page 1 from page 4
    #: without still holding the request that asked for it.
    offset: int = 0
    warnings: list[str] = Field(default_factory=list)
    #: Over the **matched** set, not over the page: a footer under fifty rows that describes
    #: nine hundred is the same lie as a total that counts the page.
    totals: GoogleAdsMetrics | None = None
    rows: list[dict[str, Any]] = Field(default_factory=list)
    #: Per-read extras: ``granularity`` on geo, ``device_totals`` on devices,
    #: ``effective_period`` on changes. Kept as one named object rather than sprinkled onto the
    #: envelope, so the shared shape stays the same across every tool.
    extra: dict[str, Any] = Field(default_factory=dict)


class GoogleAdsSnapshotRead(GoogleAdsReport):
    """Account totals plus its campaigns — the read to start an analysis from."""

    account_summary: dict[str, Any] = Field(default_factory=dict)
    campaign_count: int = 0
    enabled_campaign_count: int = 0
    total_daily_budget: float | None = None


class GoogleAdsQueryRequest(BaseModel):
    """A GAQL query against one linked account.

    The customer id is **not** a field here and never will be: it comes from the account row in
    the path, so no query can reach an advertiser this workspace has not linked.
    """

    query: str = Field(min_length=1, max_length=8_000)
    limit: int | None = Field(default=None, ge=1)


class GoogleAdsQueryRead(GoogleAdsReport):
    """Rows exactly as Google returned them, plus what the guard did to the query."""

    #: The query as it was actually sent — with the imposed or clamped ``LIMIT``. Returned so a
    #: caller can see what was run rather than what they typed.
    executed_query: str = ""
    resource: str = ""


class GoogleAdsTrendPoint(BaseModel):
    date: date
    metrics: GoogleAdsMetrics


class GoogleAdsChangeAmount(BaseModel):
    """What moved, in both the absolute and the relative sense.

    ``relative`` is ``null`` when the baseline was zero. Not infinity and not 100 %: a
    percentage against nothing is undefined, and anything that renders `inf` eventually prints
    it in a sentence in front of a client.
    """

    from_: float | None = Field(default=None, alias="from")
    to: float | None = None
    absolute: float | None = None
    relative: float | None = None

    model_config = ConfigDict(populate_by_name=True)


class GoogleAdsTrendRead(BaseModel):
    """A window against its comparison, **entirely from stored rows** — no Google call.

    The compared window's dates are part of the payload rather than a label. "Up 21 %" over an
    unnamed span is a sentence that could be printed over any two dates at all, which is why a
    comparison set to the wrong thing looks exactly like one set to the right thing (#312).
    """

    account: GoogleAdsAccountBrief
    period: GoogleAdsPeriod
    compared_with: GoogleAdsPeriod
    compare_mode: str
    currency: str | None = None
    totals: GoogleAdsMetrics
    previous_totals: GoogleAdsMetrics
    change: dict[str, GoogleAdsChangeAmount | None] = Field(default_factory=dict)
    series: list[GoogleAdsTrendPoint] = Field(default_factory=list)
    breakdown: list[dict[str, Any]] = Field(default_factory=list)
    #: Days in the window with no stored row — usually "not synced yet", never "no spend".
    #: Reported rather than smoothed over, because a chart with a silent gap makes the second
    #: claim while meaning the first.
    missing_days: int = 0
    warnings: list[str] = Field(default_factory=list)


class GoogleAdsPolicyRead(BaseModel):
    """One policy row, plus what it actually resolves to.

    Both, because a form full of blanks meaning "something else decides" is unreadable: the
    editor binds to ``stored`` and the screen shows ``resolved``, so an inherit option can be
    labelled with the value it inherits rather than with the word "inherit" (#312's rule about a
    comparison that names its own span, applied to a setting).
    """

    account_id: uuid.UUID | None = None
    #: ``true`` when no row exists yet — every value shown is inherited and nothing was saved.
    stored: bool = False
    protected_terms: list[str] = Field(default_factory=list)
    banned_phrases: list[str] = Field(default_factory=list)
    always_exclude: list[str] = Field(default_factory=list)
    max_daily_budget: float | None = None
    max_budget_increase_pct: float | None = None
    max_cpc: float | None = None
    waste_min_cost: float | None = None
    waste_min_clicks: int | None = None
    steering: str = ""
    ad_copy_rules: str = ""
    #: The three layers already folded — lists unioned with the house policy's, scalars filled in
    #: from it or from the built-in. Never written back; it is what the rules currently *are*.
    resolved: dict[str, Any] = Field(default_factory=dict)


class GoogleAdsPolicyWrite(BaseModel):
    """Absent means leave alone; explicit ``null`` on a scalar means inherit again.

    Both are real states and the value alone cannot tell them apart, so the router reads
    ``model_fields_set`` (CLAUDE.md §18). Without it a ceiling set once could never be taken off,
    and an account would stay pinned to a number somebody typed in a hurry.
    """

    protected_terms: list[str] | None = None
    banned_phrases: list[str] | None = None
    always_exclude: list[str] | None = None
    max_daily_budget: float | None = Field(default=None, ge=0)
    max_budget_increase_pct: float | None = Field(default=None, ge=0)
    max_cpc: float | None = Field(default=None, ge=0)
    waste_min_cost: float | None = Field(default=None, ge=0)
    waste_min_clicks: int | None = Field(default=None, ge=0)
    steering: str | None = Field(default=None, max_length=8_000)
    ad_copy_rules: str | None = Field(default=None, max_length=8_000)


class GoogleAdsDecisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_id: uuid.UUID
    subject_type: str
    subject: str
    scope: str
    decision: str
    reason: str = ""
    applied: bool = False
    source: str = "manual"
    payload: dict[str, Any] = Field(default_factory=dict)
    #: Snapshotted at record time (§16) — an audit trail whose actor evaporates is not one.
    decided_by_name: str = ""
    #: Set when the decision was taken through an impersonated session (#296).
    impersonator_name: str | None = None
    expires_on: date | None = None
    withdrawn_at: datetime | None = None
    withdrawn_by_name: str | None = None
    created_at: datetime


class GoogleAdsDecisionPage(BaseModel):
    """A page of the log. ``total`` is ``null`` when the caller asked not to count."""

    items: list[GoogleAdsDecisionRead] = Field(default_factory=list)
    total: int | None = None


class GoogleAdsDecisionCreate(BaseModel):
    subject_type: str = Field(default="search_term", max_length=24)
    subject: str = Field(min_length=1, max_length=255)
    decision: str = Field(min_length=1, max_length=24)
    scope: str = Field(default="account", max_length=64)
    reason: str = Field(default="", max_length=4_000)
    #: A decision with no end date is silent forever, which is the wrong default for a judgement
    #: about a market: "not worth excluding at today's CPC" stops being true.
    expires_on: date | None = None


# --- the write surface -------------------------------------------------------------------------- #


class GoogleAdsMutationResult(BaseModel):
    """One operation's outcome. ``resource_name`` is what Google named the thing it created."""

    index: int
    ok: bool
    resource_name: str | None = None
    #: Google's own error enum (``criterionError.INVALID_KEYWORD_TEXT``), which is the only
    #: reliable way to tell two refusals sharing a status code apart.
    error_code: str | None = None
    #: Google's own sentence, scrubbed of credentials. Provider text, so never an i18n key (§9).
    message: str | None = None


class GoogleAdsSkipped(BaseModel):
    """One operation the **policy** refused before Google saw it.

    Kept apart from :class:`GoogleAdsMutationResult` on purpose: "we did not ask" and "Google said
    no" are different sentences, and only one of them is fixable in Google's interface.
    """

    subject: str
    #: An i18n key — this refusal is ours, so unlike a Google message it is translated.
    reason: str
    #: The protected term this exclusion would have blocked, when that is why it was skipped.
    #: Named rather than implied: "refused" invites an argument, "would also block *beugel*"
    #: invites a fix.
    blocks: str | None = None
    limit: float | None = None


class GoogleAdsMutationRead(BaseModel):
    """What a write did, per operation.

    ``requested`` and ``applied`` differ whenever the policy skipped a row, Google refused one
    inside a partial-failure batch, or ``validate_only`` was set — where ``applied`` is zero
    because nothing was.
    """

    account: GoogleAdsAccountBrief
    resource: str
    validate_only: bool
    requested: int = 0
    applied: int = 0
    results: list[GoogleAdsMutationResult] = Field(default_factory=list)
    skipped: list[GoogleAdsSkipped] = Field(default_factory=list)
    #: Read before drawing conclusions: a validate-only run, a shared budget that moved several
    #: campaigns, a list that could not be attached to every campaign, and a refusal Google gave
    #: no operation index for are all reported here and nowhere else.
    warnings: list[str] = Field(default_factory=list)
    fetched_at: datetime


class _Validatable(BaseModel):
    """Every write carries it, and it is the real dry run.

    Google validates the operation against the *actual* account structure and applies nothing —
    better than a test account, which serves no ads and therefore cannot answer any question about
    real campaigns.
    """

    validate_only: bool = Field(
        default=False,
        description="Validate against the live account and change nothing. Use this first.",
    )


class GoogleAdsBudgetCreate(_Validatable):
    name: str = Field(min_length=1, max_length=255)
    #: In the **account's** currency, which is on every read's envelope. Never assumed to be EUR.
    amount: float = Field(ge=0)
    #: A shared budget's next edit moves every campaign attached to it, so this defaults off.
    shared: bool = False


class GoogleAdsBudgetUpdate(_Validatable):
    amount: float | None = Field(default=None, ge=0)
    name: str | None = Field(default=None, max_length=255)


class GoogleAdsCampaignCreate(_Validatable):
    name: str = Field(min_length=1, max_length=255)
    #: An existing budget. This route cannot create one: that is a ``budget.write`` decision, and
    #: a campaign route that could conjure a budget would make ``campaign.write`` a budget key.
    budget_id: str = Field(min_length=1, max_length=32)
    channel: str = Field(default="SEARCH", max_length=32)
    #: Off by default: a Search campaign quietly opted into Display spends its budget where
    #: nobody is looking.
    target_content_network: bool = False


class GoogleAdsCampaignUpdate(_Validatable):
    status: str | None = Field(default=None, max_length=16)
    name: str | None = Field(default=None, max_length=255)


class GoogleAdsAdGroupCreate(_Validatable):
    name: str = Field(min_length=1, max_length=255)
    campaign_id: str = Field(min_length=1, max_length=32)
    cpc_bid: float | None = Field(default=None, ge=0)


class GoogleAdsAdGroupUpdate(_Validatable):
    status: str | None = Field(default=None, max_length=16)
    name: str | None = Field(default=None, max_length=255)
    cpc_bid: float | None = Field(default=None, ge=0)


class GoogleAdsKeywordInput(BaseModel):
    text: str = Field(min_length=1, max_length=80)
    match_type: str = Field(default="PHRASE", max_length=16)
    cpc_bid: float | None = Field(default=None, ge=0)


class GoogleAdsKeywordsAdd(_Validatable):
    ad_group_id: str = Field(min_length=1, max_length=32)
    keywords: list[GoogleAdsKeywordInput] = Field(min_length=1, max_length=200)


class GoogleAdsKeywordUpdate(_Validatable):
    ad_group_id: str = Field(min_length=1, max_length=32)
    criterion_id: str = Field(min_length=1, max_length=32)
    status: str | None = Field(default=None, max_length=16)
    cpc_bid: float | None = Field(default=None, ge=0)


class GoogleAdsKeywordsRemove(_Validatable):
    ad_group_id: str = Field(min_length=1, max_length=32)
    criterion_ids: list[str] = Field(min_length=1, max_length=200)


class GoogleAdsNegativeInput(BaseModel):
    text: str = Field(min_length=1, max_length=80)
    match_type: str = Field(default="PHRASE", max_length=16)
    #: Why. It reaches the decisions log, which is the whole reason this list stops growing back.
    reason: str = Field(default="", max_length=1_000)


class GoogleAdsKeepInput(BaseModel):
    text: str = Field(min_length=1, max_length=255)
    reason: str = Field(default="", max_length=1_000)
    expires_on: date | None = None


class GoogleAdsNegativesAdd(_Validatable):
    #: ``ad_group``, ``campaign`` or ``shared_set`` — Google models an exclusion as three
    #: different resources and a write has to pick one.
    level: str = Field(default="campaign", max_length=16)
    parent_id: str = Field(min_length=1, max_length=32)
    terms: list[GoogleAdsNegativeInput] = Field(default_factory=list, max_length=200)
    #: Terms deliberately **not** excluded. Nothing is written to Google; the decision is
    #: recorded, so the same terms are not proposed again next month.
    keep: list[GoogleAdsKeepInput] = Field(default_factory=list, max_length=200)


class GoogleAdsNegativesRemove(_Validatable):
    level: str = Field(default="campaign", max_length=16)
    parent_id: str = Field(min_length=1, max_length=32)
    criterion_ids: list[str] = Field(min_length=1, max_length=200)


class GoogleAdsNegativeListCreate(_Validatable):
    name: str = Field(min_length=1, max_length=255)
    #: Campaigns to attach it to. Empty is legal: a list that blocks nothing yet is a normal
    #: intermediate state, and attaching is a second, re-runnable act.
    campaign_ids: list[str] = Field(default_factory=list, max_length=100)


class GoogleAdsAdCreate(_Validatable):
    ad_group_id: str = Field(min_length=1, max_length=32)
    #: 3–15 headlines of at most 30 characters; 2–4 descriptions of at most 90. Google's limits,
    #: checked here so a refusal names the field rather than an operation index.
    headlines: list[str] = Field(min_length=1, max_length=15)
    descriptions: list[str] = Field(min_length=1, max_length=4)
    final_urls: list[str] = Field(min_length=1, max_length=10)
    path1: str | None = Field(default=None, max_length=15)
    path2: str | None = Field(default=None, max_length=15)


class GoogleAdsAdUpdate(_Validatable):
    ad_group_id: str = Field(min_length=1, max_length=32)
    ad_id: str = Field(min_length=1, max_length=32)
    #: Status only: an ad's creative is immutable at Google, because its performance history
    #: belongs to its text. Changing a headline is a new ad plus a removal.
    status: str = Field(min_length=1, max_length=16)


class GoogleAdsKeywordIdeaRequest(BaseModel):
    """Seeds for keyword research. At least one of ``keywords`` or ``url`` is required."""

    keywords: list[str] = Field(default_factory=list, max_length=20)
    url: str | None = None
    #: Geo target constant ids (``2528`` is the Netherlands) and a language constant id.
    geo_target_ids: list[int] = Field(default_factory=list, max_length=10)
    language_id: int | None = None
    limit: int | None = Field(default=None, ge=1, le=1_000)
