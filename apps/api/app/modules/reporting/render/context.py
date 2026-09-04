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

from markupsafe import Markup, escape

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

#: The order a strip of figures is read in. **Not cosmetic: a snapshot has no order.**
#:
#: ``data_snapshot`` is a ``JSONB`` column, and Postgres does not store an object's keys in the
#: order they were written — it sorts them by length, then bytewise. A section provider builds
#: its totals in the source's own display order (``sessions``, ``totalUsers``, ``newUsers``,
#: ``keyEvents`` …) and that order survives exactly as far as the first commit. What comes back
#: out, and what every report printed, is
#:
#:     NIEUWE GEBRUIKERS 2.810   SESSIES 4.124   BELANGRIJKE GEBEURTENISSEN 879   GEBRUIKERS 3.781
#:
#: — an ordering with no meaning at all, on the strip that is the first thing a client reads.
#: Invisible in every test that asserts on values, and invisible in the offline renderer too,
#: because a Python dict *does* keep insertion order: only a document read back from the
#: database shows it.
#:
#: So the document states the order itself. A metric not named here keeps its relative position
#: after the ones that are, which is what stops a section a later release adds from vanishing
#: to the front or the back of somebody's strip.
_TILE_ORDER = (
    "sessions", "totalUsers", "newUsers", "keyEvents", "conversions",
    "engagementRate", "avg_engagement_time", "userEngagementDuration", "screenPageViews",
    "totalRevenue", "cost", "conversionsValue",
    "clicks", "impressions", "ctr", "position",
    "avg_position", "top3", "top10", "top30", "keywords_ranking", "keywords_tracked",
    "score", "errors", "warnings", "pages",
)

#: Past this, a percentage has stopped being a comparison and become an artefact of a tiny
#: denominator. "+91.300,0%" is one session last July against 914 this July: arithmetically
#: correct, and it tells a reader nothing except that a number got big. Above the threshold the
#: same fact is stated as a multiplier, which is what a person would say out loud.
_DELTA_AS_FACTOR = 1000.0

#: The section kinds whose rows are "one row per source we happened to see" — an open-ended
#: list nobody chose, as opposed to a closed vocabulary (``channels``: Google's twelve) or the
#: client's own goals (``conversions``). Only these get their long tail folded and their
#: columns narrowed for a client, because only these have a tail worth folding.
_SOURCE_KINDS = {"organic_sources", "social_sources", "referral_sources"}

#: What a section's *name* column is called, by kind. Anything unlisted is a source, which is
#: what every table on the document was before there was one whose rows are search engines —
#: including ``ai_search``, whose column is arguably misnamed too and is not this change's to
#: rename: a heading a client has read for three months is not collateral on a different fix.
_NAME_LABELS = {"engines": "search_engine"}

#: Columns a **client** document drops from a traffic-split table. The provider returns the
#: marketeer's seven (`report_sections._split_section`), which is the right answer for the
#: internal analysis and a data dump on a client's desk: nobody reading a monthly report needs
#: `totalUsers` *and* `newUsers` per referring domain, and three of the seven columns are
#: routinely wider than the name they describe. What survives is the four a client reads —
#: sessions, users, how long they stayed, what they did — and the full set is one click away in
#: the dashboard the same numbers come from.
_CLIENT_DROPS_COLUMNS = {"newUsers", "screenPageViews", "engagementRate"}

#: However aggressive the floor, a folded table never shrinks below this. It is a guard against
#: the degenerate case — a client whose traffic is so evenly spread that everything is tail —
#: and **not** a positional guarantee: a rank-5 row with one session is folded like every other
#: one-session row, because "we show at least five" is a rule about the table being a table, not
#: a licence to print one arbitrary piece of noise above the line that says there is noise.
_MIN_KEPT_ROWS = 3
#: …and at most this many. Past a dozen a reader has stopped reading rows and started skimming
#: for the one they came for, which is what the folded line and the section's paragraph are for.
_MAX_KEPT_ROWS = 12
#: The share of a table's own total below which a row is tail rather than information. A source
#: that carried half a percent of the month's traffic tells a client nothing they can act on,
#: and thirteen such rows bury the four that do. The floor is never below 2 of whatever the
#: primary metric counts, so a small client's real sources are never folded away.
_TAIL_SHARE = 0.005
_TAIL_FLOOR = 2.0


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


