"""The widget catalog: the fixed questions a leads dashboard asks, and how each is answered
from the profile and the GA4 reports.

A widget declares what it *needs* — which roles, which dimensions — and is computed only when
the profile carries them; otherwise it is listed as unavailable with the reason, so the editor
can say what adding a form-start role would unlock. What it never does is draw a zero for a
thing that was not measured: "no funnel" and "100% dropout" are different sentences.

Every number here is arithmetic over one report's rows. The funnel is the one place two
event roles meet in one table (started beside submitted per service), which is exactly what a
Looker blend did with a left outer join — and the left join matters: a service with starts and
no submissions is the row the funnel exists to show.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from app.modules.marketing.leads import ga4 as g
from app.modules.marketing.leads.filters import matches
from app.modules.marketing.leads.profile import (
    CHANNEL_GROUPS,
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
    LeadProfile,
    channel_group_for,
)
from app.modules.marketing.leads.schemas import (
    LeadColumn,
    LeadCoverage,
    LeadRow,
    LeadSeries,
    LeadUnavailable,
    LeadWarning,
    LeadWidget,
)

NOT_SET = "(not set)"
#: A donut keeps this many named slices; the rest fold into one "other" figure.
DONUT_SLICES = 6
#: Dropout thresholds, as the Looker build used them: red at 60 %, amber at 35 %.
DROPOUT_ALARM = 0.6
DROPOUT_WARN = 0.35
#: The silent-zero window: this many days of traffic with no request at all.
SILENT_DAYS = 7


@dataclass(frozen=True)
class WidgetDef:
    key: str
    part: str
    kind: str
    source: str
    #: Any one of these roles present is enough.
    roles_any: tuple[str, ...] = ()
    #: Every one of these roles must be present.
    roles_all: tuple[str, ...] = ()
    #: Every one of these dimensions must be present (a tuple of alternatives is "any of").
    dims_all: tuple[str | tuple[str, ...], ...] = ()
    needs_quote_values: bool = False
    needs_ads: bool = False
    #: The reports it reads, for the payload's traceability.
    reports: tuple[str, ...] = ()


_PAGE_DIM = (DIM_PAGE_TITLE, DIM_PAGE_PATH)

WIDGETS: tuple[WidgetDef, ...] = (
    WidgetDef(
        "contacts",
        "leads",
        "scorecard",
        "ga4",
        roles_any=(ROLE_PHONE, ROLE_EMAIL),
        reports=(g.R_EVENTS,),
    ),
    WidgetDef(
        "requests", "leads", "scorecard", "ga4", roles_all=(ROLE_REQUEST,), reports=(g.R_EVENTS,)
    ),
    WidgetDef(
        "quotes",
        "leads",
        "scorecard",
        "ga4",
        roles_all=(ROLE_REQUEST,),
        dims_all=(DIM_FORM_TYPE,),
        needs_quote_values=True,
        reports=(g.R_EVENTS,),
    ),
    WidgetDef(
        "failures", "leads", "scorecard", "ga4", roles_all=(ROLE_FORM_ERROR,), reports=(g.R_EVENTS,)
    ),
    WidgetDef(
        "applications",
        "leads",
        "scorecard",
        "ga4",
        roles_all=(ROLE_APPLICATION,),
        reports=(g.R_EVENTS,),
    ),
    WidgetDef(
        "requests_by_service",
        "leads",
        "bars",
        "ga4",
        roles_all=(ROLE_REQUEST,),
        dims_all=(DIM_SERVICE,),
        reports=(g.R_SERVICE,),
    ),
    WidgetDef(
        "requests_by_day", "leads", "line", "ga4", roles_all=(ROLE_REQUEST,), reports=(g.R_DAILY,)
    ),
    WidgetDef(
        "requests_by_channel",
        "leads",
        "donut",
        "ga4",
        roles_all=(ROLE_REQUEST,),
        reports=(g.R_CHANNEL,),
    ),
    WidgetDef(
        "requests_by_channel_group",
        "leads",
        "bars",
        "ga4",
        roles_all=(ROLE_REQUEST,),
        reports=(g.R_CHANNEL,),
    ),
    WidgetDef(
        "service_by_channel",
        "leads",
        "pivot",
        "ga4",
        roles_all=(ROLE_REQUEST,),
        dims_all=(DIM_SERVICE,),
        reports=(g.R_CHANNEL,),
    ),
    WidgetDef(
        "requests_by_page",
        "leads",
        "table",
        "ga4",
        roles_all=(ROLE_REQUEST,),
        dims_all=(_PAGE_DIM,),
        reports=(g.R_PAGE,),
    ),
    WidgetDef(
        "requests_by_form_type",
        "leads",
        "bars",
        "ga4",
        roles_all=(ROLE_REQUEST,),
        dims_all=(DIM_FORM_TYPE,),
        reports=(g.R_EVENTS,),
    ),
    WidgetDef(
        "requests_by_language",
        "leads",
        "bars",
        "ga4",
        roles_all=(ROLE_REQUEST,),
        dims_all=(DIM_LANGUAGE,),
        reports=(g.R_LANGUAGE,),
    ),
    WidgetDef(
        "funnel",
        "leads",
        "funnel",
        "ga4",
        roles_all=(ROLE_FORM_STARTED,),
        roles_any=(ROLE_FORM_SUBMITTED, ROLE_REQUEST),
        dims_all=(DIM_SERVICE,),
        reports=(g.R_SERVICE,),
    ),
    WidgetDef(
        "failures_by_reason",
        "leads",
        "bars",
        "ga4",
        roles_all=(ROLE_FORM_ERROR,),
        dims_all=(DIM_ERROR_REASON,),
        reports=(g.R_ERRORS,),
    ),
    WidgetDef(
        "failures_by_page",
        "leads",
        "table",
        "ga4",
        roles_all=(ROLE_FORM_ERROR,),
        dims_all=(_PAGE_DIM,),
        reports=(g.R_ERRORS,),
    ),
    WidgetDef(
        "requests_by_source",
        "leads",
        "table",
        "ga4",
        roles_all=(ROLE_REQUEST,),
        reports=(g.R_SOURCE_MEDIUM,),
    ),
    # --- the advertising half (computed in ads.py, declared here so the catalog is one list) ---
    WidgetDef("ads_cost", "ads", "scorecard", "gads", needs_ads=True, reports=("ads_daily",)),
    WidgetDef(
        "ads_conversions", "ads", "scorecard", "gads", needs_ads=True, reports=("ads_daily",)
    ),
    WidgetDef(
        "ads_cost_per_conversion",
        "ads",
        "scorecard",
        "gads",
        needs_ads=True,
        reports=("ads_daily",),
    ),
    WidgetDef(
        "ads_conversion_value", "ads", "scorecard", "gads", needs_ads=True, reports=("ads_daily",)
    ),
    WidgetDef("ads_by_day", "ads", "combo", "gads", needs_ads=True, reports=("ads_daily",)),
    WidgetDef("ads_campaigns", "ads", "table", "gads", needs_ads=True, reports=("ads_campaigns",)),
    WidgetDef(
        "ads_impression_share", "ads", "table", "gads", needs_ads=True, reports=("ads_campaigns",)
    ),
    WidgetDef(
        "ads_conversion_actions", "ads", "table", "gads", needs_ads=True, reports=("ads_actions",)
    ),
)

WIDGET_KEYS: tuple[str, ...] = tuple(w.key for w in WIDGETS)
WIDGETS_BY_KEY: dict[str, WidgetDef] = {w.key: w for w in WIDGETS}


def missing_for(definition: WidgetDef, profile: LeadProfile, *, has_ads: bool) -> str | None:
    """Why this widget cannot be drawn for this profile — ``None`` when it can."""
    if definition.needs_ads and not has_ads:
        return "no_ads"
    if definition.roles_any and not any(profile.has_role(r) for r in definition.roles_any):
        return f"missing_role:{definition.roles_any[0]}"
    for role in definition.roles_all:
        if not profile.has_role(role):
            return f"missing_role:{role}"
    for dim in definition.dims_all:
        options = dim if isinstance(dim, tuple) else (dim,)
        if not any(profile.has_dimension(d) for d in options):
            return f"missing_dimension:{options[0]}"
    if definition.needs_quote_values and not profile.quote_values:
        return "missing_quote_values"
    return None


@dataclass
class Computed:
    widgets: list[LeadWidget] = field(default_factory=list)
    unavailable: list[LeadUnavailable] = field(default_factory=list)
    warnings: list[LeadWarning] = field(default_factory=list)
    coverage: list[LeadCoverage] = field(default_factory=list)
    #: ``{dimension key: {raw value: count}}`` over requests — the filter controls' options.
    seen_values: dict[str, dict[str, float]] = field(default_factory=dict)


class _Ctx:
    """What every GA4 widget computation reads."""

    def __init__(
        self,
        profile: LeadProfile,
        reports: dict[str, g.ParsedReport],
        *,
        start: date,
        end: date,
        locale: str,
        channel_groups: dict[str, list[str]],
    ) -> None:
        self.profile = profile
        self.reports = reports
        self.start = start
        self.end = end
        self.locale = locale
        self.channel_groups = channel_groups
        self._role_cache: dict[str, frozenset[str]] = {}

    def roles_of(self, event_name: str) -> frozenset[str]:
        cached = self._role_cache.get(event_name)
        if cached is None:
            cached = frozenset(
                role
                for role, matchers in self.profile.roles.items()
                if matches(matchers, event_name)
            )
            self._role_cache[event_name] = cached
        return cached

    def label(self, dim_key: str, raw: str) -> str:
        spec = self.profile.dimension(dim_key)
        return spec.value_label(raw, self.locale) if spec else raw

    def title(self, dim_key: str) -> str | None:
        spec = self.profile.dimension(dim_key)
        return spec.title(self.locale) if spec else None

    def report(self, key: str) -> g.ParsedReport | None:
        report = self.reports.get(key)
        return report if report is not None and report.error is None else None

    def page_dim(self) -> str | None:
        for key in _PAGE_DIM:
            if self.profile.has_dimension(key):
                return key
        return None


def compute(
    profile: LeadProfile,
    reports: dict[str, g.ParsedReport],
    *,
    start: date,
    end: date,
    locale: str,
    channel_groups: dict[str, list[str]],
    has_ads: bool,
) -> Computed:
    """Every GA4 widget the profile allows, from the reports the plan fetched."""
    ctx = _Ctx(profile, reports, start=start, end=end, locale=locale, channel_groups=channel_groups)
    out = Computed()
    hidden = set(profile.hidden_widgets)
    for definition in WIDGETS:
        if definition.source != "ga4":
            # The advertising half is computed in ads.py; what this pass owes it is the
            # answer for a client with no account at all, so the list of what is missing is
            # one list.
            if not has_ads:
                out.unavailable.append(LeadUnavailable(key=definition.key, reason="no_ads"))
            continue
        reason = missing_for(definition, profile, has_ads=has_ads)
        if reason:
            out.unavailable.append(LeadUnavailable(key=definition.key, reason=reason))
            continue
        if definition.key in hidden:
            out.unavailable.append(LeadUnavailable(key=definition.key, reason="hidden"))
            continue
        report_missing = next((r for r in definition.reports if ctx.report(r) is None), None)
        if report_missing:
            out.unavailable.append(
                LeadUnavailable(key=definition.key, reason=f"report_failed:{report_missing}")
            )
            continue
        widget = _BUILDERS[definition.key](ctx, definition)
        if widget is not None:
            out.widgets.append(widget)
    _quality(ctx, out)
    _coverage(ctx, out)
    _seen_values(ctx, out)
    return out


# --- helpers ------------------------------------------------------------------------------- #
def _base(definition: WidgetDef, **kwargs) -> LeadWidget:  # noqa: ANN003
    return LeadWidget(
        key=definition.key,
        part=definition.part,  # type: ignore[arg-type]
        kind=definition.kind,  # type: ignore[arg-type]
        source=definition.source,  # type: ignore[arg-type]
        title_key=f"marketing.leads.widget.{definition.key}",
        reports=list(definition.reports),
        **kwargs,
    )


def _role_sum(ctx: _Ctx, report: g.ParsedReport, roles: tuple[str, ...]) -> float:
    ev = report.dim(g.EVENT_NAME)
    col = report.column(g.EVENT_COUNT)
    return sum(m[col] for d, m in report.rows if ctx.roles_of(d[ev]) & set(roles))


def _by_dimension(
    ctx: _Ctx, report: g.ParsedReport, dim_index: int, roles: tuple[str, ...]
) -> dict[str, float]:
    """``{raw value: count}`` over the rows whose event plays one of ``roles``; a report with
    no event column counts every row (it was filtered to the role on the way in)."""
    col = report.column(g.EVENT_COUNT)
    has_event = g.EVENT_NAME in report.dimensions
    ev = report.dim(g.EVENT_NAME) if has_event else -1
    out: dict[str, float] = {}
    for d, m in report.rows:
        if has_event and not (ctx.roles_of(d[ev]) & set(roles)):
            continue
        out[d[dim_index]] = out.get(d[dim_index], 0.0) + m[col]
    return out


def _rows(
    ctx: _Ctx, dim_key: str, counts: dict[str, float], *, value_key: str = "count"
) -> list[LeadRow]:
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [
        LeadRow(key=raw, label=ctx.label(dim_key, raw), values={value_key: value})
        for raw, value in ordered
    ]


def _count_columns() -> list[LeadColumn]:
    return [LeadColumn(key="count", title_key="marketing.leads.col.count")]


# --- scorecards ---------------------------------------------------------------------------- #
def _scorecard(ctx: _Ctx, definition: WidgetDef, roles: tuple[str, ...]) -> LeadWidget:
    report = ctx.report(g.R_EVENTS)
    assert report is not None
    return _base(definition, value=_role_sum(ctx, report, roles))


def _contacts(ctx: _Ctx, d: WidgetDef) -> LeadWidget:
    return _scorecard(ctx, d, (ROLE_PHONE, ROLE_EMAIL))


def _requests(ctx: _Ctx, d: WidgetDef) -> LeadWidget:
    return _scorecard(ctx, d, (ROLE_REQUEST,))


def _failures(ctx: _Ctx, d: WidgetDef) -> LeadWidget:
    return _scorecard(ctx, d, (ROLE_FORM_ERROR,))


def _applications(ctx: _Ctx, d: WidgetDef) -> LeadWidget:
    return _scorecard(ctx, d, (ROLE_APPLICATION,))


def _quotes(ctx: _Ctx, d: WidgetDef) -> LeadWidget:
    report = ctx.report(g.R_EVENTS)
    assert report is not None
    spec = ctx.profile.dimension(DIM_FORM_TYPE)
    assert spec is not None
    ft = report.dim(spec.field)
    ev = report.dim(g.EVENT_NAME)
    col = report.column(g.EVENT_COUNT)
    quote = {v.lower() for v in ctx.profile.quote_values}
    total = sum(
        m[col]
        for dims, m in report.rows
        if ROLE_REQUEST in ctx.roles_of(dims[ev]) and dims[ft].lower() in quote
    )
    return _base(d, value=total)


# --- breakdowns ---------------------------------------------------------------------------- #
def _requests_by_service(ctx: _Ctx, d: WidgetDef) -> LeadWidget:
    report = ctx.report(g.R_SERVICE)
    assert report is not None
    spec = ctx.profile.dimension(DIM_SERVICE)
    assert spec is not None
    counts = _by_dimension(ctx, report, report.dim(spec.field), (ROLE_REQUEST,))
    return _base(
        d,
        dimension=DIM_SERVICE,
        dimension_title=ctx.title(DIM_SERVICE),
        columns=_count_columns(),
        rows=_rows(ctx, DIM_SERVICE, counts),
        total=sum(counts.values()),
    )


def _requests_by_form_type(ctx: _Ctx, d: WidgetDef) -> LeadWidget:
    report = ctx.report(g.R_EVENTS)
    assert report is not None
    spec = ctx.profile.dimension(DIM_FORM_TYPE)
    assert spec is not None
    counts = _by_dimension(ctx, report, report.dim(spec.field), (ROLE_REQUEST,))
    return _base(
        d,
        dimension=DIM_FORM_TYPE,
        dimension_title=ctx.title(DIM_FORM_TYPE),
        columns=_count_columns(),
        rows=_rows(ctx, DIM_FORM_TYPE, counts),
        total=sum(counts.values()),
    )


def _requests_by_language(ctx: _Ctx, d: WidgetDef) -> LeadWidget:
    report = ctx.report(g.R_LANGUAGE)
    assert report is not None
    spec = ctx.profile.dimension(DIM_LANGUAGE)
    assert spec is not None
    counts = _by_dimension(ctx, report, report.dim(spec.field), (ROLE_REQUEST,))
    return _base(
        d,
        dimension=DIM_LANGUAGE,
        dimension_title=ctx.title(DIM_LANGUAGE),
        columns=_count_columns(),
        rows=_rows(ctx, DIM_LANGUAGE, counts),
        total=sum(counts.values()),
    )


def _requests_by_page(ctx: _Ctx, d: WidgetDef) -> LeadWidget:
    report = ctx.report(g.R_PAGE)
    assert report is not None
    dim_key = ctx.page_dim()
    assert dim_key is not None
    spec = ctx.profile.dimension(dim_key)
    assert spec is not None
    counts = _by_dimension(ctx, report, report.dim(spec.field), (ROLE_REQUEST,))
    return _base(
        d,
        dimension=dim_key,
        dimension_title=ctx.title(dim_key),
        columns=_count_columns(),
        rows=_rows(ctx, dim_key, counts),
        row_count=report.row_count if report.truncated else None,
        total=sum(counts.values()),
    )


def _requests_by_source(ctx: _Ctx, d: WidgetDef) -> LeadWidget:
    report = ctx.report(g.R_SOURCE_MEDIUM)
    assert report is not None
    counts = _by_dimension(ctx, report, report.dim(g.SOURCE_MEDIUM), (ROLE_REQUEST,))
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return _base(
        d,
        columns=_count_columns(),
        rows=[LeadRow(key=raw, label=raw, values={"count": v}) for raw, v in ordered],
        row_count=report.row_count if report.truncated else None,
        total=sum(counts.values()),
    )


def _requests_by_day(ctx: _Ctx, d: WidgetDef) -> LeadWidget:
    report = ctx.report(g.R_DAILY)
    assert report is not None
    ev = report.dim(g.EVENT_NAME)
    dt = report.dim(g.DATE)
    col = report.column(g.EVENT_COUNT)
    days = [ctx.start + timedelta(days=i) for i in range((ctx.end - ctx.start).days + 1)]
    index = {day: i for i, day in enumerate(days)}
    series: dict[str, list[float]] = {"requests": [0.0] * len(days)}
    optional = {
        ROLE_FORM_STARTED: "started",
        ROLE_FORM_SUBMITTED: "submitted",
        ROLE_FORM_ERROR: "failures",
    }
    for role, name in optional.items():
        if ctx.profile.has_role(role):
            series[name] = [0.0] * len(days)
    for dims, m in report.rows:
        try:
            day = g.parse_ga4_date(dims[dt])
        except (ValueError, IndexError):
            continue
        i = index.get(day)
        if i is None:
            continue
        roles = ctx.roles_of(dims[ev])
        if ROLE_REQUEST in roles:
            series["requests"][i] += m[col]
        for role, name in optional.items():
            if role in roles and name in series:
                series[name][i] += m[col]
    return _base(
        d,
        series=LeadSeries(dates=days, values=series, units={k: "count" for k in series}),
        total=sum(series["requests"]),
    )


def _channel_counts(ctx: _Ctx) -> tuple[dict[str, float], dict[tuple[str, str], float]]:
    """``{channel: requests}`` and ``{(service, channel): requests}`` from the channel report."""
    report = ctx.report(g.R_CHANNEL)
    assert report is not None
    ch = report.dim(g.CHANNEL)
    col = report.column(g.EVENT_COUNT)
    spec = ctx.profile.dimension(DIM_SERVICE)
    sv = report.dim(spec.field) if spec and spec.field in report.dimensions else -1
    by_channel: dict[str, float] = {}
    by_pair: dict[tuple[str, str], float] = {}
    for dims, m in report.rows:
        by_channel[dims[ch]] = by_channel.get(dims[ch], 0.0) + m[col]
        if sv >= 0:
            key = (dims[sv], dims[ch])
            by_pair[key] = by_pair.get(key, 0.0) + m[col]
    return by_channel, by_pair


def _requests_by_channel(ctx: _Ctx, d: WidgetDef) -> LeadWidget:
    by_channel, _ = _channel_counts(ctx)
    ordered = sorted(by_channel.items(), key=lambda kv: (-kv[1], kv[0]))
    visible = ordered[:DONUT_SLICES]
    other = sum(v for _, v in ordered[DONUT_SLICES:])
    return _base(
        d,
        dimension="channel",
        columns=_count_columns(),
        rows=[
            LeadRow(
                key=name,
                label=name,
                values={"count": value},
                group=channel_group_for(name, ctx.channel_groups),
            )
            for name, value in visible
        ],
        other=other or None,
        total=sum(by_channel.values()),
    )


def _requests_by_channel_group(ctx: _Ctx, d: WidgetDef) -> LeadWidget:
    by_channel, _ = _channel_counts(ctx)
    groups: dict[str, float] = {}
    for name, value in by_channel.items():
        group = channel_group_for(name, ctx.channel_groups)
        groups[group] = groups.get(group, 0.0) + value
    rows = [
        LeadRow(key=group, label=group, values={"count": groups[group]}, group=group)
        for group in CHANNEL_GROUPS
        if group in groups
    ]
    return _base(
        d,
        dimension="channel_group",
        columns=_count_columns(),
        rows=rows,
        total=sum(groups.values()),
    )


def _service_by_channel(ctx: _Ctx, d: WidgetDef) -> LeadWidget:
    by_channel, by_pair = _channel_counts(ctx)
    channels = [name for name, _ in sorted(by_channel.items(), key=lambda kv: (-kv[1], kv[0]))]
    services: dict[str, float] = {}
    for (service, _channel), value in by_pair.items():
        services[service] = services.get(service, 0.0) + value
    rows = []
    for service in sorted(services, key=lambda s: (-services[s], s)):
        cells = {c: by_pair.get((service, c), 0.0) for c in channels}
        rows.append(
            LeadRow(
                key=service,
                label=ctx.label(DIM_SERVICE, service),
                values={"count": services[service]},
                cells=cells,
            )
        )
    return _base(
        d,
        dimension=DIM_SERVICE,
        dimension_title=ctx.title(DIM_SERVICE),
        columns=[LeadColumn(key=c, title=c) for c in channels]
        + [LeadColumn(key="count", title_key="marketing.leads.col.total")],
        rows=rows,
        total=sum(services.values()),
    )


def _funnel(ctx: _Ctx, d: WidgetDef) -> LeadWidget:
    report = ctx.report(g.R_SERVICE)
    assert report is not None
    spec = ctx.profile.dimension(DIM_SERVICE)
    assert spec is not None
    sv = report.dim(spec.field)
    # "Submitted" is the technical success where a client measures it, else the request itself
    # — a client with one derived key event and no separate submit event still has a funnel.
    submitted_role = (
        ROLE_FORM_SUBMITTED if ctx.profile.has_role(ROLE_FORM_SUBMITTED) else ROLE_REQUEST
    )
    started = _by_dimension(ctx, report, sv, (ROLE_FORM_STARTED,))
    submitted = _by_dimension(ctx, report, sv, (submitted_role,))
    rows: list[LeadRow] = []
    # Left outer: every service that was *started* is a row, whether or not anything was sent.
    for service in sorted(started, key=lambda s: (-started[s], s)):
        s, done = started[service], submitted.get(service, 0.0)
        dropout = (1 - done / s) if s > 0 else None
        rows.append(
            LeadRow(
                key=service,
                label=ctx.label(DIM_SERVICE, service),
                values={"started": s, "submitted": done, "dropout": dropout},
            )
        )
    # …and a service that was submitted without a recorded start is still a fact worth a row,
    # drawn with no dropout figure rather than a negative one.
    for service in sorted(set(submitted) - set(started)):
        rows.append(
            LeadRow(
                key=service,
                label=ctx.label(DIM_SERVICE, service),
                values={"started": 0.0, "submitted": submitted[service], "dropout": None},
            )
        )
    return _base(
        d,
        dimension=DIM_SERVICE,
        dimension_title=ctx.title(DIM_SERVICE),
        columns=[
            LeadColumn(key="started", title_key="marketing.leads.col.started"),
            LeadColumn(key="submitted", title_key="marketing.leads.col.submitted"),
            LeadColumn(
                key="dropout",
                title_key="marketing.leads.col.dropout",
                unit="ratio",
                warn_above=DROPOUT_WARN,
                alarm_above=DROPOUT_ALARM,
            ),
        ],
        rows=rows,
        note_key=None
        if submitted_role == ROLE_FORM_SUBMITTED
        else "marketing.leads.note.funnel_requests",
    )


def _failures_by_reason(ctx: _Ctx, d: WidgetDef) -> LeadWidget:
    report = ctx.report(g.R_ERRORS)
    assert report is not None
    spec = ctx.profile.dimension(DIM_ERROR_REASON)
    assert spec is not None
    counts = _by_dimension(ctx, report, report.dim(spec.field), (ROLE_FORM_ERROR,))
    return _base(
        d,
        dimension=DIM_ERROR_REASON,
        dimension_title=ctx.title(DIM_ERROR_REASON),
        columns=_count_columns(),
        rows=_rows(ctx, DIM_ERROR_REASON, counts),
        total=sum(counts.values()),
    )


def _failures_by_page(ctx: _Ctx, d: WidgetDef) -> LeadWidget:
    report = ctx.report(g.R_ERRORS)
    assert report is not None
    dim_key = ctx.page_dim()
    assert dim_key is not None
    spec = ctx.profile.dimension(dim_key)
    assert spec is not None
    counts = _by_dimension(ctx, report, report.dim(spec.field), (ROLE_FORM_ERROR,))
    return _base(
        d,
        dimension=dim_key,
        dimension_title=ctx.title(dim_key),
        columns=_count_columns(),
        rows=_rows(ctx, dim_key, counts),
        total=sum(counts.values()),
    )


_BUILDERS = {
    "contacts": _contacts,
    "requests": _requests,
    "quotes": _quotes,
    "failures": _failures,
    "applications": _applications,
    "requests_by_service": _requests_by_service,
    "requests_by_day": _requests_by_day,
    "requests_by_channel": _requests_by_channel,
    "requests_by_channel_group": _requests_by_channel_group,
    "service_by_channel": _service_by_channel,
    "requests_by_page": _requests_by_page,
    "requests_by_form_type": _requests_by_form_type,
    "requests_by_language": _requests_by_language,
    "funnel": _funnel,
    "failures_by_reason": _failures_by_reason,
    "failures_by_page": _failures_by_page,
    "requests_by_source": _requests_by_source,
}


# --- data quality ---------------------------------------------------------------------------- #
def _quality(ctx: _Ctx, out: Computed) -> None:
    flags = {"sampled": False, "thresholded": False, "other_row": False}
    for report in ctx.reports.values():
        if report.error:
            out.warnings.append(
                LeadWarning(
                    code="report_failed",
                    severity="error",
                    details={"report": report.key, "reason": report.error},
                )
            )
            continue
        flags["sampled"] |= report.sampled
        flags["thresholded"] |= report.thresholded
        flags["other_row"] |= report.other_row
    for code, raised in flags.items():
        if raised:
            out.warnings.append(LeadWarning(code=code))
    # A silent zero: the last week of the window had traffic and no request at all — the one
    # failure a dashboard of zeros cannot distinguish from a quiet week.
    daily, traffic = ctx.report(g.R_DAILY), ctx.report(g.R_TRAFFIC)
    if (
        daily is not None
        and traffic is not None
        and g.SESSIONS in traffic.metrics
        and g.EVENT_COUNT in daily.metrics
    ):
        # The last week of the window, or the whole window when it is shorter: a five-day span
        # with traffic and no request is the same silence.
        since = max(ctx.start, ctx.end - timedelta(days=SILENT_DAYS - 1))
        ev, dt, col = daily.dim(g.EVENT_NAME), daily.dim(g.DATE), daily.column(g.EVENT_COUNT)
        requests = sum(
            m[col]
            for dims, m in daily.rows
            if ROLE_REQUEST in ctx.roles_of(dims[ev]) and g.parse_ga4_date(dims[dt]) >= since
        )
        tdt, tcol = traffic.dim(g.DATE), traffic.column(g.SESSIONS)
        sessions = sum(m[tcol] for dims, m in traffic.rows if g.parse_ga4_date(dims[tdt]) >= since)
        if requests == 0 and sessions > 0:
            out.warnings.append(
                LeadWarning(code="silent_zero", details={"days": SILENT_DAYS, "sessions": sessions})
            )


def _coverage(ctx: _Ctx, out: Computed) -> None:
    """The ``(not set)`` share per dimension the dashboard uses, over requests."""
    sources: list[tuple[str, str]] = [
        (DIM_SERVICE, g.R_SERVICE),
        (DIM_FORM_TYPE, g.R_EVENTS),
        (DIM_LANGUAGE, g.R_LANGUAGE),
    ]
    page = ctx.page_dim()
    if page:
        sources.append((page, g.R_PAGE))
    for dim_key, report_key in sources:
        spec = ctx.profile.dimension(dim_key)
        report = ctx.report(report_key)
        if spec is None or report is None or spec.field not in report.dimensions:
            continue
        counts = _by_dimension(ctx, report, report.dim(spec.field), (ROLE_REQUEST,))
        total = sum(counts.values())
        if total <= 0:
            continue
        not_set = counts.get(NOT_SET, 0.0)
        out.coverage.append(
            LeadCoverage(
                dimension=dim_key,
                title=ctx.title(dim_key),
                total=total,
                not_set=not_set,
                share=round(not_set / total, 4),
            )
        )


def _seen_values(ctx: _Ctx, out: Computed) -> None:
    """Which values each filterable dimension took over requests this period."""
    sources: list[tuple[str, str]] = [
        (DIM_SERVICE, g.R_SERVICE),
        (DIM_FORM_TYPE, g.R_EVENTS),
        (DIM_LANGUAGE, g.R_LANGUAGE),
    ]
    for dim_key, report_key in sources:
        spec = ctx.profile.dimension(dim_key)
        report = ctx.report(report_key)
        if (
            spec is None
            or not spec.filterable
            or report is None
            or spec.field not in report.dimensions
        ):
            continue
        counts = _by_dimension(ctx, report, report.dim(spec.field), (ROLE_REQUEST,))
        out.seen_values[dim_key] = {k: v for k, v in counts.items() if k != NOT_SET}
