"""Request/response shapes for meta_ads. Business-licensed — see LICENSE.

Every name is prefixed ``MetaAds``: a generic schema name makes FastAPI qualify both modules'
components in the OpenAPI document, which rewrites somebody else's generated client.

**Money is an integer of minor units, and the field name says so.** ``daily_budget_cents: 2500``
is €25,00. It is the unit Meta takes, so nothing is multiplied on the way through, and a field
called ``daily_budget`` holding ``25`` in one payload and ``2500`` in the next is how an extra
zero gets spent.

**Ids named ``meta_id`` are Meta's; ``id`` and ``account_id`` are schakl's.** A payload carrying
two things both called ``campaign_id`` is a payload an agent picks the wrong one out of.

**A write cannot say ``ACTIVE``.** The status fields on the create and update shapes have no
such value: switching something on is its own route with its own permission, and a vocabulary
with no room for the dangerous value is a stronger guarantee than a check that refuses it
(#327's rule about what a model may write, applied to what a key may).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

MetaAdsObjective = Literal[
    "OUTCOME_AWARENESS",
    "OUTCOME_TRAFFIC",
    "OUTCOME_ENGAGEMENT",
    "OUTCOME_LEADS",
    "OUTCOME_SALES",
    "OUTCOME_APP_PROMOTION",
]
MetaAdsCategory = Literal[
    "EMPLOYMENT",
    "HOUSING",
    "CREDIT",
    "FINANCIAL_PRODUCTS_SERVICES",
    "ISSUES_ELECTIONS_POLITICS",
    "ONLINE_GAMBLING_AND_GAMING",
]
#: What a create or an update may set a status to. ``ACTIVE`` is deliberately not here.
MetaAdsQuietStatus = Literal["PAUSED", "ARCHIVED"]
MetaAdsLevel = Literal["account", "campaign", "adset", "ad"]
MetaAdsObjectKind = Literal["campaigns", "adsets", "ads"]


# --------------------------------------------------------------------------- settings, policy
class MetaAdsSettingsRead(BaseModel):
    writes_enabled: bool


class MetaAdsSettingsWrite(BaseModel):
    writes_enabled: bool | None = None


class MetaAdsPolicyValues(BaseModel):
    max_daily_budget_cents: int | None = None
    max_lifetime_budget_cents: int | None = None
    #: How much one change may raise a budget by, in percent: ``100`` is "may at most double".
    max_budget_increase_pct: int | None = None
    banned_phrases: list[str] = Field(default_factory=list)
    dsa_beneficiary: str | None = None
    dsa_payor: str | None = None
    steering: str = ""


class MetaAdsPolicyRead(BaseModel):
    #: ``null`` = the agency's own house policy.
    account_id: uuid.UUID | None
    #: What this layer itself says. ``null`` on a limit means it inherits.
    own: MetaAdsPolicyValues
    #: What actually applies: the built-in, the house policy and this account's, folded.
    effective: MetaAdsPolicyValues
    #: The house policy's prose, shown beside an account's own and never merged into it.
    house_steering: str = ""


class MetaAdsPolicyWrite(BaseModel):
    """§18: a field left out is left alone; ``null`` on a limit makes it inherit again."""

    max_daily_budget_cents: int | None = Field(default=None, ge=1)
    max_lifetime_budget_cents: int | None = Field(default=None, ge=1)
    max_budget_increase_pct: int | None = Field(default=None, ge=0, le=10_000)
    banned_phrases: list[str] | None = Field(default=None, max_length=200)
    dsa_beneficiary: str | None = Field(default=None, max_length=512)
    dsa_payor: str | None = Field(default=None, max_length=512)
    steering: str | None = Field(default=None, max_length=8_000)


# --------------------------------------------------------------------------- accounts
class MetaAdsAccountRead(BaseModel):
    id: uuid.UUID
    meta_id: str
    name: str
    company_id: uuid.UUID | None
    company_name: str | None = None
    currency: str | None
    timezone: str | None
    account_status: int | None
    #: Whether the credential's scopes allow reading, and writing, this account.
    can_read: bool
    can_write: bool
    status: str
    last_error: str | None
    ads_manager_url: str


class MetaAdsAccountLive(BaseModel):
    """What Meta says about the account right now."""

    account_id: uuid.UUID
    meta_id: str
    name: str
    currency: str | None
    account_status: int | None
    disable_reason: int | None
    amount_spent_cents: int | None
    spend_cap_cents: int | None
    balance_cents: int | None
    #: ``development_access`` or ``standard_access`` — the Marketing API tier Meta reports
    #: this app at, which is what decides how many calls an hour this account takes.
    api_tier: str | None
    #: How much of the hourly allowance is used, 0–100, where Meta said.
    usage_percent: int | None


# --------------------------------------------------------------------------- objects
class MetaAdsCampaignRead(BaseModel):
    meta_id: str
    name: str
    objective: str | None
    status: str | None
    effective_status: str | None
    daily_budget_cents: int | None
    lifetime_budget_cents: int | None
    budget_remaining_cents: int | None
    bid_strategy: str | None
    special_ad_categories: list[str]
    start_time: datetime | None
    stop_time: datetime | None
    created_time: datetime | None
    updated_time: datetime | None


class MetaAdsAdSetRead(BaseModel):
    meta_id: str
    campaign_meta_id: str | None
    name: str
    status: str | None
    effective_status: str | None
    daily_budget_cents: int | None
    lifetime_budget_cents: int | None
    budget_remaining_cents: int | None
    billing_event: str | None
    optimization_goal: str | None
    bid_strategy: str | None
    bid_amount_cents: int | None
    targeting: dict[str, Any]
    dsa_beneficiary: str | None
    dsa_payor: str | None
    start_time: datetime | None
    end_time: datetime | None


class MetaAdsAdRead(BaseModel):
    meta_id: str
    adset_meta_id: str | None
    campaign_meta_id: str | None
    name: str
    status: str | None
    #: ``PENDING_REVIEW``, ``DISAPPROVED``, ``WITH_ISSUES`` … — what Meta's review made of it.
    effective_status: str | None
    creative_meta_id: str | None
    #: Meta's own account of what is wrong, as it sent it.
    issues: list[dict[str, Any]]
    created_time: datetime | None


class MetaAdsCreativeRead(BaseModel):
    meta_id: str
    name: str
    status: str | None
    #: ``{page id}_{post id}`` — the post this creative shows.
    object_story_id: str | None
    thumbnail_url: str | None


class MetaAdsInsightRow(BaseModel):
    meta_id: str | None
    name: str | None
    date_from: date | None
    date_to: date | None
    spend: float
    impressions: int
    reach: int | None
    clicks: int
    #: Ratios as Meta computed them — never recomputed from a column of other ratios.
    ctr: float | None
    cpc: float | None
    cpm: float | None
    #: ``{action type: count}`` — ``link_click``, ``lead``, ``purchase`` …
    actions: dict[str, float]


class MetaAdsInsightsRead(BaseModel):
    account_id: uuid.UUID
    level: MetaAdsLevel
    date_from: date
    date_to: date
    currency: str | None
    rows: list[MetaAdsInsightRow]
    #: Meta's own figures for the whole span. ``null`` where Meta answered with no row.
    totals: MetaAdsInsightRow | None
    #: i18n keys under ``meta_ads.warning.*``.
    warnings: list[str]


# --------------------------------------------------------------------------- writes
class _Write(BaseModel):
    #: Ask Meta whether it *would* accept this, and change nothing. Records nothing.
    validate_only: bool = False
    #: Why. Stored with the decision; the one field a later reader cannot reconstruct.
    reason: str = Field(default="", max_length=2_000)


class MetaAdsCampaignCreate(_Write):
    """A new campaign. **Always created paused.**"""

    name: str = Field(min_length=1, max_length=400)
    objective: MetaAdsObjective
    #: Empty means none apply. Meta requires the field either way, so it is always sent.
    special_ad_categories: list[MetaAdsCategory] = Field(default_factory=list)
    #: The countries a special ad category applies in — ISO codes.
    special_ad_category_countries: list[str] = Field(default_factory=list)
    #: A budget on the campaign is shared by its ad sets. Leave both out to budget per ad set.
    daily_budget_cents: int | None = Field(default=None, ge=1)
    lifetime_budget_cents: int | None = Field(default=None, ge=1)
    bid_strategy: str | None = Field(default=None, max_length=64)
    start_time: datetime | None = None
    stop_time: datetime | None = None


class MetaAdsCampaignUpdate(_Write):
    name: str | None = Field(default=None, min_length=1, max_length=400)
    status: MetaAdsQuietStatus | None = None
    start_time: datetime | None = None
    stop_time: datetime | None = None


class MetaAdsTargeting(BaseModel):
    """Who sees the ad. The common fields are modelled; ``extra`` takes the rest as Meta
    spells it (interests, custom audiences, placements), and Meta's own validator judges it."""

    countries: list[str] = Field(min_length=1, max_length=50)
    age_min: int = Field(default=18, ge=13, le=65)
    age_max: int = Field(default=65, ge=13, le=65)
    #: ``1`` men, ``2`` women. Empty means everyone.
    genders: list[int] = Field(default_factory=list)
    #: Let Meta widen the audience beyond what is stated here. Off unless asked for: the
    #: audience somebody typed is the audience that is targeted.
    advantage_audience: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)


