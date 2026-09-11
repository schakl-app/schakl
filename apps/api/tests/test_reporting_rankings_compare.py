"""A rank is compared with last month, and a column says when it was read.

The first August report printed *Gem. positie 7,0 ▼ −32,6%* over a comparison with **August
2025**, because the rankings section borrowed the report's own comparison window — right for
traffic, where seasonality is the argument (#312), and wrong for a level. Three things are pinned
here: the section compares with the month before and says so; the position columns are headed
with the day (or month) they were read over; and the channel table carries the goals a channel
produced beside its sessions.

No database and no Google session: the providers are reached through their own memo seam, and
the renderer through ``build_context``.
"""

from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from app.modules.marketing.rankings import RankingSettings, RankingSource
from app.modules.marketing.report_sections import (
    _CACHE_ATTR,
    GatheredMarketing,
    Part,
    _rankings,
    _traffic_channels,
)
from app.modules.reporting import present, prompts
from app.modules.reporting.render import context as ctx
from app.registry import ReportWindow

pytestmark = pytest.mark.anyio


class _Ctx:
    """Enough of a context to reach a section provider through its own memo."""


def _seeded(data: GatheredMarketing, window: ReportWindow) -> _Ctx:
    out = _Ctx()
    key = (window.company_id, window.start, window.end, window.compare_start)
    setattr(out, _CACHE_ATTR, {key: data})
    return out


def _window() -> ReportWindow:
    return ReportWindow(
        company_id=uuid.uuid4(),
        start=date(2026, 8, 1),
        end=date(2026, 8, 31),
        compare_start=date(2025, 8, 1),
        compare_end=date(2025, 8, 31),
    )


def _rows() -> list[dict[str, Any]]:
    return [
        {
            "keyword": "archery tag zeeland",
            "group": "Activiteiten",
            "begin": 1,
            "end": 2,
            "change": -1,
            "status": "declined",
            "landing_page": "https://klokuus.com/activiteiten/archery-tag-in-zeeland",
            "volume": 10,
        }
    ]


def _link(source: str) -> Any:
    return SimpleNamespace(
        id=uuid.uuid4(), source=source, display_name=source, config={}, connection_id=None
    )


# --------------------------------------------------------------------------------------- #
# The section compares with the month before, and says so
# --------------------------------------------------------------------------------------- #
async def test_the_rankings_tiles_compare_with_last_month_not_the_reports_window() -> None:
    window = _window()
    link = _link("seranking")
    data = GatheredMarketing(
        keywords=_rows(),
        keyword_source=RankingSource.SERANKING,
        ranking_settings=RankingSettings(),
        parts={"seranking": [Part(key="seranking", label="", links=(link,))]},
        stored={
            "seranking": {
                "totals": {"avg_position": 7.0, "top3": 36.5},
                # What the report's own window would have handed the tiles: a year back.
                "compare": {"avg_position": 10.4, "top3": 37.7},
            }
        },
        rankings_compare={"avg_position": 7.6, "top3": 33.1},
        rankings_compare_period=(date(2026, 7, 1), date(2026, 7, 31)),
        keyword_spans={
            "begin": (date(2026, 8, 1), date(2026, 8, 1)),
            "end": (date(2026, 8, 31), date(2026, 8, 31)),
        },
    )

    payload = await _rankings(_seeded(data, window), window)  # type: ignore[arg-type]

    assert payload is not None
    assert payload["compare"] == {"avg_position": 7.6, "top3": 33.1}
    assert payload["compare_period"] == {"start": "2026-07-01", "end": "2026-07-31"}
    assert payload["begin_span"] == {"start": "2026-08-01", "end": "2026-08-01"}
    assert payload["end_span"] == {"start": "2026-08-31", "end": "2026-08-31"}


