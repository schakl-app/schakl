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
    share_palette,
)
from app.i18n import translate

#: Metrics a document renders as a percentage rather than a count. ``delta`` is not among them:
#: a change has its own renderer (:func:`fmt_delta`, signed, and a multiplier past the point a
#: percentage stops meaning anything), and having two answers to "print this delta" is how the
#: table came to say ``+47,8%`` where the paragraph beside it said ``47,8%``.
_PERCENT_METRICS = {"engagementRate", "ctr", "share", "link_percent", "mention_percent"}
#: Metrics that are a duration in seconds.
_DURATION_METRICS = {"userEngagementDuration", "avg_engagement_time"}
#: Metrics that are an amount of money — printed in the *account's* currency, which a GA4
#: property reports as its own ``currencyCode`` and which a Dutch agency's client does not
#: always share (#124: label it, never convert it).
_CURRENCY_METRICS = {"totalRevenue", "conversionsValue", "cost"}
#: Metrics that are a percentage *change* rather than a measurement. Not ``change``, which is a
#: rankings section's move in positions — three places, not three percent.
_DELTA_METRICS = {"delta"}
#: Metrics where a *lower* number is the better one, so a fall reads as good.
_LOWER_IS_BETTER = {"position", "avg_position"}

#: Past this, a percentage has stopped being a comparison and become an artefact of a tiny
#: denominator. "+91.300,0%" is one session last July against 914 this July: arithmetically
#: correct, and it tells a reader nothing except that a number got big. Above the threshold the
#: same fact is stated as a multiplier, which is what a person would say out loud.
_DELTA_AS_FACTOR = 1000.0


def metric_label(key: str, locale: str) -> str:
    """A metric's name in the document's language: the measurement catalogue, then this
    document's own vocabulary, then — only then — the metric's own key.

    ``translate`` answers a missing key with **the key**, so a source that starts returning a
    column nobody has catalogued yet would head a column on a client's report
    ``marketing.metric.bounceRate``. A raw camelCase name is a poor heading; a message key is a
    bug report printed on somebody's desk.

    The second lookup is not a nicety. A site audit's totals are ``score`` / ``errors`` /
    ``warnings``, which `marketing.metric.*` has never held and ``reporting.doc.*`` has held all
    along — so the fallback alone printed SCORE / ERRORS / WARNINGS beside PAGINA'S on a Dutch
    internal report, and handed the model the same English identifiers to write prose around.
    """
    for namespace in ("marketing.metric", "reporting.doc"):
        candidate = f"{namespace}.{key}"
        label = translate(candidate, locale)
        if label != candidate:
            return label
    return key


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
    """``04:12`` under an hour, ``10:26:10`` over one.

    Minutes-and-seconds alone is only unambiguous while the number is small, and a *total*
    engagement time for a month never is: 37.570 seconds printed as ``626:10`` reads as ten
    minutes to anyone who does not stop to count the digits, and as nothing at all to everyone
    else. The hour field appears when there is an hour to show and not before, so a per-session
    average keeps the short form it deserves.
    """
    try:
        total = int(float(seconds or 0))
    except (TypeError, ValueError):
        return ""
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


#: The symbol a code is written as. Anything else prints its ISO code, which is a label rather
#: than a guess — a report that invents "€" over Australian dollars states a wrong number.
_CURRENCY_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£"}


def fmt_currency(value: Any, locale: str, currency: str | None = None) -> str:
    """An amount in the **account's** currency, not in the one we happen to work in.

    A Google Analytics property reports its own ``currencyCode`` and a Dutch agency does have
    clients selling in dollars (docs/MARKETING.md's rule, #124: label it, never convert it).
    ``None`` falls back to the instance currency, which CLAUDE.md §8 pins to EUR.
    """
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return ""
    code = (currency or "EUR").upper()
    unit = _CURRENCY_SYMBOLS.get(code, code)
    return f"{unit} {fmt_number(number, locale)}"


