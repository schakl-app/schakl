"""Per-client marketing tab layout (issue #192).

A tenant curates each client's marketing tab the way My Day is arranged — but the layout
lives **on the company** (it is the curated view of that client's marketing, and exactly what
a portal login sees, #193), stored as a ``layout`` JSONB on ``marketing_company_settings``:

```json
{"sources": {"ga4": {
    "tiles":       ["sessions", "keyEvents"],          // ordered; absence = hidden
    "labels":      {"keyEvents": {"nl": "Aanvragen via de website", "en": "Website enquiries"}},
    "drilldowns":  ["top_pages", "key_events"],         // enabled kinds; absent key = all
    "chart_metric": "sessions"                          // default charted metric
}}}
```

No layout row / no source entry = today's behaviour, so nothing changes until someone edits.
Enforcement is **server-side**, like ``show_key_events`` before it (#134): hidden tiles are
dropped from the metrics payload (panel, tab, overview), never hidden client-side; label
overrides ride the payload so every consumer — web and MCP — shows the tenant's naming.

The legacy ``show_key_events`` boolean (#134) is one special case of "hide these tiles" and is
being replaced over two releases (docs/WORKFLOW.md): this release migrates ``False`` rows into
layouts and keeps honouring the boolean **only where no layout tiles exist**; the next one
drops the column.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.errors import AppError
from app.modules.marketing.sources.base import METRICS_BY_SOURCE, primary_metric

#: Kept in step with each adapter's ``drilldowns`` tuple (sources/*.py).
DRILLDOWNS_BY_SOURCE: dict[str, tuple[str, ...]] = {
    "ga4": ("top_pages", "channels", "devices", "key_events"),
    "gsc": ("top_queries", "top_pages", "movers"),
    "gads": ("campaigns",),
}

#: The GA4 metrics the legacy boolean gates, and the drill-down that rides them (#134).
GA4_KEY_EVENT_TILES = ("keyEvents", "conversions")
GA4_KEY_EVENT_DRILLDOWN = "key_events"

_LOCALES = ("nl", "en")


class SourceLayout(BaseModel):
    """One source's curated shape. ``None`` fields mean "not curated" (defaults apply)."""

    tiles: list[str] | None = None
    labels: dict[str, dict[str, str]] = Field(default_factory=dict)
    drilldowns: list[str] | None = None
    chart_metric: str | None = None


class CompanyLayout(BaseModel):
    sources: dict[str, SourceLayout] = Field(default_factory=dict)


def validate_layout(layout: CompanyLayout) -> None:
    """Reject unknown sources/metrics/drilldowns and malformed labels with a 422.

    The vocabulary is the adapters' own; a stray key would silently hide nothing (or leak a
    tile forever), so it is refused at the door instead.
    """

    def bad(field: str) -> AppError:
        return AppError(
            "validation",
            "errors.validation",
            status_code=422,
            fields={field: "errors.validation"},
        )

    for source, src in layout.sources.items():
        metrics = METRICS_BY_SOURCE.get(source)
        if metrics is None:
            raise bad("layout")
        if src.tiles is not None and any(t not in metrics for t in src.tiles):
            raise bad("layout")
        if src.drilldowns is not None and any(
            d not in DRILLDOWNS_BY_SOURCE.get(source, ()) for d in src.drilldowns
        ):
            raise bad("layout")
        if src.chart_metric is not None and src.chart_metric not in metrics:
            raise bad("layout")
        for metric, labels in src.labels.items():
            if metric not in metrics:
                raise bad("layout")
            for locale, label in labels.items():
                if locale not in _LOCALES or not isinstance(label, str) or len(label) > 80:
                    raise bad("layout")


def source_layout(layout: dict | None, source: str) -> SourceLayout | None:
    """The stored layout's entry for one source, parsed; ``None`` when not curated."""
    if not layout:
        return None
    raw = (layout.get("sources") or {}).get(source)
    if raw is None:
        return None
    return SourceLayout.model_validate(raw)


def resolved_tiles(source: str, src: SourceLayout | None, show_key_events: bool) -> list[str]:
    """The ordered, visible metric keys for one source.

    A curated ``tiles`` list wins outright. Without one, the full default set applies, minus
    the legacy key-events gate (#134) — honoured only here, so a saved layout supersedes the
    boolean rather than fighting it.
    """
    base = METRICS_BY_SOURCE.get(source, [])
    if src is not None and src.tiles is not None:
        return [t for t in src.tiles if t in base]
    if source == "ga4" and not show_key_events:
        return [m for m in base if m not in GA4_KEY_EVENT_TILES]
    return list(base)


def resolved_drilldowns(
    source: str,
    adapter_drilldowns: tuple[str, ...],
    src: SourceLayout | None,
    tiles: list[str],
) -> list[str]:
    """The enabled drill-down kinds. The by-event breakdown exists only while the keyEvents
    tile is visible — a hidden KPI must not keep a back door open (#134)."""
    kinds = list(adapter_drilldowns)
    if src is not None and src.drilldowns is not None:
        kinds = [k for k in src.drilldowns if k in adapter_drilldowns]
    if GA4_KEY_EVENT_DRILLDOWN in kinds and source == "ga4" and "keyEvents" not in tiles:
        kinds.remove(GA4_KEY_EVENT_DRILLDOWN)
    return kinds


def resolved_primary(source: str, src: SourceLayout | None, tiles: list[str]) -> str:
    """The default charted metric: the curated choice if visible, else the source's primary
    if visible, else the first visible tile (a chart of a hidden metric would be a leak)."""
    if src is not None and src.chart_metric in tiles:
        return str(src.chart_metric)
    default = primary_metric(source)
    if default in tiles:
        return default
    return tiles[0] if tiles else default