class MetaAdsAdSetCreate(_Write):
    """A new ad set. **Always created paused.**"""

    campaign_meta_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=400)
    optimization_goal: str = Field(min_length=1, max_length=64)
    billing_event: str = Field(default="IMPRESSIONS", max_length=64)
    daily_budget_cents: int | None = Field(default=None, ge=1)
    lifetime_budget_cents: int | None = Field(default=None, ge=1)
    bid_strategy: str | None = Field(default=None, max_length=64)
    bid_amount_cents: int | None = Field(default=None, ge=1)
    targeting: MetaAdsTargeting
    destination_type: str | None = Field(default=None, max_length=64)
    #: What is being promoted, as Meta takes it (``{"page_id": …}``, ``{"pixel_id": …}``).
    promoted_object: dict[str, Any] | None = None
    start_time: datetime | None = None
    #: Required with a lifetime budget.
    end_time: datetime | None = None
    #: Who the ad is for and who pays, for an ad set targeting the EU. Left out, they come
    #: from the account's policy, its defaults at Meta, or the client's legal name.
    dsa_beneficiary: str | None = Field(default=None, max_length=512)
    dsa_payor: str | None = Field(default=None, max_length=512)


class MetaAdsAdSetUpdate(_Write):
    name: str | None = Field(default=None, min_length=1, max_length=400)
    status: MetaAdsQuietStatus | None = None
    targeting: MetaAdsTargeting | None = None
    bid_amount_cents: int | None = Field(default=None, ge=1)
    start_time: datetime | None = None
    end_time: datetime | None = None
    dsa_beneficiary: str | None = Field(default=None, max_length=512)
    dsa_payor: str | None = Field(default=None, max_length=512)