def metric_short(key: str, locale: str) -> str:
    """A metric's name **as a printed column heading** — the short form where one exists.

    The full name is right on a KPI tile, which is a box with a line to itself. It is wrong on a
    table head, and not for taste: an auto-layout table allocates width by *content demand*, and
    the loudest demand on a report's traffic table is ``BELANGRIJKE GEBEURTENISSEN`` — one
    unbreakable phrase at 7pt with letter-spacing — competing with cells holding two digits. The
    heading won every time, so the name column was squeezed to its stated 26 % and every
    hostname broke mid-token (``duckduc/kgo``, ``mail.googl/e.com``) while the widest column on
    the sheet held sixteen zeros.

    A stated layout (:func:`column_widths`) is what actually fixes the allocation; this is what
    makes the stated width *enough*. ``DOELEN`` fits where ``BELANGRIJKE GEBEURTENISSEN`` never
    could, and the long name still heads the tile, so nothing is hidden — it is said once, in
    the place with room for it.

    Falls back to :func:`metric_label`, so a metric a later release adds keeps working and reads
    as its full name until somebody decides it needs a short one.
    """
    candidate = f"marketing.metric.short.{key}"
    label = translate(candidate, locale)
    return metric_label(key, locale) if label == candidate else label


#: One 16×16 stroke glyph per metric, so a seven-column table is **scanned** rather than read.
#: A column heading in a printed report is two words the eye has to parse on every table; a mark
#: it has already learned on the first one is free from then on. Drawn from the same geometry as
#: the app's own icon set so the document and the screen agree about what a "session" looks like.
#:
#: Inline SVG for the reason every other chart here is (``engine.no_network_fetcher`` answers
#: ``data:`` and raises on everything else), and stroked in ``currentColor`` so the mark inherits
#: whatever the head is painted in rather than carrying a hex of its own (Golden Rule 4).
#:
#: A metric with no glyph simply has none: an invented mark is worse than a bare heading,
#: because a reader will try to learn it.
_METRIC_ICONS: dict[str, str] = {
    "sessions": "M1 8h3.2l2.1-4.6 3 9.2 2.1-4.6H15",
    "compare_sessions": "M1 8h3.2l2.1-4.6 3 9.2 2.1-4.6H15",
    "totalUsers": (
        "M6 8a2.6 2.6 0 1 0 0-5.2 2.6 2.6 0 0 0 0 5.2M1.6 14c0-2.4 2-3.6 4.4-3.6"
        "s4.4 1.2 4.4 3.6M11.6 10.6c1.7.3 2.8 1.4 2.8 3.4"
    ),
    "newUsers": (
        "M6 8a2.6 2.6 0 1 0 0-5.2 2.6 2.6 0 0 0 0 5.2M1.6 14c0-2.4 2-3.6 4.4-3.6"
        "s4.4 1.2 4.4 3.6M12.6 6v4.4M10.4 8.2h4.4"
    ),
    "screenPageViews": "M3.4 2h6l3.2 3.2V14H3.4zM9.4 2v3.4h3.2",
    "pages": "M3.4 2h6l3.2 3.2V14H3.4zM9.4 2v3.4h3.2",
    "avg_engagement_time": "M8 14.4A6.4 6.4 0 1 0 8 1.6a6.4 6.4 0 0 0 0 12.8M8 4.6V8l2.4 1.6",
    "userEngagementDuration": (
        "M8 14.4A6.4 6.4 0 1 0 8 1.6a6.4 6.4 0 0 0 0 12.8M8 4.6V8l2.4 1.6"
    ),
    "engagementRate": (
        "M3.6 4.4a1.4 1.4 0 1 0 0-2.8 1.4 1.4 0 0 0 0 2.8"
        "M12.4 14.4a1.4 1.4 0 1 0 0-2.8 1.4 1.4 0 0 0 0 2.8M13 3L3 13"
    ),
    "ctr": (
        "M3.6 4.4a1.4 1.4 0 1 0 0-2.8 1.4 1.4 0 0 0 0 2.8"
        "M12.4 14.4a1.4 1.4 0 1 0 0-2.8 1.4 1.4 0 0 0 0 2.8M13 3L3 13"
    ),
    "keyEvents": "M8 14.4A6.4 6.4 0 1 0 8 1.6a6.4 6.4 0 0 0 0 12.8M5.2 8l2 2 3.6-3.8",
    "compare_keyEvents": "M8 14.4A6.4 6.4 0 1 0 8 1.6a6.4 6.4 0 0 0 0 12.8M5.2 8l2 2 3.6-3.8",
    "conversions": "M8 14.4A6.4 6.4 0 1 0 8 1.6a6.4 6.4 0 0 0 0 12.8M5.2 8l2 2 3.6-3.8",
    "clicks": "M3 2l4.6 12 1.9-4.9 4.9-1.9z",
    "impressions": (
        "M1 8s2.6-4.4 7-4.4S15 8 15 8s-2.6 4.4-7 4.4S1 8 1 8"
        "M8 9.9A1.9 1.9 0 1 0 8 6.1a1.9 1.9 0 0 0 0 3.8"
    ),
    "position": (
        "M8 14.4s5.2-4.3 5.2-7.7A5.2 5.2 0 1 0 2.8 6.7c0 3.4 5.2 7.7 5.2 7.7"
        "M8 8.6a1.9 1.9 0 1 0 0-3.8 1.9 1.9 0 0 0 0 3.8"
    ),
    "avg_position": (
        "M8 14.4s5.2-4.3 5.2-7.7A5.2 5.2 0 1 0 2.8 6.7c0 3.4 5.2 7.7 5.2 7.7"
        "M8 8.6a1.9 1.9 0 1 0 0-3.8 1.9 1.9 0 0 0 0 3.8"
    ),
    "share": "M8 2.6a5.4 5.4 0 1 0 5.4 5.4H8z",
    "delta": "M2 11.6l4.4-4.4 2.8 2.8L14 4.6M10.6 4.6H14v3.4",
    "change": "M2 11.6l4.4-4.4 2.8 2.8L14 4.6M10.6 4.6H14v3.4",
    "totalRevenue": "M12.4 3.6a5 5 0 1 0 0 8.8M2.6 6.6h6M2.6 9.4h6",
    "cost": "M12.4 3.6a5 5 0 1 0 0 8.8M2.6 6.6h6M2.6 9.4h6",
    "conversionsValue": "M12.4 3.6a5 5 0 1 0 0 8.8M2.6 6.6h6M2.6 9.4h6",
    "top3": "M8 1.8l1.9 3.9 4.3.6-3.1 3 .7 4.2L8 11.6 4.2 13.5l.7-4.2-3.1-3 4.3-.6z",
    "top10": "M8 1.8l1.9 3.9 4.3.6-3.1 3 .7 4.2L8 11.6 4.2 13.5l.7-4.2-3.1-3 4.3-.6z",
    "top30": "M8 1.8l1.9 3.9 4.3.6-3.1 3 .7 4.2L8 11.6 4.2 13.5l.7-4.2-3.1-3 4.3-.6z",
    "keyword": "M7.2 12.4a5.2 5.2 0 1 0 0-10.4 5.2 5.2 0 0 0 0 10.4M14.4 14.4l-3.5-3.5",
    "keywords_tracked": "M7.2 12.4a5.2 5.2 0 1 0 0-10.4 5.2 5.2 0 0 0 0 10.4M14.4 14.4l-3.5-3.5",
    "keywords_ranking": "M7.2 12.4a5.2 5.2 0 1 0 0-10.4 5.2 5.2 0 0 0 0 10.4M14.4 14.4l-3.5-3.5",
    "landing_page": (
        "M6.6 9.4a3 3 0 0 0 4.4.3l1.9-1.9a3 3 0 0 0-4.2-4.2l-1 1"
        "M9.4 6.6a3 3 0 0 0-4.4-.3L3.1 8.2a3 3 0 0 0 4.2 4.2l1-1"
    ),
    "begin": "M4 14V2M4 2.8h8.4L10.6 6l1.8 3.2H4",
    "end": "M12 14V2M12 2.8H3.6L5.4 6 3.6 9.2H12",
}