async def test_with_no_previous_month_the_tiles_compare_with_nothing() -> None:
    """A client whose tracker was set up this month: no denominator, no percentage — never the
    year-earlier figure sneaking back in because it happened to be there."""
    window = _window()
    data = GatheredMarketing(
        keywords=_rows(),
        keyword_source=RankingSource.SERANKING,
        stored={"seranking": {"totals": {"avg_position": 7.0}, "compare": {"avg_position": 9}}},
        rankings_compare=None,
        rankings_compare_period=(date(2026, 7, 1), date(2026, 7, 31)),
    )

    payload = await _rankings(_seeded(data, window), window)  # type: ignore[arg-type]

    assert payload is not None
    assert payload["compare"] is None
    assert payload["compare_period"] is None


# --------------------------------------------------------------------------------------- #
# The column is headed with the day it was read on
# --------------------------------------------------------------------------------------- #
def test_a_day_prints_as_a_day_and_a_month_as_a_month() -> None:
    assert prompts.span_label(date(2026, 8, 1), date(2026, 8, 1), "nl") == "1 aug"
    assert prompts.span_label(date(2026, 8, 31), date(2026, 8, 31), "en") == "Aug 31"
    # Dutch shortens maart to mrt — a `[:3]` would have printed "maa".
    assert prompts.day_label(date(2026, 3, 1), "nl") == "1 mrt"
    # Search Console averages over a month, so its column is the month.
    assert prompts.span_label(date(2026, 7, 1), date(2026, 7, 31), "nl") == "juli 2026"


def _rankings_snapshot(*, with_spans: bool) -> dict[str, Any]:
    section: dict[str, Any] = {
        "kind": "rankings",
        "columns": ["begin", "end", "change"],
        "rows": _rows(),
        "groups": [{"name": "Activiteiten", "rows": _rows()}],
        "totals": {"avg_position": 7.0},
        "compare": {"avg_position": 7.6},
        "chart": None,
    }
    if with_spans:
        section |= {
            "compare_period": {"start": "2026-07-01", "end": "2026-07-31"},
            "begin_span": {"start": "2026-08-01", "end": "2026-08-01"},
            "end_span": {"start": "2026-08-31", "end": "2026-08-31"},
        }
    return {
        "order": ["marketing.rankings"],
        "period": {"label": "augustus 2026"},
        "compare": {"label": "augustus 2025"},
        "company": {"name": "Klok'uus"},
        "sections": {"marketing.rankings": section},
    }


def _build(snapshot: dict[str, Any]) -> dict[str, Any]:
    return ctx.build_context(
        report=SimpleNamespace(title="t", company_name="Klok'uus"),
        snapshot=snapshot,
        narrative={},
        section_titles={"marketing.rankings": "Zoekwoordposities"},
        brand_name="breik.",
        logo_uri=None,
        cover_uri=None,
        client_logo_uri=None,
        accent=None,
        intro_text=None,
        footer_text=None,
        locale="nl",
        internal=False,
    )


def test_the_document_heads_the_position_columns_with_their_dates() -> None:
    section = _build(_rankings_snapshot(with_spans=True))["sections"][0]
    assert section["begin_label"] == "1 aug"
    assert section["end_label"] == "31 aug"
    # The section names its own comparison, because the cover's line no longer describes it.
    assert section["compare_label"] == "juli 2026"


def test_a_snapshot_stored_before_the_spans_keeps_the_words() -> None:
    section = _build(_rankings_snapshot(with_spans=False))["sections"][0]
    assert section["begin_label"] is None
    assert section["end_label"] is None
    assert section["compare_label"] is None


def test_the_model_is_told_which_span_the_rankings_compare_with() -> None:
    """The first live narrative wrote "augustus 2025: 10,4" under a July comparison."""
    presented = present.section(
        _rankings_snapshot(with_spans=True)["sections"]["marketing.rankings"],
        locale="nl",
        title="Zoekwoordposities",
        compare_label="augustus 2025",
    )
    assert presented["compared_with"] == "juli 2026"
    tile = presented["totals"][0]
    assert tile["juli 2026"] == "7,6"
    assert "augustus 2025" not in tile
    row = presented["groups"][0]["rows"][0]
    assert row["Begin periode (1 aug)"] == "1"
    assert row["Einde periode (31 aug)"] == "2"


