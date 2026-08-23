"""Request/response shapes for the Google Analytics surface. Business-licensed — see LICENSE.

Two decisions worth stating, because both look like laziness and neither is.

**Admin resources travel as their own JSON.** A data stream, a key event, a custom dimension and
a Google Ads link are Google's records, not ours: we store none of them, we cannot correct them,
and re-modelling each one here would mean a field Google adds next quarter is a field this
module drops. So :class:`GoogleAnalyticsResourceList` carries the resources verbatim under a
``kind`` that says which listing they are, and the one place a shape *is* imposed —
:class:`GoogleAnalyticsProperty` — is imposed because a property is the thing every other call
takes an id from, and an agent should not have to know that ``property`` is a resource name
while ``displayName`` is a label.

**A report answers with the columns it was actually given.** ``dimensions`` and ``metrics`` are
echoed from Google's response headers rather than from the request, so a caller can tell a
metric that came back from one that was quietly not returned — the difference between a zero and
an absence, which every number on the row would otherwise hide.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field


class GoogleAnalyticsProperty(BaseModel):
    """One GA4 property the connected Google account can read."""

    property_id: str
    display_name: str
    #: The account the property sits under — an agency reading a list of forty needs it.
    account_id: str = ""
    account_name: str = ""
    property_type: str = ""
    currency_code: str = ""
    #: The property's **own** reporting timezone, which is not the org's and not the viewer's.
    #: Every date in a GA4 report is a day in *this* zone, so a comparison against anything the
    #: platform computed is only sound once somebody has looked at it.
    time_zone: str = ""
    industry_category: str = ""
    parent: str = ""


class GoogleAnalyticsPropertyList(BaseModel):
    """The properties this caller's Google connection reaches — or why it reaches none.

    ``connected``/``has_scope`` are reported rather than raised for the picker's reason (#411):
    a missing credential decides a *sentence*, never whether the control exists. A caller that
    has connected Google but never granted Analytics is a different state from one that has
    never connected at all, and only the first is fixed by re-consenting.
    """

    connected: bool = False
    has_scope: bool = False
    properties: list[GoogleAnalyticsProperty] = Field(default_factory=list)
    #: The listing stopped at its page ceiling: what is here is real, and it is not all of it.
    truncated: bool = False
    error: str | None = None
    #: The flag the Google connect flow needs to add ``analytics.readonly`` to this grant.
    connect_flag: str = "include_analytics"


class GoogleAnalyticsResourceList(BaseModel):
    """An Admin API listing, verbatim. ``kind`` names which one."""

    property_id: str
    kind: str
    rows: list[dict[str, Any]] = Field(default_factory=list)
    truncated: bool = False


class GoogleAnalyticsField(BaseModel):
    """One dimension or metric this property will accept, from its own metadata document."""

    api_name: str
    ui_name: str = ""
    description: str = ""
    category: str = ""
    #: Metrics only: COUNT, CURRENCY, PERCENT, SECONDS… — what the number *is*, which is the
    #: difference between "0,45" and "45 %".
    type: str = ""
    custom: bool = False


class GoogleAnalyticsMetadata(BaseModel):
    """Everything this property can be asked for. Custom dimensions and metrics are in here
    too, under their own api_names, which is the only place they are discoverable."""

    property_id: str
    dimensions: list[GoogleAnalyticsField] = Field(default_factory=list)
    metrics: list[GoogleAnalyticsField] = Field(default_factory=list)


class GoogleAnalyticsPeriod(BaseModel):
    date_from: date
    date_to: date
    days: int


class GoogleAnalyticsCompare(BaseModel):
    date_from: date
    date_to: date
    mode: str


class GoogleAnalyticsRow(BaseModel):
    dimensions: dict[str, str] = Field(default_factory=dict)
    metrics: dict[str, float] = Field(default_factory=dict)


class GoogleAnalyticsReport(BaseModel):
    """A Data API report, flattened."""

    property_id: str
    period: GoogleAnalyticsPeriod | None = None
    dimensions: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    rows: list[GoogleAnalyticsRow] = Field(default_factory=list)
    #: Google's own totals row — never a sum of the column, because the ratio metrics are
    #: weighted and adding them answers a number that is none of them.
    totals: dict[str, float] = Field(default_factory=dict)
    row_count: int = 0
    #: The answer is a page of a longer list: the caller asked for fewer rows than exist.
    truncated: bool = False
    #: GA4 sampled or thresholded this answer (a small audience, a long window). Reported
    #: because a sampled number is an estimate and a client will read it as a count.
    warnings: list[str] = Field(default_factory=list)


class GoogleAnalyticsChange(BaseModel):
    value_from: float
    value_to: float
    absolute: float
    #: ``None`` when the baseline is zero: a percentage against nothing is undefined, and a
    #: model handed a number writes a sentence about it.
    relative: float | None = None


class GoogleAnalyticsOverview(BaseModel):
    """The question an agency asks: how did this property do, against what, and by how much."""

    property_id: str
    period: GoogleAnalyticsPeriod
    compared_with: GoogleAnalyticsCompare
    currency_code: str = ""
    time_zone: str = ""
    totals: dict[str, float] = Field(default_factory=dict)
    previous_totals: dict[str, float] = Field(default_factory=dict)
    change: dict[str, GoogleAnalyticsChange | None] = Field(default_factory=dict)
    #: Sessions by default channel group — where the traffic came from, in one line.
    channels: dict[str, float] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class GoogleAnalyticsRealtime(BaseModel):
    """Who is on the site right now. No date range exists here — that is the whole point."""

    property_id: str
    active_users: float = 0.0
    dimensions: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    rows: list[GoogleAnalyticsRow] = Field(default_factory=list)


class GoogleAnalyticsCompatibilityItem(BaseModel):
    api_name: str
    compatibility: str


class GoogleAnalyticsCompatibility(BaseModel):
    """Which dimensions and metrics may be combined with the ones already named.

    The check exists because GA4 refuses some pairs outright, and its refusal names neither
    half — so the way to build a report that works is to ask this first, not to retry.
    """

    property_id: str
    dimensions: list[GoogleAnalyticsCompatibilityItem] = Field(default_factory=list)
    metrics: list[GoogleAnalyticsCompatibilityItem] = Field(default_factory=list)