def metric_icon(key: str) -> Markup:
    """The metric's glyph as inline SVG, or empty — never a placeholder."""
    path = _METRIC_ICONS.get(key)
    if not path:
        return Markup("")
    return Markup(
        '<svg class="mi" viewBox="0 0 16 16" fill="none" stroke="currentColor" '
        'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        f'<path d="{path}"/></svg>'
    )


#: How much wider a column that carries its own change is than a plain one. A period total is
#: four or five tabular digits; beside it sit an arrow and a signed percentage at 7pt, which is
#: about as much again — so the host column is not two columns' worth, but it is not one either.
_HOST_WIDTH_FACTOR = 1.8


def column_widths(count: int, hosting: int = 0) -> tuple[float, float, float]:
    """``(name column %, each metric column %, each change-hosting column %)`` for a
    fixed-layout table.

    A sheet of A4 has a width and the *table* decides how to spend it, not the widest word in
    a heading. Each metric column gets an equal, generous-enough share — a period total is four
    or five tabular digits and a duration is five characters, so ~9 % of the text column at 8pt
    is room to spare — and the name column takes everything left over, which is where the
    hostnames and the event names actually live.

    ``hosting`` is how many of the ``count`` columns carry a change beside their number (see
    :func:`attach_changes`). Such a column holds ``4.124 ▲ +26,5%`` and gets
    :data:`_HOST_WIDTH_FACTOR` times a plain column's share, paid for out of the same budget —
    so a table that folded its VERSCHIL column into its SESSIES column spends about what it
    spent before, and the name column keeps what it had.

    Capped at 14 % so a two-column table does not draw two enormous number columns, and the
    name column is floored so seven metrics cannot starve it back to where this started.
    """
    if count <= 0:
        return 100.0, 0.0, 0.0
    hosting = max(0, min(hosting, count))
    units = (count - hosting) + hosting * _HOST_WIDTH_FACTOR
    per = min(14.0, 66.0 / units)
    host = per * _HOST_WIDTH_FACTOR
    name = max(24.0, 100.0 - per * (count - hosting) - host * hosting)
    return round(name, 2), round(per, 2), round(host, 2)


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


