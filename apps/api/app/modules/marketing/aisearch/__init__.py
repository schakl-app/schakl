"""SE Ranking's AI Search overview: the settings, and the parse (docs/SERANKING.md).

How visible a client's brand is inside AI answers — ChatGPT, Perplexity, Gemini, Google's AI
Overviews and AI Mode — as four monthly figures and five monthly streams, read from SE
Ranking's **Data API**. Pure functions only: nothing here touches a session or a socket, so a
test feeds ``parse_overview`` a fixture and ``resolve`` two blobs.

Three decisions live here rather than at a call site.

**It is off until somebody switches it on.** One read costs 800 of the agency's own units, and
"every engine separately" costs five times that, per client, per month. So the code default is
``enabled=False``, the house default is a choice made in Instellingen → Marketing, and a client
may differ in either direction — the ``rankings`` idiom (``NULL`` and an absent key both mean
*inherit*, so raising the house default reaches every client who never set their own).

**"Every engine" is one question, not five.** ``all`` is SE Ranking's own cross-engine
aggregate (a separate endpoint), and it is **not** the sum of the five: a prompt answered by
three engines is one prompt. Nothing here adds engines together, and a tenant who picks three
engines gets three blocks rather than a total nobody measured.

**The month on the screen is the month the answer covers.** The platform asks about the last
complete month. SE Ranking's answer names no month of its own — its ``summary`` says
``current`` and ``previous`` — so the month is read off the time series beside it, and the two
ways it can disagree with what was asked are both handled rather than assumed away:
the vendor has not published the month yet (the figures are served *as the month they are*),
or the vendor's newest point is the month still running (the figures are re-read from the
series for the month that was asked about, and a metric with no series keeps only what the
vendor called ``previous``). Relabelling either as "last month" would be a number under the
wrong heading, which nothing on any screen could contradict.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from urllib.parse import urlsplit

from app.modules.marketing.sources.seranking import AI_ENGINES, AI_SCOPES

#: SE Ranking's own aggregate across every engine. Our word, not the API's: on the wire it is
#: the *absence* of ``engine`` on a different endpoint (``SeRankingAdapter.ai_search_overview``).
ENGINE_ALL = "all"
#: Every value the ``engines`` setting may hold, in the order a screen lists them.
ENGINES: tuple[str, ...] = (ENGINE_ALL, *AI_ENGINES)

#: What SE Ranking charges, in units, as its reference states them. Printed on the settings
#: screen beside the choice that spends them (#305: show the constraint working), and stored on
#: each row so "what did this cost us" is answerable without a second source.
UNITS_OVERVIEW = 800
UNITS_DISCOVER_BRAND = 100

DEFAULT_SOURCE = "nl"
DEFAULT_SCOPE = "base_domain"

#: The four headline figures, in reading order (a JSONB column has no key order — CLAUDE.md §10).
METRICS: tuple[str, ...] = (
    "brand_presence",
    "link_presence",
    "average_position",
    "ai_opportunity_traffic",
)
#: The monthly streams. Two of them are also headline figures, which is what makes the
#: running-month realignment in :func:`parse_overview` possible for exactly those two.
STREAMS: tuple[str, ...] = (
    "link_presence",
    "average_position",
    "ai_traffic",
    "organic_traffic",
    "overall_traffic",
)
#: A position is a rank: lower is better, so a fall reads as good.
LOWER_IS_BETTER = frozenset({"average_position"})

STATUS_OK = "ok"
STATUS_FETCHING = "fetching"
#: The ways SE Ranking declines, as ``DataApiRefused.kind`` names them.
REFUSALS = frozenset({"denied", "insufficient", "failed"})

_COUNTRY = re.compile(r"^[a-z]{2}$")
_MONTH = re.compile(r"^(\d{4})-(\d{2})")


# --- settings -------------------------------------------------------------------------------- #
@dataclass(frozen=True)
class AiSearchSettings:
    """The resolved answer for one client — never NULLs, so no caller re-derives inheritance."""

    enabled: bool = False
    engines: tuple[str, ...] = (ENGINE_ALL,)
    #: SE Ranking's ``source``: the alpha-2 country whose prompt database is read.
    source: str = DEFAULT_SOURCE
    scope: str = DEFAULT_SCOPE
    #: Client-only. Empty = derive it from what the client already has linked.
    target: str = ""
    #: Client-only. Empty = the brand SE Ranking attributes to the target.
    brand: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "engines": list(self.engines),
            "source": self.source,
            "scope": self.scope,
            "target": self.target,
            "brand": self.brand,
        }

    @property
    def monthly_units(self) -> int:
        """What one client costs per month at these settings, before any brand lookup."""
        return UNITS_OVERVIEW * len(self.engines)


def _engines(raw: Any, fallback: tuple[str, ...]) -> tuple[str, ...]:
    """A clean engine list in canonical order, or the fallback where there is none to read.

    An empty list inherits rather than meaning "no engines": switching the overview off is what
    ``enabled`` is for, and a form that posts no ticked box must not quietly become a state in
    which the feature is on and asks nothing.
    """
    if not isinstance(raw, (list, tuple)):
        return fallback
    picked = {str(item) for item in raw}
    clean = tuple(engine for engine in ENGINES if engine in picked)
    return clean or fallback


def clean_target(raw: Any) -> str:
    """A target as SE Ranking takes one: a bare host, or a URL when a path was given.

    People paste ``https://www.klant.nl/`` where a domain is meant. The scheme and a lone
    trailing slash say nothing, so they go; a real path survives, because ``scope=url`` needs it.
    """
    value = str(raw or "").strip()
    if not value:
        return ""
    parts = urlsplit(value if "//" in value else f"//{value}")
    host = (parts.hostname or "").lower()
    if not host:
        return ""
    path = parts.path.rstrip("/")
    if not path:
        return host
    return f"{parts.scheme or 'https'}://{host}{path}"[:512]


def parse(
    stored: dict[str, Any] | None,
    *,
    base: AiSearchSettings | None = None,
    client: bool = False,
) -> AiSearchSettings:
    """One stored blob over a base, every value reduced to something the API accepts.

    ``client`` admits the two fields only a client has. At org level they are dropped on the way
    in: a house ``target`` would be one domain's numbers under every client's name.
    """
    base = base or AiSearchSettings()
    if not isinstance(stored, dict):
        return base
    enabled = stored.get("enabled")
    source = str(stored.get("source") or "").strip().lower()
    scope = str(stored.get("scope") or "")
    return AiSearchSettings(
        enabled=enabled if isinstance(enabled, bool) else base.enabled,
        engines=_engines(stored.get("engines"), base.engines),
        source=source if _COUNTRY.match(source) else base.source,
        scope=scope if scope in AI_SCOPES else base.scope,
        target=clean_target(stored.get("target")) if client else "",
        brand=str(stored.get("brand") or "").strip()[:255] if client else "",
    )


def resolve(
    org_stored: dict[str, Any] | None, company_stored: dict[str, Any] | None
) -> AiSearchSettings:
    """The house settings, then this client's own diff over them."""
    return parse(company_stored, base=parse(org_stored), client=True)


