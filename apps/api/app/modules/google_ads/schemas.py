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
    row_count: int = 0
    warnings: list[str] = Field(default_factory=list)
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


class GoogleAdsKeywordIdeaRequest(BaseModel):
    """Seeds for keyword research. At least one of ``keywords`` or ``url`` is required."""

    keywords: list[str] = Field(default_factory=list, max_length=20)
    url: str | None = None
    #: Geo target constant ids (``2528`` is the Netherlands) and a language constant id.
    geo_target_ids: list[int] = Field(default_factory=list, max_length=10)
    language_id: int | None = None
    limit: int | None = Field(default=None, ge=1, le=1_000)