#: A change column, and the column whose number it describes — in the order the host is
#: looked for. ``delta`` is a percentage against the row's own ``compare_<metric>``, so its host
#: is whichever metric has that twin beside it (``sessions`` on a channel table, ``keyEvents``
#: on a conversions one). ``change`` is a move in *places*: on the per-engine table it is the
#: average position's, on the rankings table the end position's.
_CHANGE_HOSTS: dict[str, tuple[str, ...]] = {
    "delta": ("sessions", "keyEvents", "totalUsers", "clicks", "impressions"),
    "change": ("avg_position", "position", "end"),
}

#: The two triangles a change is drawn with — solid, 16×16, filled in ``currentColor`` so they
#: take the badge's own colour. Glyphs rather than ``▲``/``▼``, because a document font is the
#: tenant's choice and not every one of them carries the geometric shapes block; an inline path
#: prints the same on every machine WeasyPrint runs on.
_ARROWS = {
    "up": "M8 3.2l5.2 8.4H2.8z",
    "down": "M8 12.8L2.8 4.4h10.4z",
}


def _arrow(direction: str) -> Markup:
    path = _ARROWS.get(direction)
    if not path:
        return Markup("")
    return Markup(
        f'<svg class="arrow" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">'
        f'<path d="{path}"/></svg>'
    )


def change_badge(key: str, value: Any, locale: str) -> Markup:
    """A change, drawn beside the number it is about: a coloured arrow and the signed figure.

    The **arrow** is the direction the number moved (up for a rise, down for a fall, none for
    no movement) and the **colour** is the verdict (:func:`delta_class`), and the two are
    deliberately separate signals: an average position that fell from 22 to 19 draws a *down*
    arrow in *green*, because the number went down and that is the good direction. Folding the
    two into one glyph would make every lower-is-better metric read backwards.

    ``delta`` is a percentage and prints through :func:`fmt_delta`; ``change`` is a move in
    places and prints as a signed count. Nothing to compare against (``None``, or not a number)
    draws nothing at all — a dash beside a number is a question, and the compare column already
    holds the answer.
    """
    if value is None or value == "":
        return Markup("")
    try:
        number = float(value)
    except (TypeError, ValueError):
        return Markup("")
    if key in _DELTA_METRICS:
        text = fmt_delta(number, locale)
    else:
        # A whole number of places prints whole; an engine's average moves by halves and
        # tenths, and rounding `-1,5` to `-2` would overstate a move the tile beside it states
        # exactly.
        figure = (
            fmt_number(number, locale) if number.is_integer() else fmt_decimal(number, locale)
        )
        text = f"{'+' if number > 0 else ''}{figure}"
    direction = "up" if number > 0 else ("down" if number < 0 else "")
    return Markup(
        f'<span class="badge {delta_class(key, number)}">{_arrow(direction)}{escape(text)}</span>'
    )