def diff(values: dict[str, Any], *, client: bool) -> dict[str, Any] | None:
    """What to **store** for a posted settings form: only the keys that say something.

    ``None`` and an absent key both mean *inherit* (§18: absent means leave alone, and here the
    thing left alone is the layer below), so neither is written — a stored ``"scope": null``
    would be a second spelling of nothing. Returns ``None`` when nothing is left, which is how
    "volg de standaard" on every field becomes a NULL column rather than an empty object.
    """
    out: dict[str, Any] = {}
    if isinstance(values.get("enabled"), bool):
        out["enabled"] = values["enabled"]
    engines = _engines(values.get("engines"), ())
    if engines:
        out["engines"] = list(engines)
    source = str(values.get("source") or "").strip().lower()
    if _COUNTRY.match(source):
        out["source"] = source
    if values.get("scope") in AI_SCOPES:
        out["scope"] = values["scope"]
    if client:
        target = clean_target(values.get("target"))
        if target:
            out["target"] = target
        brand = str(values.get("brand") or "").strip()[:255]
        if brand:
            out["brand"] = brand
    return out or None


# --- the brand -------------------------------------------------------------------------------- #
def _letters(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def brand_fits(brand: str, *, company: str, target: str) -> bool:
    """Whether a brand SE Ranking guessed plausibly *is* this client.

    SE Ranking attributes a brand to a domain by itself, and the mentions it counts are that
    brand's. For ``fietsenwinkel-janssen.nl`` it may well answer "Janssen" — a surname shared
    with a pharmaceutical company, whose mentions would then be this client's "brand presence".
    Nothing can *decide* that from here, so this only ever produces a hint beside the figures:
    the brand fits when it shares its letters with the client's name or with the domain's own
    label, and otherwise the screen asks somebody who knows to type the right one.
    """
    needle = _letters(brand)
    if not needle:
        return True
    host = urlsplit(target if "//" in target else f"//{target}").hostname or target
    label = host.removeprefix("www.").split(".")[0]
    for candidate in (_letters(company), _letters(label)):
        if candidate and (needle in candidate or candidate in needle):
            return True
    return False


# --- the parse -------------------------------------------------------------------------------- #
def month_of(value: Any) -> date | None:
    """The first of the month a time-series point names — ``YYYY-MM``, or a full date."""
    match = _MONTH.match(str(value or ""))
    if not match:
        return None
    year, month = int(match.group(1)), int(match.group(2))
    return date(year, month, 1) if 1 <= month <= 12 else None


def previous_month(month: date) -> date:
    return date(month.year - 1, 12, 1) if month.month == 1 else date(month.year, month.month - 1, 1)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rank(value: float | None) -> float | None:
    """A position as SE Ranking sends one: ``0`` is its word for *no position*, not first place.

    Measured on the live API (docs/SERANKING.md §10): an engine read with no series — ChatGPT or
    Perplexity for a Dutch domain — answers ``average_position: 0`` beside non-zero presence
    counts. Printed, that is "position 0", which reads as better than first.
    """
    return value if value is not None and value > 0 else None


@dataclass(frozen=True)
class ParsedOverview:
    """One answer, aligned to the month it is about."""

    #: The month the figures cover. ``None`` only when the answer carried no series *and* the
    #: caller named no month — which the service never does.
    data_month: date | None
    #: ``{metric: {"current": …, "previous": …}}``, either side possibly ``None``.
    summary: dict[str, dict[str, float | None]]
    #: ``{stream: [{"month": "YYYY-MM", "value": …}]}``, oldest first.
    series: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    #: ``summary`` was re-read from the series because the vendor's newest point was the month
    #: still running — so the two figures with no series have no "previous" to compare with.
    realigned: bool = False
    #: SE Ranking holds no AI answers for this target in this country database (its own
    #: ``no_index``, or an answer with no figure and no series at all). A state, not four zeros.
    no_data: bool = False


def _series(body: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    raw = body.get("time_series")
    out: dict[str, list[dict[str, Any]]] = {}
    if not isinstance(raw, dict):
        return out
    for stream in STREAMS:
        points: dict[date, float] = {}
        for point in raw.get(stream) or []:
            if not isinstance(point, dict):
                continue
            month, value = month_of(point.get("date")), _number(point.get("value"))
            if stream in LOWER_IS_BETTER:
                value = _rank(value)
            if month is not None and value is not None:
                points[month] = value
        if points:
            out[stream] = [
                {"month": month.strftime("%Y-%m"), "value": points[month]}
                for month in sorted(points)
            ]
    return out


def parse_overview(body: dict[str, Any], expected: date) -> ParsedOverview:
    """SE Ranking's overview body, aligned to ``expected`` (the month that was asked about).

    See the module docstring for the three cases. The vendor's own ``change_absolute`` and
    ``change_percent`` are deliberately **not** read: their sign convention differs per metric
    (a position that improved from 9,2 to 8,5 arrives as ``+0.7``), and after a realignment
    they describe two other months anyway. The change is computed from the two values we keep,
    where it is drawn.
    """
    series = _series(body)
    raw = body.get("summary") if isinstance(body.get("summary"), dict) else {}
    vendor: dict[str, dict[str, float | None]] = {}
    for metric in METRICS:
        entry = raw.get(metric) if isinstance(raw.get(metric), dict) else {}
        current, previous = _number(entry.get("current")), _number(entry.get("previous"))
        if metric in LOWER_IS_BETTER:
            current, previous = _rank(current), _rank(previous)
        vendor[metric] = {"current": current, "previous": previous}

    no_data = bool(body.get("no_index")) or (
        not series and all(pair["current"] is None for pair in vendor.values())
    )
    months = [month_of(p["month"]) for points in series.values() for p in points]
    latest = max((m for m in months if m is not None), default=None)
    if latest is None or latest <= expected:
        # The ordinary case (latest == expected), and the lagging one (latest < expected): the
        # vendor's values are the vendor's newest month, and that is what they are served as.
        # Its ``previous`` is null on every live answer seen so far — so a figure that has a
        # series takes the month before from the series, or no tile would ever show a change.
        data_month = latest or expected
        before = previous_month(data_month).strftime("%Y-%m")
        summary: dict[str, dict[str, float | None]] = {}
        for metric in METRICS:
            pair = dict(vendor[metric])
            by_month = {p["month"]: p["value"] for p in series.get(metric, [])}
            if pair["current"] is None:
                pair["current"] = by_month.get(data_month.strftime("%Y-%m"))
            if pair["previous"] is None:
                pair["previous"] = by_month.get(before)
            summary[metric] = pair
        return ParsedOverview(
            data_month=data_month, summary=summary, series=series, no_data=no_data
        )

    # The vendor's newest point is a month we did not ask about — the one still running.
    before = previous_month(expected)
    summary = {}
    for metric in METRICS:
        if metric in series:
            by_month = {p["month"]: p["value"] for p in series[metric]}
            summary[metric] = {
                "current": by_month.get(expected.strftime("%Y-%m")),
                "previous": by_month.get(before.strftime("%Y-%m")),
            }
        else:
            # No series to re-read. What the vendor called "previous" is the asked-about month
            # exactly when its "current" is the month right after it; anything further out says
            # nothing about ``expected`` at all.
            is_next = previous_month(latest) == expected
            summary[metric] = {
                "current": vendor[metric]["previous"] if is_next else None,
                "previous": None,
            }
    return ParsedOverview(
        data_month=expected, summary=summary, series=series, realigned=True, no_data=no_data
    )


def change(metric: str, current: float | None, previous: float | None) -> dict[str, Any]:
    """The move between two months, and whether it reads as good.

    ``direction`` is where the number went; ``verdict`` is what that means — kept apart for the
    same reason the report's badge keeps its arrow and its colour apart (CLAUDE.md §10): an
    average position that fell is a down arrow in green.
    """
    if current is None or previous is None:
        return {"absolute": None, "percent": None, "direction": None, "verdict": None}
    absolute = current - previous
    percent = (absolute / previous * 100.0) if previous else None
    direction = "up" if absolute > 0 else "down" if absolute < 0 else "flat"
    if direction == "flat":
        verdict = "neutral"
    else:
        rose = direction == "up"
        verdict = "good" if rose != (metric in LOWER_IS_BETTER) else "bad"
    return {
        "absolute": round(absolute, 2),
        "percent": round(percent, 1) if percent is not None else None,
        "direction": direction,
        "verdict": verdict,
    }


__all__ = [
    "DEFAULT_SCOPE",
    "DEFAULT_SOURCE",
    "ENGINES",
    "ENGINE_ALL",
    "LOWER_IS_BETTER",
    "METRICS",
    "REFUSALS",
    "STATUS_FETCHING",
    "STATUS_OK",
    "STREAMS",
    "UNITS_DISCOVER_BRAND",
    "UNITS_OVERVIEW",
    "AiSearchSettings",
    "ParsedOverview",
    "brand_fits",
    "change",
    "clean_target",
    "diff",
    "month_of",
    "parse",
    "parse_overview",
    "previous_month",
    "resolve",
]
