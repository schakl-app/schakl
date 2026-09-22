"""What the leads dashboard answers with.

A **widget** is one question answered in one of a handful of shapes (a scorecard, a bar list, a
line, a donut, a pivot, a table, a funnel, a combo of bars and a line). The shape is a
rendering instruction; the numbers are the same rows whichever shape they wear, so the screen
draws every widget generically and a report section can reuse the rows without the shape.

Labels for a dimension's *values* are resolved on the API (the tenant's per-locale labels from
the profile), so a widget row carries ``key`` (the raw value, what a filter posts back) beside
``label`` (what is printed). Titles are i18n **keys** — a title is ours, a value label is the
tenant's, and the two never travel in the same field.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

WidgetKind = Literal["scorecard", "bars", "line", "donut", "pivot", "table", "funnel", "combo"]
Unit = Literal["count", "money", "percent", "ratio", "text"]


class LeadColumn(BaseModel):
    key: str
    #: An i18n key for a column we name (``marketing.leads.col.started``) …
    title_key: str | None = None
    #: … or the tenant's own text for a column they named (a pivot's channel columns).
    title: str | None = None
    unit: Unit = "count"
    #: Conditional formatting: a threshold above which the cell is drawn as a fault (dropout).
    warn_above: float | None = None
    alarm_above: float | None = None


class LeadRow(BaseModel):
    #: The raw value — what a click on the row posts as a filter.
    key: str
    label: str
    values: dict[str, float | None] = Field(default_factory=dict)
    #: Which channel group a channel row belongs to, when the widget is grouped.
    group: str | None = None
    #: A second dimension for a pivot cell set, ``{column key: value}``.
    cells: dict[str, float] | None = None
    #: Text cells beside the numbers — a campaign name, a mapped service, a category.
    texts: dict[str, str] = Field(default_factory=dict)


class LeadSeries(BaseModel):
    dates: list[date]
    #: ``{series key: one value per date}``.
    values: dict[str, list[float]]
    #: Which series draws as bars in a combo (the rest draw as lines, right axis).
    bars: list[str] = Field(default_factory=list)
    units: dict[str, Unit] = Field(default_factory=dict)


class LeadWidget(BaseModel):
    key: str
    part: Literal["leads", "ads"]
    kind: WidgetKind
    source: Literal["ga4", "gads"]
    title_key: str
    #: Which report(s) answered it — the traceability the spec asks for.
    reports: list[str] = Field(default_factory=list)
    #: The dimension the rows are grouped by, when they are — the key a click filters on.
    dimension: str | None = None
    #: The tenant's title for that dimension, when they gave one.
    dimension_title: str | None = None
    #: Whether a row of this widget is a page filter. The API's statement, not the screen's
    #: guess: a widget may group by a dimension the page cannot be narrowed by (a channel, an
    #: error reason), and a row drawn as a link there is a control that empties the dashboard.
    filterable: bool = False
    value: float | None = None
    #: A second figure drawn beside the first on a scorecard ("waarvan primair").
    secondary: float | None = None
    secondary_key: str | None = None
    unit: Unit = "count"
    currency: str | None = None
    columns: list[LeadColumn] = Field(default_factory=list)
    rows: list[LeadRow] = Field(default_factory=list)
    #: Google's count of the whole answer when the rows were cut.
    row_count: int | None = None
    series: LeadSeries | None = None
    total: float | None = None
    #: Rows folded into one "other" slice (a donut keeps at most six visible).
    other: float | None = None
    #: A sentence the widget carries (an i18n key), e.g. "primary conversions only".
    note_key: str | None = None


class LeadWarning(BaseModel):
    code: str
    severity: Literal["info", "warning", "error"] = "warning"
    details: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class LeadCoverage(BaseModel):
    """How much of the requests a dimension actually carries — the ``(not set)`` share."""

    dimension: str
    title: str | None = None
    total: float
    not_set: float
    share: float


class LeadBreakpoint(BaseModel):
    date: date
    text: str | None = None
    severity: Literal["hard", "soft"]


class LeadFilterOption(BaseModel):
    key: str
    label: str
    count: float


class LeadFilter(BaseModel):
    """A dimension the reader may narrow on, with the values seen in this period."""

    dimension: str
    title: str | None = None
    options: list[LeadFilterOption] = Field(default_factory=list)
    active: list[str] = Field(default_factory=list)


class LeadUnavailable(BaseModel):
    key: str
    reason: str


class LeadsWindow(BaseModel):
    start: date
    end: date
    token: str
    #: The floor of comparable reporting (the latest hard breakpoint), if any.
    comparable_from: date | None = None


class LeadsDashboard(BaseModel):
    company_id: uuid.UUID
    #: ``False`` when the client has no profile — the screen teaches, nothing draws.
    configured: bool
    can_manage: bool = False
    window: LeadsWindow | None = None
    widgets: list[LeadWidget] = Field(default_factory=list)
    #: Widgets the profile cannot answer (and why) — a manager's view, so the editor can say
    #: what a missing role would unlock. Withheld from a client login.
    unavailable: list[LeadUnavailable] = Field(default_factory=list)
    warnings: list[LeadWarning] = Field(default_factory=list)
    coverage: list[LeadCoverage] = Field(default_factory=list)
    breakpoints: list[LeadBreakpoint] = Field(default_factory=list)
    filters: list[LeadFilter] = Field(default_factory=list)
    disclaimer: str | None = None
    #: When the numbers were read from Google (the cache's timestamp, so a reader can tell a
    #: cached hour-old answer from a live one).
    refreshed_at: datetime | None = None
    ga4_available: bool = False
    ads_available: bool = False
    #: A leads-part deep link into the property, staff only.
    ga4_deep_link: str = ""
    ads_deep_link: str = ""


# --- the editor's catalog --------------------------------------------------------------------- #
class CatalogEvent(BaseModel):
    name: str
    count: float


class CatalogDimension(BaseModel):
    parameter: str
    field: str
    display_name: str


class CatalogAction(BaseModel):
    name: str
    category: str | None = None
    primary: bool | None = None


class LeadsCatalog(BaseModel):
    """What the client's property and account actually carry — so the profile editor offers
    the event names, dimensions, key events and conversion actions to pick from rather than
    asking somebody to type them from memory."""

    ga4_available: bool = False
    ads_available: bool = False
    events: list[CatalogEvent] = Field(default_factory=list)
    custom_dimensions: list[CatalogDimension] = Field(default_factory=list)
    key_events: list[str] = Field(default_factory=list)
    conversion_actions: list[CatalogAction] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)
    unavailable_reason: str | None = None