class MetaAdsBudgetWrite(_Write):
    """One of the two. The change is judged against the account's policy before it is sent."""

    daily_budget_cents: int | None = Field(default=None, ge=1)
    lifetime_budget_cents: int | None = Field(default=None, ge=1)


class MetaAdsCreativeCreate(BaseModel):
    """What the ad shows: a link with words and a picture, or a post that already exists."""

    name: str = Field(min_length=1, max_length=400)
    #: The Page the ad runs as — one of ``list_meta_assets``' ids, and the same client's.
    page_id: uuid.UUID
    #: The Instagram account it runs as there, if it should.
    instagram_id: uuid.UUID | None = None
    #: Promote a post that is already live: ``{page id}_{post id}``, a planned post's
    #: ``meta_post_id``. When set, the fields below are not used.
    meta_post_id: str | None = Field(default=None, max_length=128)
    link: str | None = Field(default=None, max_length=2_000)
    message: str | None = Field(default=None, max_length=5_000)
    headline: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=1_000)
    #: An image already uploaded to this ad account (``upload_meta_ads_image``).
    image_hash: str | None = Field(default=None, max_length=64)
    image_url: str | None = Field(default=None, max_length=2_000)
    #: ``LEARN_MORE``, ``SHOP_NOW``, ``CONTACT_US`` … as Meta spells it.
    call_to_action: str | None = Field(default=None, max_length=64)
    reason: str = Field(default="", max_length=2_000)