def attach_changes(data: dict[str, Any]) -> dict[str, Any]:
    """Fold a table's change column into the column whose number it describes.

    A row used to read ``SESSIES 1.240 · VORIG JAAR 980 · VERSCHIL +26,5%``: three cells for two
    facts, with the one a reader actually wants — *did it go up* — in the narrowest column at
    the far end, away from the number it qualifies. The change now rides the number's own cell,
    as an arrow and a signed figure (:func:`change_badge`), exactly as the KPI tile above the
    table has always drawn its own.

    ``columns`` loses the change key and ``changes`` records where it went, ``{host: change}``,
    so a design can ask each column whether it carries one. The **rows** are untouched: the
    stored snapshot stays the record of what the source said, and the model's copy of the table
    (``present.section``) still names the change in words — a paragraph that says "a quarter
    up on last year" needs the figure whichever cell the page draws it in.

    Applied at the renderer for the reason every narrowing here is: a report freezes its rows,
    so reports already generated print the new layout too.
    """
    columns = [key for key in data.get("columns") or [] if isinstance(key, str)]
    changes: dict[str, str] = {}
    for change, hosts in _CHANGE_HOSTS.items():
        if change not in columns:
            continue
        host = next((key for key in hosts if key in columns and key not in changes), None)
        if host is None:
            continue
        changes[host] = change
        columns.remove(change)
    if not changes:
        return data
    return {**data, "columns": columns, "changes": changes}


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
        kind = data.get("kind") or "table"
        # One block per website, or one block full stop (#381). A payload written before parts
        # existed — every report already in the database — has no ``parts``, and is its own
        # single block: a stored report must render next December exactly as it rendered today,
        # which is the whole reason `data_snapshot` is frozen in the first place.
        raw_parts = [
            part for part in (data.get("parts") or [data]) if isinstance(part, dict)
        ] or [data]
        parts = [
            _shaped_part({**part, "kind": kind}, style, locale, internal=internal)
            for part in raw_parts
        ]
        sections.append(
            {
                "key": key,
                "title": section_titles.get(key, key),
                "kind": kind,
                # What the *name* column holds. It was hardcoded to "Bron", which is right for
                # a table of referring domains and wrong for one of search engines — and the
                # `reporting.doc.engine` label existed for exactly this and was never wired to
                # anything. Decided from the kind, so a design never has to know which sections
                # count as sources (#381).
                "name_label": translate(
                    f"reporting.doc.{_NAME_LABELS.get(kind, 'source')}", locale
                ),
                # The blocks, and — flattened onto the section — the first of them. The flat
                # keys are what every design rendered before this existed, including a tenant's
                # own Jinja (docs/INVOICING.md), so they stay: a design that has never heard of
                # `parts` prints the first website rather than breaking.
                "parts": parts,
                **parts[0],
                "narrative": (narrative.get(key) or "").strip(),
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
        # The cover's own figures — see `_headline`.
        "headline": _headline(sections),
        "labels": {
            key: translate(f"reporting.doc.{key}", locale)
            for key in (
                "summary", "period", "compared_with", "actions", "questions",
                "internal_banner", "generated", "no_data", "keyword", "landing_page",
                "start_position", "end_position", "score", "errors", "warnings", "pages",
                "audited_at", "source", "engine", "search_engine", "new_keyword",
                "at_a_glance", "move",
            )
        },
        # Helpers a tenant's own template gets too, so "print this number properly" does not
        # require them to reimplement Dutch thousands separators in Jinja.
        "fmt": lambda key, value: fmt_metric(key, value, locale),
        "fmt_currency": lambda value, code=None: fmt_currency(value, locale, code),
        "fmt_number": lambda value: fmt_number(value, locale),
        "fmt_delta": lambda value: fmt_delta(value, locale),
        "delta_class": delta_class,
        "change_badge": lambda key, value: change_badge(key, value, locale),
        "tile_rows": tile_rows,
        "icon": metric_icon,
    }


def _shaped_part(
    data: dict[str, Any], style: ChartStyle, locale: str, *, internal: bool
) -> dict[str, Any]:
    """One block of a section, ready to draw: its table, its tiles, its chart, its geometry.

    Everything here used to be inlined in :func:`build`, computed once per section. It is
    computed once per *block* now, which is the whole of what "report per website" costs the
    renderer — a client with two GA4 properties gets two tables with their own column widths,
    their own folded tails and their own charts, rather than two websites' numbers interleaved
    under one heading.

    ``label`` is the website's name, or empty. Empty is not a missing value: it is what a client
    with one property has, and what a deliberately combined report has, and in both cases a
    heading naming the property would be noise arguing with the figures under it.
    """
    data = shape_section(data, locale, internal=internal)
    colors = _row_colors(data, style, locale)
    currency = data.get("currency")
    keys = list(data.get("columns") or [])
    changes: dict[str, str] = dict(data.get("changes") or {})
    name_width, metric_width, host_width = column_widths(len(keys), len(changes))
    return {
        "label": str(data.get("label") or ""),
        "columns": [
            {
                "key": column,
                "label": metric_short(column, locale),
                # The full name travels too: a design that has the room (a tile, a legend, a
                # title attribute on the preview) should never have to guess what the short one
                # stands for.
                "full_label": metric_label(column, locale),
                "icon": metric_icon(column),
                # The change this column carries beside its number, or none — the key the row
                # holds it under, so a design draws `change_badge(column.change, row[…])` and
                # never has to know that a channel's is `delta` and an engine's is `change`.
                "change": changes.get(column),
                "width": host_width if column in changes else metric_width,
            }
            for column in keys
        ],
        # The table's own geometry, decided here rather than negotiated by the widest heading at
        # layout time — see `column_widths`.
        "name_width": name_width,
        # How many columns carry a change beside their number. A design that decides whether a
        # chart fits *beside* a table counts these twice: the change was a column of its own
        # before it was folded in, and its width did not go away with the heading.
        "hosts": len(changes),
        "rows": _coloured(_ranked(data.get("rows") or []), colors),
        # Whether this block's *column* carries the mark. A design needs it as well as the
        # per-row colour, because the rows past the chart's segment cap have no segment and must
        # still line up with the ones that do.
        "dots": bool(colors),
        "groups": [
            {**group, "rows": _ranked(group.get("rows") or [])}
            for group in (data.get("groups") or [])
            if isinstance(group, dict)
        ],
        # Resolved to labelled tiles here rather than in the template: a design should never
        # have to know that "keyEvents" is called something else on screen.
        #
        # A metric that is zero now and was zero then is dropped, on the same argument that
        # stops an empty section printing: a client who sells nothing online got an "OMZET 0"
        # tile every month for ever, which is not a fact about their July. A zero that follows a
        # non-zero is a real and often unwelcome fact, and stays.
        "totals": _tiles(data, locale, currency),
        "compare": data.get("compare"),
        "currency": currency,
        # Whether the rankings table draws a landing-page column, and what that leaves for the
        # keyword. A Search Console-sourced table knows the query and not which page answered
        # it, and a column of dashes is worse than no column.
        "show_landing_page": _has_landing_pages(data),
        "rank_name_width": 39.0 if _has_landing_pages(data) else 65.0,
        "chart": _chart(data.get("chart"), style, locale),
        # How many things the chart draws, so a design can decide whether it will fit beside a
        # table without parsing the SVG it was handed.
        "chart_categories": _chart_categories(data.get("chart")),
    }


def ordered_metrics(totals: dict[str, Any]) -> list[tuple[str, Any]]:
    """``totals`` in the order a reader reads them, not in the order Postgres kept them.

    See :data:`_TILE_ORDER`. An unlisted metric sorts after every listed one and keeps its
    position relative to the other unlisted ones, so a metric a later release adds is appended
    rather than dropped into the middle of the strip.
    """
    index = {metric: position for position, metric in enumerate(_TILE_ORDER)}
    entries = list(enumerate(totals.items()))
    entries.sort(key=lambda item: (index.get(item[1][0], len(index)), item[0]))
    return [pair for _, pair in entries]


def _tiles(data: dict[str, Any], locale: str, currency: str | None) -> list[dict[str, Any]]:
    """One section's headline figures, resolved to labelled tiles.

    Resolved here rather than in the template: a design should never have to know that
    ``keyEvents`` is called something else on screen.

    A metric that is zero now **and** was zero then is dropped, on the same argument that stops
    an empty section printing: a client who sells nothing online got an "OMZET 0" tile every
    month for ever, which is not a fact about their July. A zero that follows a non-zero is a
    real and often unwelcome fact, and stays.

    And **a figure already on the strip is not printed a second time under another name.**
    ``GA4_METRICS`` holds both ``keyEvents`` and ``conversions``, and GA4 answers both with the
    same number for practically every property, so every report ever generated printed

        BELANGRIJKE GEBEURTENISSEN 879      CONVERSIES 879
        +48,7%                              +48,7%

    — two tiles, one fact, and a client left wondering what the difference is. The value *and*
    the change must match before a tile is dropped: two metrics that happen to be equal this
    month and moved differently are two facts, and the first one declared wins, which is the
    source's own display order (``sessions`` before ``totalUsers``, ``keyEvents`` before its
    ``conversions`` alias).
    """
    compare = data.get("compare") or {}
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for metric, value in ordered_metrics(data.get("totals") or {}):
        previous = compare.get(metric)
        if always_zero(value, previous):
            continue
        change = _pct(value, previous)
        tile = {
            "key": metric,
            "label": metric_label(metric, locale),
            "icon": metric_icon(metric),
            "value": fmt_metric(metric, value, locale, currency),
            "delta": fmt_delta(change, locale),
            "delta_class": delta_class(metric, change),
            # The drawn form — arrow and figure — so the tile and a table cell say a change
            # the same way. `delta` and `delta_class` stay for a tenant's own design that
            # reads them.
            "badge": change_badge("delta", change, locale),
        }
        fingerprint = (str(tile["value"]), str(tile["delta"]))
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        out.append(tile)
    return out


#: How many figures the cover strip carries. Four is a row that reads in one glance on a sheet
#: whose whole job is being the one glance; five wraps and stops being a strip.
_HEADLINE_TILES = 4

#: Most tiles in one row of the KPI strip.
_TILES_PER_ROW = 4


def tile_rows(tiles: list[dict[str, Any]]) -> list[list[dict[str, Any] | None]]:
    """A tile strip split into balanced rows, padded to a rectangle.

    The strip used to be a flex row at ``flex: 1 1 22%`` — three per line — so a section with
    four figures wrapped 3 + 1 and the fourth tile stretched across the whole page. A lone
    full-bleed *GEM. POSITIE* under three ordinary tiles reads as a rendering fault rather than
    as a layout, and Search Console's strip is exactly four.

    Balanced rather than greedy: six tiles are 3 + 3, not 4 + 2, and seven are 4 + 3. The last
    row is padded with ``None`` so every tile in the section is the same width — a fixed table
    layout divides by cells present, so an unpadded short row would silently draw wider boxes.
    """
    count = len(tiles)
    if count == 0:
        return []
    rows = -(-count // _TILES_PER_ROW)
    per = max(2, -(-count // rows))
    out: list[list[dict[str, Any] | None]] = []
    for start in range(0, count, per):
        row: list[dict[str, Any] | None] = list(tiles[start : start + per])
        out.append(row + [None] * (per - len(row)))
    return out


def _headline(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The figures the cover leads with — the first section that has any.

    The cover used to be a title, a summary paragraph and 60 % white space, then a hard page
    break. What belongs in that space is not decoration: it is the thing the client opened the
    document for. The first section is the traffic one for every client who has GA4, and its
    totals *are* the month — sessies, gebruikers, conversies — so "in één oogopslag" is
    literally available and was simply not being printed.

    Taken from the resolved section rather than re-derived, so the cover and the section it came
    from can never disagree, and so a client whose first section is Search Console (no GA4) gets
    *their* headline rather than an empty strip.
    """
    for section in sections:
        if section.get("totals"):
            return list(section["totals"])[:_HEADLINE_TILES]
    return []


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


def humanise(value: str) -> str:
    """A developer's identifier, made into something a client can read.

    ``bedankt_offerte_aanvragen`` → *Bedankt offerte aanvragen*. GA4 event names are whatever
    somebody typed into a tag manager years ago, and a tenant *can* rename them per client
    (``marketing_company_settings.layout.event_labels``, #192) — but the default is the raw
    identifier, printed on a document going to a reader who has never seen it. Same family as
    #300's ``totalUsers`` fix: the model stopped writing it because the word left its input, and
    the **table** kept printing it.

    Deliberately conservative: **snake_case and nothing else**. A space means somebody wrote it
    (``Telefoon GA4``), a dot means it is an address (``mail.google.com``, ``s-bb.crm4.dynamics
    .com`` — which a looser rule would helpfully mangle into *S bb.crm4.dynamics.com*), and a
    name somebody chose is not ours to restyle. Only the underscore is unambiguous.
    """
    text = (value or "").strip()
    if not text or "_" not in text or " " in text or "." in text or "/" in text:
        return text
    words = [word for word in text.split("_") if word]
    if not words:
        return text
    return " ".join([words[0][:1].upper() + words[0][1:]] + words[1:])


def _drop_empty_columns(data: dict[str, Any]) -> dict[str, Any]:
    """Columns every row of which is zero or absent.

    :func:`always_zero` already stops a KPI tile that is zero now and was zero then; this is the
    same argument one dimension over. A referral table's *belangrijke gebeurtenissen* column was
    sixteen zeros wide — and, because an auto-layout table spends width on its headings, it was
    the **widest** column on the page. A column that says nothing should not be the one thing
    the reader's eye is drawn to.

    A column with a single non-zero cell stays: that cell is this period's news.
    """
    rows = data.get("rows") or []
    if not rows:
        return data
    keep = []
    for key in data.get("columns") or []:
        present = False
        for row in rows:
            if not isinstance(row, dict):
                continue
            value = row.get(key)
            if value is None or value == "":
                continue
            try:
                if float(value):
                    present = True
                    break
            except (TypeError, ValueError):
                present = True
                break
        if present:
            keep.append(key)
    if len(keep) == len(data.get("columns") or []):
        return data
    return {**data, "columns": keep}


def _fold_tail(data: dict[str, Any], locale: str) -> dict[str, Any]:
    """The long tail of a source table, folded into one row that says how big it is.

    Twelve of sixteen referrers with exactly one session is not sixteen facts, it is four facts
    and a footnote — and printed in full it buries the four. ``peekyou.com · 1 · 1 · 1 · 00:01``
    is a row a client can do nothing with.

    The fold **names the size of what it is not showing** (§17's rule: a cap that truncates says
    so), which is strictly more informative than the rows it replaces: *Overig · 12 bronnen ·
    12 sessies* answers "is there anything else?" in a way thirteen one-session rows do not.

    Only summable metrics are folded. An average — engagement rate, CTR, position — has no
    meaning summed and no honest single value across twelve sources, so the folded row leaves it
    blank rather than printing a number nobody computed.

    Only :data:`_SOURCE_KINDS`: a channel table is Google's closed list of twelve and a
    conversions table is the client's own goals, and folding either would be hiding a choice
    somebody made rather than a tail nobody chose.
    """
    rows = [row for row in (data.get("rows") or []) if isinstance(row, dict)]
    if (data.get("kind") or "") not in _SOURCE_KINDS or len(rows) <= _MIN_KEPT_ROWS + 2:
        return data
    columns = list(data.get("columns") or [])
    primary = columns[0] if columns else "sessions"

    def value_of(row: dict[str, Any]) -> float:
        try:
            return float(row.get(primary) or 0)
        except (TypeError, ValueError):
            return 0.0

    total = sum(value_of(row) for row in rows)
    floor = max(_TAIL_FLOOR, total * _TAIL_SHARE)
    kept = [row for row in rows if value_of(row) >= floor][:_MAX_KEPT_ROWS]
    # The minimum applies to the *result*, not to a position: it tops the table back up only
    # where the floor left almost nothing, and never promotes one arbitrary one-session row to
    # sit above the line that says how many one-session rows there were.
    if len(kept) < _MIN_KEPT_ROWS:
        kept = rows[:_MIN_KEPT_ROWS]
    seen = {id(row) for row in kept}
    tail = [row for row in rows if id(row) not in seen]
    if len(tail) < 2:
        return data
    folded: dict[str, Any] = {
        "label": translate(
            "reporting.doc.other_sources", locale, count=str(len(tail))
        ),
        "folded": True,
    }
    for key in columns:
        if key in _PERCENT_METRICS or key in _DURATION_METRICS or key in _DELTA_METRICS:
            continue
        subtotal = 0.0
        for row in tail:
            try:
                subtotal += float(row.get(key) or 0)
            except (TypeError, ValueError):
                continue
        folded[key] = subtotal
    return {**data, "rows": [*kept, folded]}


def shape_section(
    data: dict[str, Any], locale: str, *, internal: bool = False
) -> dict[str, Any]:
    """One section's stored payload, turned into the table the document actually prints.

    Four narrowings, applied **here** rather than where the section was gathered, and for the
    reason :func:`localise_section` already gives: a report freezes its rows, so a presentation
    rule applied at the renderer also improves the reports already stored and leaves the
    snapshot a record of what the source really said.

    :func:`present.section` calls this too, so the paragraph the model writes describes the
    table the reader is looking at — the same "one formatter" argument that made the renderer
    shared in the first place. A model told about a column the page does not print will write a
    sentence about it.
    """
    data = localise_section(data, locale)
    if (data.get("kind") or "") == "conversions":
        # The tenant's own label wins where they set one; this is the fallback, and the raw
        # identifier is never it. The **chart** is renamed with the table, or the picture beside
        # the rows says `bedankt_offerte_aanvragen` where the row says *Bedankt offerte
        # aanvragen* — the same half-translated failure `localise_section` warns about.
        data = {
            **data,
            "rows": [
                {**row, "label": humanise(str(row.get("label") or ""))}
                if isinstance(row, dict)
                else row
                for row in data.get("rows") or []
            ],
        }
        chart = data.get("chart")
        if isinstance(chart, dict) and chart.get("labels"):
            data["chart"] = {
                **chart,
                "labels": [humanise(str(label)) for label in chart["labels"]],
            }
    if not internal and (data.get("kind") or "") in _SOURCE_KINDS:
        data = {
            **data,
            "columns": [
                key
                for key in data.get("columns") or []
                if key not in _CLIENT_DROPS_COLUMNS
            ],
        }
        data = _fold_tail(data, locale)
    # The change joins the number it describes *before* the empty-column sweep: a compare
    # column that is zero on every row is dropped there, and a change against nothing draws
    # nothing (`change_badge`), so the two rules agree without knowing about each other.
    return _drop_empty_columns(attach_changes(data))


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


def _has_landing_pages(data: dict[str, Any]) -> bool:
    """Whether *any* row of a rankings section names the page that ranked."""
    if (data.get("kind") or "") != "rankings":
        return False
    rows = list(data.get("rows") or [])
    for group in data.get("groups") or []:
        if isinstance(group, dict):
            rows.extend(group.get("rows") or [])
    return any(isinstance(row, dict) and row.get("landing_page") for row in rows)


def _ranked(rows: list[Any]) -> list[Any]:
    """A rankings row, told which way it moved.

    The position cell used to be washed green or red by **absolute** rank — ten or better was
    green, everything else red — which is a verdict rather than a measurement, and the wrong one
    on a monthly report. A term parked at 22 all year earned a red cell every month for standing
    still, and a term that climbed 41 → 38 earned two red cells for its best month in a year.
    What a client wants to know is what changed, so the change is what carries the colour.

    ``change`` is already signed the way a reader expects (positive = climbed, computed by the
    source adapter, because rank 8 → 3 is an improvement even though the number fell), so this
    only has to name it. A row with no move gets no class, and a neutral row is a fact too.
    """
    out: list[Any] = []
    for row in rows:
        if not isinstance(row, dict) or "change" not in row:
            out.append(row)
            continue
        try:
            change = float(row.get("change") or 0)
        except (TypeError, ValueError):
            change = 0.0
        if row.get("status") == "new":
            move = "up"
        else:
            move = "up" if change > 0 else ("down" if change < 0 else "")
        out.append({**row, "move_class": move})
    return out


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