def fmt_metric(key: str, value: Any, locale: str, currency: str | None = None) -> str:
    if value is None or value == "":
        return "-"
    if key in _DELTA_METRICS:
        return fmt_delta(value, locale)
    if key in _DURATION_METRICS:
        return fmt_duration(value)
    if key in _CURRENCY_METRICS:
        return fmt_currency(value, locale, currency)
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
    """``+12,4%`` / ``-3,0%`` / ``×914`` / ``-`` when there is nothing to compare against."""
    if value is None:
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    if number >= _DELTA_AS_FACTOR:
        return f"×{fmt_number(1 + number / 100, locale)}"
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
        data = localise_section(data, locale)
        colors = _row_colors(data, style, locale)
        currency = data.get("currency")
        sections.append(
            {
                "key": key,
                "title": section_titles.get(key, key),
                "kind": data.get("kind") or "table",
                "columns": [
                    {"key": column, "label": metric_label(column, locale)}
                    for column in data.get("columns") or []
                ],
                "rows": _coloured(data.get("rows") or [], colors),
                # Whether this section's *column* carries the mark. A design needs it as well
                # as the per-row colour, because the rows past the chart's segment cap have no
                # segment and must still line up with the ones that do.
                "dots": bool(colors),
                "groups": data.get("groups") or [],
                # Resolved to labelled tiles here rather than in the template: a design should
                # never have to know that "keyEvents" is called something else on screen.
                #
                # A metric that is zero now and was zero then is dropped, on the same argument
                # that stops an empty section printing: a client who sells nothing online got
                # an "OMZET 0" tile every month for ever, which is not a fact about their July.
                # A zero that follows a non-zero is a real and often unwelcome fact, and stays.
                "totals": [
                    {
                        "key": metric,
                        "label": metric_label(metric, locale),
                        "value": fmt_metric(metric, value, locale, currency),
                        "delta": fmt_delta(
                            _pct(value, (data.get("compare") or {}).get(metric)), locale
                        ),
                        "delta_class": delta_class(
                            metric, _pct(value, (data.get("compare") or {}).get(metric))
                        ),
                    }
                    for metric, value in (data.get("totals") or {}).items()
                    if not always_zero(value, (data.get("compare") or {}).get(metric))
                ],
                "compare": data.get("compare"),
                "narrative": (narrative.get(key) or "").strip(),
                "chart": _chart(data.get("chart"), style, locale),
                # How many things the chart draws, so a design can decide whether it will fit
                # beside a table without parsing the SVG it was handed.
                "chart_categories": _chart_categories(data.get("chart")),
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
        "fmt_currency": lambda value, code=None: fmt_currency(value, locale, code),
        "fmt_number": lambda value: fmt_number(value, locale),
        "fmt_delta": lambda value: fmt_delta(value, locale),
        "delta_class": delta_class,
    }


def _chart_categories(spec: dict[str, Any] | None) -> int:
    if not spec:
        return 0
    return len(spec.get("labels") or spec.get("items") or [])


def always_zero(current: Any, previous: Any) -> bool:
    """Zero now **and** zero then — where "then" was actually measured.

    ``previous is None`` means no comparison happened at all, which is a different fact from a
    comparison that came back zero, and conflating them drops a tile that is this period's own
    news. The site audit is the case that proves it: it never carries a comparison, so treating
    its ``None`` as zero deleted *Fouten 0* and *Waarschuwingen 0* from a clean site's internal
    report — the good news, missing from a document whose whole job is listing faults, with
    nothing on the page to say a tile had been withheld.
    """
    if previous is None:
        return False
    try:
        return float(current or 0) == 0 and float(previous) == 0
    except (TypeError, ValueError):
        return False


def channel_label(value: Any, locale: str) -> str:
    """One GA4 channel group, translated — or exactly what Google called it."""
    raw = str(value or "")
    if not raw:
        return raw
    key = f"marketing.channel.{raw.lower().replace('-', '_').replace(' ', '_')}"
    label = translate(key, locale)
    return raw if label == key else label


def localise_section(data: dict[str, Any], locale: str) -> dict[str, Any]:
    """Google's own vocabulary, in the document's language.

    ``sessionDefaultChannelGroup`` is a **fixed list Google defines** — "Paid Social",
    "Cross-network", "Unassigned" — not tenant data and not a name anybody chose, and it was
    printing verbatim in the middle of a Dutch report: a row reading *Unassigned* beside one
    reading *Verwijzend verkeer*. It is catalogued (``marketing.channel.*``), and anything
    uncatalogued keeps Google's string, which is the only honest fallback — a channel group
    Google adds next year prints as itself rather than as a message key.

    Done **here** rather than where the section is gathered, deliberately: a report freezes its
    rows, so translating at the renderer also fixes the ones already stored and keeps the
    snapshot a record of what Google actually said. It runs before the colours are computed
    because both the share palette and the chart key on the label, and a half-translated
    section would put every dot one row off the name it belongs to.
    """
    if (data.get("kind") or "") != "channels":
        return data
    rows = [
        {**row, "label": channel_label(row.get("label"), locale)}
        if isinstance(row, dict)
        else row
        for row in data.get("rows") or []
    ]
    out = {**data, "rows": rows}
    chart = data.get("chart")
    if isinstance(chart, dict) and chart.get("labels"):
        out["chart"] = {
            **chart,
            "labels": [channel_label(label, locale) for label in chart["labels"]],
        }
    return out


def _row_colors(
    data: dict[str, Any], style: ChartStyle, locale: str
) -> dict[str, str]:
    """``{row label: hex}`` for the sections whose rows are shares of one whole.

    Two sections qualify, for the same reason and by the same scale.

    A section drawn as a **share bar** hands its reader six colours in a legend and then a
    table that repeats none of them, so "which row is the dark one" is answered by counting.
    The dot closes that: it is the row's own segment, taken from :func:`share_palette` — the
    function the bar itself draws from — and the ``other_label`` is passed through because the
    folded tail is part of the *scale*, not decoration. Get that wrong and every dot is one
    step off the segment it names, which is worse than no dot at all.

    **Traffic by channel** has no share bar (its chart compares this period with last), but it
    does carry a ``share`` column, and the dot is that column drawn rather than read. That is
    the one thing this may be: a second encoding of a number already on the row. It is
    deliberately *not* offered to a section whose rows are not parts of a whole — a referral
    table's rows do not sum to anything, so a tint by rank there would be decoration wearing a
    data mark's clothes.

    Rows past the bar's segment cap get no colour rather than the tail's: the tail stands for
    all of them at once, and handing four rows one colour reads as four rows that belong
    together.
    """
    spec = data.get("chart") or {}
    if spec.get("type") == "share":
        items = [
            (str(item.get("label") or ""), float(item.get("value") or 0))
            for item in spec.get("items") or []
        ]
    elif (data.get("kind") or "") == "channels":
        items = [
            (str(row.get("label") or ""), float(row.get("sessions") or 0))
            for row in data.get("rows") or []
        ]
    else:
        return {}
    return {
        segment.label: segment.colour
        for segment in share_palette(
            items, style=style, other_label=translate("reporting.doc.other", locale)
        )
        if not segment.tail
    }


def _coloured(rows: list[dict[str, Any]], colors: dict[str, str]) -> list[dict[str, Any]]:
    if not colors:
        return rows
    return [
        {**row, "color": colors[row["label"]]}
        if isinstance(row, dict) and row.get("label") in colors
        else row
        for row in rows
    ]


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