class MetaAdsAdCreate(_Write):
    """A new ad. **Always created paused.**"""

    adset_meta_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=400)
    creative_meta_id: str = Field(min_length=1, max_length=64)


class MetaAdsAdUpdate(_Write):
    name: str | None = Field(default=None, min_length=1, max_length=400)
    status: MetaAdsQuietStatus | None = None
    creative_meta_id: str | None = Field(default=None, max_length=64)


class MetaAdsStatusWrite(_Write):
    pass


class MetaAdsImageUpload(BaseModel):
    """An image for an ad, as JSON — the multipart route's twin, so a tool can reach it."""

    filename: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1, max_length=14_000_000)


class MetaAdsImageRead(BaseModel):
    hash: str
    url: str | None


class MetaAdsBoost(_Write):
    """Put money behind a post that is already live: a campaign, an ad set, a creative and an
    ad, made together and **all paused**."""

    #: The post at Meta: ``{page id}_{post id}``.
    meta_post_id: str = Field(min_length=3, max_length=128)
    page_id: uuid.UUID
    name: str | None = Field(default=None, max_length=300)
    objective: MetaAdsObjective = "OUTCOME_ENGAGEMENT"
    optimization_goal: str = Field(default="POST_ENGAGEMENT", max_length=64)
    daily_budget_cents: int = Field(ge=1)
    start_time: datetime | None = None
    end_time: datetime | None = None
    targeting: MetaAdsTargeting
    dsa_beneficiary: str | None = Field(default=None, max_length=512)
    dsa_payor: str | None = Field(default=None, max_length=512)


class MetaAdsResult(BaseModel):
    """What a write came to."""

    #: Meta's id of the object. ``null`` on a ``validate_only`` create: nothing was made.
    meta_id: str | None
    kind: str
    #: Whether Meta was changed.
    applied: bool
    validate_only: bool
    status: str | None = None
    #: The decision this write recorded. ``null`` when it recorded none.
    decision_id: uuid.UUID | None = None


class MetaAdsBoostResult(BaseModel):
    applied: bool
    validate_only: bool
    campaign_meta_id: str | None
    adset_meta_id: str | None
    creative_meta_id: str | None
    ad_meta_id: str | None
    #: Set when a later step was refused after an earlier one had been made: what exists at
    #: Meta now, and the step that failed. Everything made is paused.
    failed_step: str | None = None


# --------------------------------------------------------------------------- decisions
class MetaAdsDecisionRead(BaseModel):
    id: uuid.UUID
    account_id: uuid.UUID
    subject_type: str
    subject_meta_id: str
    subject_name: str
    decision: str
    reason: str
    applied: bool
    payload: dict[str, Any]
    decided_by_name: str
    impersonator_name: str | None
    created_at: datetime


class MetaAdsDecisionPage(BaseModel):
    items: list[MetaAdsDecisionRead]
    total: int
    limit: int
    offset: int


class MetaAdsDecisionCreate(BaseModel):
    """Record a judgement that changed nothing: "looked at this, left it as it is"."""

    subject_type: Literal["campaign", "ad_set", "ad"]
    subject_meta_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=2_000)
