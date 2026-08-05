"""The dict a report design renders against (issue #300).

**Strings, never rows.** A tenant's own Jinja renders against this exact dict, so if it held
ORM objects then "print the client's name" and "walk the session into another org's reports"
would be the same expression. The rule, and the reason for it, are docs/INVOICING.md's.

**Formatting is a property of the document.** Numbers and dates resolve in the *report's*
locale, not the viewer's: a German client's report prints German whoever opens it.

**Charts are inline SVG, computed here.** They cannot be images: the engine's URL fetcher
answers ``data:`` and raises on everything else. That is also why the tenant's accent reaches
the charts through :class:`~app.core.documents.ChartStyle` rather than any of them owning a hex.
"""

from __future__ import annotations

from typing import Any

from markupsafe import Markup

from app.core.documents import (
    ChartStyle,
    accent_for,
    column_chart,
    grouped_columns,
    share_bar,
)
from app.i18n import translate

#: Metrics a document renders as a percentage rather than a count.
_PERCENT_METRICS = {"engagementRate", "ctr", "share", "delta", "link_percent", "mention_percent"}
#: Metrics that are a duration in seconds.
_DURATION_METRICS = {"userEngagementDuration"}
#: Metrics where a *lower* number is the better one, so a fall reads as good.
_LOWER_IS_BETTER = {"position", "avg_position"}


def fmt_number(value: Any, locale: str) -> str:
    """A count in the document's own convention — ``1.234`` in Dutch, ``1,234`` in English."""
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return ""
    whole = f"{int(round(number)):,}"
    return whole.replace(",", ".") if (locale or "nl").startswith("nl") else whole


def fmt_decimal(value: Any, locale: str, places: int = 1) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return ""
    text = f"{number:,.{places}f}"
    if (locale or "nl").startswith("nl"):
        text = text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return text


def fmt_duration(seconds: Any) -> str:
    try:
        total = int(float(seconds or 0))
    except (TypeError, ValueError):
        return ""
    return f"{total // 60:02d}:{total % 60:02d}"


def fmt_metric(key: str, value: Any, locale: str) -> str:
    if value is None or value == "":
        return "-"
    if key in _DURATION_METRICS:
        return fmt_duration(value)
    if key in _PERCENT_METRICS:
        number = float(value or 0)
        # An engagement rate arrives as a fraction; a share and a delta already as percentages.
        if key in ("engagementRate", "ctr") and number <= 1:
            number *= 100
        return f"{fmt_decimal(number, locale)}%"
    if key in _LOWER_IS_BETTER:
        return fmt_decimal(value, locale)
    return fmt_number(value, locale)


def delta_class(key: str, value: Any) -> str:
    """Whether a change reads as good, bad or neutral — direction is not always up."""
    if value is None:
        return "neutral"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "neutral"
    if number == 0:
        return "neutral"
    good = number < 0 if key in _LOWER_IS_BETTER else number > 0
    return "up" if good else "down"


def fmt_delta(value: Any, locale: str) -> str:
    """``+12,4%`` / ``-3,0%`` / ``-`` when there is nothing to compare against."""
    if value is None:
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    sign = "+" if number > 0 else ""
    return f"{sign}{fmt_decimal(number, locale)}%"