def test_a_landing_page_prints_without_its_scheme_and_breaks_at_a_slash() -> None:
    html = str(ctx.fmt_url("https://klokuus.com/activiteiten/archery-tag-in-zeeland/"))
    assert html == "klokuus.com<wbr>/activiteiten<wbr>/archery-tag-in-zeeland"
    # Tenant data still goes through the escaper.
    assert "<script" not in str(ctx.fmt_url("https://x.nl/<script>"))


# --------------------------------------------------------------------------------------- #
# The channel table carries the goals a channel produced
# --------------------------------------------------------------------------------------- #
async def test_the_channel_table_folds_the_live_goals_onto_the_stored_rows() -> None:
    window = _window()
    link = _link("ga4")
    data = GatheredMarketing(
        parts={"ga4": [Part(key="ga4", label="", links=(link,))]},
        stored={
            "ga4": {
                "totals": {"sessions": 300},
                "compare": {"sessions": 250},
                "channels": {"Organic Search": 200.0, "Direct": 100.0},
                "compare_channels": {"Organic Search": 150.0, "Direct": 100.0},
                "currency": None,
            }
        },
        live={
            "ga4": {
                "channels": {
                    "columns": ["sessions", "keyEvents"],
                    "rows": [
                        {"label": "Organic Search", "sessions": 200, "keyEvents": 12},
                        {"label": "Direct", "sessions": 100, "keyEvents": 3},
                    ],
                    "compare_rows": [
                        {"label": "Organic Search", "sessions": 150, "keyEvents": 8},
                    ],
                }
            }
        },
    )

    payload = await _traffic_channels(_seeded(data, window), window)  # type: ignore[arg-type]

    assert payload is not None
    assert payload["columns"] == [
        "sessions",
        "compare_sessions",
        "delta",
        "share",
        "keyEvents",
        "keyEvents_delta",
    ]
    organic, direct = payload["rows"]
    # The sessions stay the warehoused figure; the goals ride beside them.
    assert organic["sessions"] == 200
    assert organic["keyEvents"] == 12
    assert organic["compare_keyEvents"] == 8
    assert organic["keyEvents_delta"] == 50.0
    # A channel with no goals last year has nothing honest to compare against.
    assert direct["keyEvents"] == 3
    assert direct["keyEvents_delta"] is None


async def test_a_failed_goals_read_costs_the_column_and_never_the_table() -> None:
    window = _window()
    link = _link("ga4")
    data = GatheredMarketing(
        parts={"ga4": [Part(key="ga4", label="", links=(link,))]},
        stored={
            "ga4": {
                "totals": {"sessions": 300},
                "compare": None,
                "channels": {"Organic Search": 200.0},
                "compare_channels": {},
                "currency": None,
            }
        },
        live={"ga4": {}},
    )

    payload = await _traffic_channels(_seeded(data, window), window)  # type: ignore[arg-type]

    assert payload is not None
    assert payload["columns"] == ["sessions", "compare_sessions", "delta", "share"]
    assert "keyEvents" not in payload["rows"][0]


def test_the_goals_change_rides_the_goals_cell() -> None:
    """Two percentages on one row need two keys, or the first host takes both."""
    shaped = ctx.shape_section(
        {
            "kind": "channels",
            "columns": [
                "sessions",
                "compare_sessions",
                "delta",
                "share",
                "keyEvents",
                "keyEvents_delta",
            ],
            "rows": [
                {
                    "label": "Organic Search",
                    "sessions": 200,
                    "compare_sessions": 150,
                    "delta": 33.3,
                    "share": 66.7,
                    "keyEvents": 12,
                    "compare_keyEvents": 8,
                    "keyEvents_delta": 50.0,
                },
            ],
            "totals": {},
            "compare": None,
            "chart": None,
        },
        "nl",
    )
    assert shaped["columns"] == ["sessions", "compare_sessions", "share", "keyEvents"]
    assert shaped["changes"] == {"sessions": "delta", "keyEvents": "keyEvents_delta"}
    # It prints as a percentage, like the sessions' delta does.
    assert ctx.fmt_metric("keyEvents_delta", 50.0, "nl") == "+50,0%"
    assert "+50,0%" in str(ctx.change_badge("keyEvents_delta", 50.0, "nl"))
