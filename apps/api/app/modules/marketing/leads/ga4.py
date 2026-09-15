"""The GA4 half of a leads dashboard: which reports are asked for, and how an answer is read.

A dashboard is a **plan** of a handful of ``runReport`` requests, all derived from the profile
and the reader's filters, sent as at most two ``batchRunReports`` calls (Google allows five per
batch). Each report answers one or more widgets and is named so the payload can say which —
"every number is traceable to one request" is the rule the Looker build plan enforced by hand
and this enforces by construction.

Reading an answer follows the ``google_analytics`` integration's rules rather than memory: the
columns come from the response headers, every metric is a string on the wire, and the metadata
that says the answer was sampled, thresholded or folded into an ``(other)`` row is carried out
as a warning rather than dropped — a sampled number reads as a count on every screen it lands on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.modules.marketing.leads.filters import (
    FilterExpression,
    all_of,
    any_of,
    event_filter,
    in_list,
)
from app.modules.marketing.leads.profile import (
    DIM_ERROR_REASON,
    DIM_FORM_TYPE,
    DIM_LANGUAGE,
    DIM_PAGE_PATH,
    DIM_PAGE_TITLE,
    DIM_SERVICE,
    ROLE_APPLICATION,
    ROLE_EMAIL,
    ROLE_FORM_ERROR,
    ROLE_FORM_STARTED,
    ROLE_FORM_SUBMITTED,
    ROLE_PHONE,
    ROLE_REQUEST,
    ROLES,
    LeadProfile,
)

#: GA4 limits one ``batchRunReports`` call to five requests.
BATCH_SIZE = 5
#: Rows per breakdown. A page table with more than this is a table nobody reads; the payload
#: says when it was cut (``row_count`` beside the rows).
BREAKDOWN_LIMIT = 100
SOURCE_LIMIT = 10
#: Days × events is bounded by the period cap (400) × the roles (7).
DAILY_LIMIT = 5000

EVENT_NAME = "eventName"
DATE = "date"
CHANNEL = "sessionDefaultChannelGroup"
SOURCE_MEDIUM = "sessionSourceMedium"
EVENT_COUNT = "eventCount"
SESSIONS = "sessions"

# The report keys. A widget names the report it read, so the two vocabularies are one.
R_EVENTS = "events"
R_DAILY = "daily"
R_SERVICE = "service"
R_CHANNEL = "channel"
R_PAGE = "page"
R_LANGUAGE = "language"
R_ERRORS = "errors"
R_SOURCE_MEDIUM = "source_medium"
R_TRAFFIC = "traffic"


@dataclass(frozen=True)
class ReportSpec:
    key: str
    dimensions: tuple[str, ...]
    metrics: tuple[str, ...] = (EVENT_COUNT,)
    dimension_filter: FilterExpression | None = None
    limit: int = BREAKDOWN_LIMIT
    #: Ordered by this metric, descending; ``None`` orders by the first dimension ascending.
    order_metric: str | None = EVENT_COUNT

    def request(self, start: date, end: date) -> dict[str, Any]:
        body: dict[str, Any] = {
            "dateRanges": [{"startDate": start.isoformat(), "endDate": end.isoformat()}],
            "dimensions": [{"name": name} for name in self.dimensions],
            "metrics": [{"name": name} for name in self.metrics],
            "limit": self.limit,
            "keepEmptyRows": False,
        }
        if self.order_metric:
            body["orderBys"] = [{"metric": {"metricName": self.order_metric}, "desc": True}]
        elif self.dimensions:
            body["orderBys"] = [{"dimension": {"dimensionName": self.dimensions[0]}}]
        if self.dimension_filter:
            body["dimensionFilter"] = self.dimension_filter
        return body


@dataclass
class ParsedReport:
    key: str
    dimensions: list[str] = field(default_factory=list)
    metrics: list[str] = field(default_factory=list)
    #: ``[(dimension values, metric values)]`` in the order Google returned them.
    rows: list[tuple[list[str], list[float]]] = field(default_factory=list)
    #: Google's own count of the whole answer, before ``limit``.
    row_count: int = 0
    sampled: bool = False
    thresholded: bool = False
    other_row: bool = False
    #: Set when this one report failed inside the batch (Google answers per report).
    error: str | None = None

    @property
    def truncated(self) -> bool:
        return self.row_count > len(self.rows)

    def column(self, metric: str) -> int:
        return self.metrics.index(metric)

    def dim(self, name: str) -> int:
        return self.dimensions.index(name)


def _num(raw: Any) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def parse_report(key: str, body: dict[str, Any]) -> ParsedReport:
    """One ``runReport`` answer, read from its headers rather than from the request."""
    if "error" in body and "rows" not in body:
        err = body.get("error") or {}
        return ParsedReport(key=key, error=str(err.get("status") or err.get("code") or "error"))
    dims = [h.get("name", "") for h in body.get("dimensionHeaders", [])]
    metrics = [h.get("name", "") for h in body.get("metricHeaders", [])]
    rows: list[tuple[list[str], list[float]]] = []
    for row in body.get("rows", []):
        dvals = [v.get("value", "") for v in row.get("dimensionValues", [])]
        mvals = [_num(v.get("value")) for v in row.get("metricValues", [])]
        rows.append((dvals, mvals))
    metadata = body.get("metadata") or {}
    return ParsedReport(
        key=key,
        dimensions=dims,
        metrics=metrics,
        rows=rows,
        row_count=int(body.get("rowCount") or len(rows)),
        sampled=bool(metadata.get("samplingMetadatas")),
        thresholded=bool(metadata.get("subjectToThresholding")),
        other_row=bool(metadata.get("dataLossFromOtherRow")),
    )


def parse_ga4_date(value: str) -> date:
    return date(int(value[0:4]), int(value[4:6]), int(value[6:8]))


UserFilters = dict[str, list[str]]


def user_filter_expression(profile: LeadProfile, filters: UserFilters) -> FilterExpression | None:
    """The reader's own narrowing (``?f.service=autotransport``) as one AND of IN-lists, on the
    profile's field names. A key the profile has no dimension for is ignored: it cannot be
    expressed, and a filter silently dropped is worse than one refused — so the service refuses
    it *before* building the plan (see ``service.py``); this only translates."""
    parts: list[FilterExpression | None] = []
    for key, values in filters.items():
        spec = profile.dimension(key)
        if spec is None or not values:
            continue
        parts.append(in_list(spec.field, values))
    return all_of(parts)


def roles_expression(profile: LeadProfile, roles: tuple[str, ...]) -> FilterExpression | None:
    """``eventName`` in any event of any of ``roles`` the profile carries."""
    parts = [event_filter(profile.roles.get(role) or []) for role in roles]
    return any_of([p for p in parts if p])


def build_plan(profile: LeadProfile, filters: UserFilters) -> list[ReportSpec]:
    """Every report this profile can answer, in a stable order.

    A report is planned only when the roles and dimensions it needs exist — the same test each
    widget applies, made once here so a widget never finds its report missing for a reason it
    did not predict. What the profile lacks is simply not asked of Google.
    """
    user = user_filter_expression(profile, filters)
    field_of = {key: spec.field for key, spec in profile.dimensions.items()}
    plan: list[ReportSpec] = []

    def spec(
        key: str,
        dims: list[str],
        roles: tuple[str, ...],
        *,
        limit: int = BREAKDOWN_LIMIT,
        order: str | None = EVENT_COUNT,
    ) -> None:
        role_expr = roles_expression(profile, roles)
        if role_expr is None:
            return
        plan.append(
            ReportSpec(
                key=key,
                dimensions=tuple(dims),
                dimension_filter=all_of([role_expr, user]),
                limit=limit,
                order_metric=order,
            )
        )

    # Every role in one report: the scorecards, split by form type where there is one.
    events_dims = [EVENT_NAME]
    if DIM_FORM_TYPE in field_of:
        events_dims.append(field_of[DIM_FORM_TYPE])
    spec(R_EVENTS, events_dims, ROLES, limit=1000)
    spec(
        R_DAILY,
        [DATE, EVENT_NAME],
        (ROLE_REQUEST, ROLE_FORM_STARTED, ROLE_FORM_SUBMITTED, ROLE_FORM_ERROR),
        limit=DAILY_LIMIT,
        order=None,
    )
    if DIM_SERVICE in field_of:
        spec(
            R_SERVICE,
            [EVENT_NAME, field_of[DIM_SERVICE]],
            (ROLE_REQUEST, ROLE_FORM_STARTED, ROLE_FORM_SUBMITTED),
            limit=1000,
        )
        spec(R_CHANNEL, [CHANNEL, field_of[DIM_SERVICE]], (ROLE_REQUEST,), limit=1000)
    else:
        spec(R_CHANNEL, [CHANNEL], (ROLE_REQUEST,))
    page_field = field_of.get(DIM_PAGE_TITLE) or field_of.get(DIM_PAGE_PATH)
    if page_field:
        spec(R_PAGE, [page_field], (ROLE_REQUEST,))
    if DIM_LANGUAGE in field_of:
        spec(R_LANGUAGE, [field_of[DIM_LANGUAGE]], (ROLE_REQUEST,))
    if profile.has_role(ROLE_FORM_ERROR):
        error_dims = [d for d in (field_of.get(DIM_ERROR_REASON), page_field) if d]
        if error_dims:
            spec(R_ERRORS, error_dims, (ROLE_FORM_ERROR,))
    spec(R_SOURCE_MEDIUM, [SOURCE_MEDIUM], (ROLE_REQUEST,), limit=SOURCE_LIMIT)
    if profile.has_role(ROLE_REQUEST):
        # Site traffic by day, unfiltered: what the silent-zero check reads against.
        plan.append(
            ReportSpec(
                key=R_TRAFFIC,
                dimensions=(DATE,),
                metrics=(SESSIONS,),
                limit=DAILY_LIMIT,
                order_metric=None,
            )
        )
    return plan


def batches(plan: list[ReportSpec], start: date, end: date) -> list[list[dict[str, Any]]]:
    """The plan as ``batchRunReports`` bodies, five requests each."""
    bodies = [spec.request(start, end) for spec in plan]
    return [bodies[i : i + BATCH_SIZE] for i in range(0, len(bodies), BATCH_SIZE)]


#: Roles a widget may read from the events report, in the order the scorecards draw them.
CONTACT_ROLES = (ROLE_PHONE, ROLE_EMAIL)
ALL_ROLES = ROLES
__all__ = [
    "ALL_ROLES",
    "CONTACT_ROLES",
    "ROLE_APPLICATION",
    "ROLE_EMAIL",
    "ROLE_FORM_ERROR",
    "ROLE_FORM_STARTED",
    "ROLE_FORM_SUBMITTED",
    "ROLE_PHONE",
    "ROLE_REQUEST",
]