def build_context(
    *,
    report: Any,
    snapshot: dict[str, Any],
    narrative: dict[str, str],
    section_titles: dict[str, str],
    brand_name: str,
    logo_uri: str | None,
    cover_uri: str | None,
    client_logo_uri: str | None,
    accent: str | None,
    intro_text: str | None,
    footer_text: str | None,
    locale: str,
    internal: bool,
) -> dict[str, Any]:
    accent_hex = accent_for(accent, None)
    style = ChartStyle(accent=accent_hex)
    sections = []
    for key in snapshot.get("order") or []:
        data = (snapshot.get("sections") or {}).get(key)
        if not data:
            continue
        sections.append(
            {
                "key": key,
                "title": section_titles.get(key, key),
                "kind": data.get("kind") or "table",
                "columns": [
                    {"key": column, "label": translate(f"marketing.metric.{column}", locale)}
                    for column in data.get("columns") or []
                ],
                "rows": data.get("rows") or [],
                "groups": data.get("groups") or [],
                # Resolved to labelled tiles here rather than in the template: a design should
                # never have to know that "keyEvents" is called something else on screen.
                "totals": [
                    {
                        "key": metric,
                        "label": translate(f"marketing.metric.{metric}", locale),
                        "value": fmt_metric(metric, value, locale),
                        "delta": fmt_delta(
                            _pct(value, (data.get("compare") or {}).get(metric)), locale
                        ),
                        "delta_class": delta_class(
                            metric, _pct(value, (data.get("compare") or {}).get(metric))
                        ),
                    }
                    for metric, value in (data.get("totals") or {}).items()
                ],
                "compare": data.get("compare"),
                "narrative": (narrative.get(key) or "").strip(),
                "chart": _chart(data.get("chart"), style, locale),
                "audited_at": data.get("audited_at"),
            }
        )
    compare = snapshot.get("compare") or {}
    return {
        "locale": locale,
        "internal": internal,
        "brand_name": brand_name,
        "logo": logo_uri,
        "cover": cover_uri,
        "client_logo": client_logo_uri,
        "accent": accent_hex,
        "title": report.title,
        "client": (snapshot.get("company") or {}).get("name") or report.company_name,
        "period_label": (snapshot.get("period") or {}).get("label") or "",
        "compare_label": compare.get("label"),
        "intro_text": intro_text,
        "footer_text": footer_text,
        "summary": (narrative.get("summary") or "").strip(),
        "actions": _lines(narrative.get("actions")),
        "questions": _lines(narrative.get("questions")),
        "sections": sections,
        "labels": {
            key: translate(f"reporting.doc.{key}", locale)
            for key in (
                "summary", "period", "compared_with", "actions", "questions",
                "internal_banner", "generated", "no_data", "keyword", "landing_page",
                "start_position", "end_position", "score", "errors", "warnings", "pages",
                "audited_at", "source", "engine", "new_keyword",
            )
        },
        # Helpers a tenant's own template gets too, so "print this number properly" does not
        # require them to reimplement Dutch thousands separators in Jinja.
        "fmt": lambda key, value: fmt_metric(key, value, locale),
        "fmt_number": lambda value: fmt_number(value, locale),
        "fmt_delta": lambda value: fmt_delta(value, locale),
        "delta_class": delta_class,
    }


def _pct(current: Any, previous: Any) -> float | None:
    """The change between two period totals, or ``None`` when there is nothing to compare."""
    try:
        now, before = float(current or 0), float(previous)
    except (TypeError, ValueError):
        return None
    if not before:
        return None
    return round(((now - before) / before) * 100, 1)


def _lines(value: str | None) -> list[str]:
    return [line.strip(" -•\t") for line in (value or "").splitlines() if line.strip()]


def _chart(spec: dict[str, Any] | None, style: ChartStyle, locale: str) -> str:
    """A chart spec from a section provider, turned into SVG the document can carry.

    Returned as ``Markup``: the environment autoescapes, and an escaped ``<svg>`` prints as
    literal angle brackets in the middle of a client's report. It is safe to mark because it
    is *our* output — every label that came from tenant or Google data went through
    ``charts._esc`` on the way in. Marking it here rather than asking each template for
    ``| safe`` also means a tenant's own design gets a working chart without knowing the rule.
    """
    if not spec:
        return ""
    kind = spec.get("type")
    if kind == "grouped":
        series = spec.get("series") or []
        labelled = [
            (translate(f"reporting.doc.series_{item.get('key')}", locale), item.get("values") or [])
            for item in series
        ]
        if len(labelled) == 1 or not any(any(values) for _, values in labelled[1:]):
            # No comparison worth drawing — one series, one colour, and no legend restating
            # the title.
            return Markup(
                column_chart(
                    spec.get("labels") or [], labelled[0][1] if labelled else [], style=style
                )
            )
        return Markup(grouped_columns(spec.get("labels") or [], labelled, style=style))
    if kind == "share":
        items = [
            (item.get("label", ""), float(item.get("value") or 0))
            for item in spec.get("items") or []
        ]
        return Markup(
            share_bar(items, style=style, other_label=translate("reporting.doc.other", locale))
        )
    return ""
