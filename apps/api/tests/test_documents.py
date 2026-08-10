"""The shared document core (issue #300): the engine seam, and charts that print.

The engine itself is exercised end-to-end by ``test_invoicing_render.py`` — it renders the
real designs through the real WeasyPrint. What is tested here is what *moving* it made
possible and what is new: that a second document family can bind its own designs, and that
the charts are safe to put in a document at all.

"Safe to put in a document" is a specific claim with the walls behind it:
``engine.no_network_fetcher`` refuses every scheme but ``data:``, so a chart that emitted an
``<img src="http://…">`` would not render — it would raise mid-print, on a client's report,
in a background job. The n8n workflow this replaces did exactly that (QuickChart URLs), which
is why the assertions below are about *no external references* and *well-formed XML* rather
than about pixels.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from app.core.documents import (
    ChartStyle,
    column_chart,
    grouped_columns,
    share_bar,
    share_palette,
    sparkline,
)

STYLE = ChartStyle(accent="#4f46e5")


def _parsed(svg: str) -> ET.Element:
    """Every chart must be well-formed XML — it is embedded in a document, not a browser page.

    WeasyPrint parses the SVG itself; a stray unescaped ``&`` in a campaign name would take the
    whole document down rather than one chart.
    """
    assert svg.startswith("<svg "), svg[:80]
    return ET.fromstring(svg)  # noqa: S314 — our own output, and that is the point


def test_charts_are_well_formed_and_reference_nothing_external() -> None:
    charts = [
        column_chart(["Organic", "Direct"], [120, 80], style=STYLE, title="Kanalen"),
        grouped_columns(
            ["Organic", "Direct"],
            [("Nu", [120, 80]), ("Vorig jaar", [90, 95])],
            style=STYLE,
            title="Vergelijking",
        ),
        share_bar([("Google", 900), ("Bing", 60)], style=STYLE, title="Zoekmachines"),
        sparkline([1, 4, 2, 8, 5], style=STYLE, title="Trend"),
    ]
    for svg in charts:
        _parsed(svg)
        for scheme in ("http://", "https://", "file:", "//"):
            assert scheme not in svg.replace('xmlns="http://www.w3.org/2000/svg"', ""), svg[:200]


def test_a_label_that_looks_like_markup_is_escaped() -> None:
    """Campaign and channel names are tenant *data*: they arrive from Google, not from us."""
    svg = column_chart(
        ['<script>x</script> & "co"'], [10], style=STYLE, title="<b>t</b> & more"
    )
    root = _parsed(svg)
    assert "<script>" not in svg
    texts = [node.text or "" for node in root.iter() if node.tag.endswith("text")]
    assert any("<script>x</script>" in text for text in texts), texts


def test_empty_input_draws_nothing_rather_than_an_empty_frame() -> None:
    """A client with no social traffic gets no chart, not an axis around a void.

    The section then says so in words. An empty plot reads as a broken report.
    """
    assert column_chart([], [], style=STYLE) == ""
    assert grouped_columns([], [], style=STYLE) == ""
    assert share_bar([], style=STYLE) == ""
    assert share_bar([("Google", 0)], style=STYLE) == ""
    assert sparkline([5], style=STYLE) == ""


def test_share_bar_folds_the_tail_into_one_named_segment() -> None:
    """Past the cap the tail becomes "Overig", never a generated colour nobody can name."""
    items = [(f"engine-{i}", float(20 - i)) for i in range(9)]
    svg = share_bar(items, style=STYLE, other_label="Overig", max_segments=4)
    root = _parsed(svg)
    labels = [node.text or "" for node in root.iter() if node.tag.endswith("text")]
    assert any(label.startswith("Overig") for label in labels), labels
    # Four named segments plus the fold — never nine.
    assert len([r for r in root.iter() if r.tag.endswith("rect")]) == 5 + 5  # bars + legend keys


def test_the_palette_a_table_marks_rows_with_is_the_one_the_bar_draws() -> None:
    """The dot beside a row and the segment it names come from one function, or they drift.

    ``share_palette`` exists so a *table* can carry the chart's colours. The property that
    makes that worth doing is this one: every colour it hands out is a fill the bar actually
    paints, in the same order — so a reader matching a row to a segment is never one step off.
    """
    items = [("Google", 90.0), ("Bing", 20.0), ("DuckDuckGo", 6.0), ("Ecosia", 4.0)]
    segments = share_palette(items, style=STYLE, other_label="Overig")
    svg = share_bar(items, style=STYLE, other_label="Overig")
    fills = [
        node.get("fill")
        for node in _parsed(svg).iter()
        if node.tag.endswith("rect") and node.get("rx") == "2"
    ]
    # Each segment is painted twice — once as the bar, once as its legend key.
    assert [s.colour for s in segments] == fills[: len(segments)]
    assert [s.label for s in segments] == ["Google", "Bing", "DuckDuckGo", "Ecosia"]
    assert not any(s.tail for s in segments)


def test_the_folded_tail_is_marked_so_a_table_does_not_hand_four_rows_one_colour() -> None:
    """The remainder segment stands for every row past the cap, so it names none of them."""
    items = [(f"engine-{index}", float(20 - index)) for index in range(9)]
    segments = share_palette(items, style=STYLE, other_label="Overig", max_segments=4)
    assert [s.tail for s in segments] == [False, False, False, False, True]
    assert segments[-1].label == "Overig"
    # And the fold is part of the *scale*: drop the label and the four named segments are
    # spread across a shorter ramp, which is exactly how a dot ends up a step off its segment.
    without = share_palette(items, style=STYLE, max_segments=4)
    assert [s.colour for s in without] != [s.colour for s in segments[:4]]


def test_grouped_columns_caps_at_two_series() -> None:
    """A period has exactly one comparison; a third grouped column stops being readable."""
    svg = grouped_columns(
        ["a", "b"],
        [("one", [1, 2]), ("two", [3, 4]), ("three", [5, 6])],
        style=STYLE,
    )
    root = _parsed(svg)
    labels = [node.text or "" for node in root.iter() if node.tag.endswith("text")]
    assert "three" not in labels


def test_a_zero_value_draws_no_bar_rather_than_a_sliver() -> None:
    svg = column_chart(["a", "b"], [0, 50], style=STYLE)
    root = _parsed(svg)
    assert len([p for p in root.iter() if p.tag.endswith("path")]) == 1


@pytest.mark.parametrize("locale", ["nl", "en"])
def test_the_engine_binds_a_family_to_its_own_designs_and_keys(locale: str) -> None:
    """A second document family is a second ``DocumentEngine``, not a second renderer."""
    from app.modules.invoicing.render.engine import ENGINE

    assert ENGINE.page_key == "invoicing.doc.page"
    body, css = ENGINE.builtin_source("letterhead")
    assert body and css
    # An unknown design falls back rather than raising: a config edited by hand must still
    # print something the tenant recognises.
    assert ENGINE.builtin_source("nope") == ENGINE.builtin_source(ENGINE.default_design)
    from app.core.documents import page_number_css

    assert "counter(page)" in page_number_css(ENGINE.page_key, locale)


def test_report_sections_compose_from_enabled_modules_only() -> None:
    """The registry seam: reporting composes sections and names no module (CLAUDE.md §6)."""
    from app.registry import ModuleDescriptor, ModuleRegistry, ReportSectionSpec

    async def _provider(ctx, window):  # noqa: ANN001, ANN202, ARG001
        return {}

    registry = ModuleRegistry()
    registry.register(
        ModuleDescriptor(
            name="alpha",
            report_sections=[
                ReportSectionSpec("alpha.one", "t", _provider, position=20),
                ReportSectionSpec("alpha.secret", "t", _provider, audience="internal"),
                ReportSectionSpec("alpha.shared", "t", _provider, audience="both", position=10),
            ],
        )
    )
    registry.register(
        ModuleDescriptor(
            name="beta", report_sections=[ReportSectionSpec("beta.one", "t", _provider)]
        )
    )

    client = [s.key for s in registry.report_sections_for("client", ["alpha", "beta"])]
    assert client == ["alpha.shared", "alpha.one", "beta.one"]

    internal = [s.key for s in registry.report_sections_for("internal", ["alpha", "beta"])]
    assert internal == ["alpha.shared", "alpha.secret"]

    # A disabled module contributes nothing — the whole point of routing this through the
    # registry rather than letting the reporting module import its sources.
    assert [s.key for s in registry.report_sections_for("client", ["beta"])] == ["beta.one"]
    assert registry.report_section("alpha.one", ["beta"]) is None
    assert registry.report_section("beta.one", ["beta"]).title_key == "t"


def test_a_share_that_is_not_zero_never_prints_as_zero() -> None:
    """A folded tail below one per cent is still sessions, and "Overig 0%" reads as a fault.

    The threshold is what the *rounding* produces rather than a number guessed beside it:
    exactly 0,5 % rounds to "0%" under banker's rounding, so a `< 0.005` guard would let
    through the one value it was written to catch.
    """
    import re

    def texts(svg: str) -> list[str]:
        return re.findall(r">([^<>]+)</text>", svg)

    assert "b &lt;1%" in texts(share_bar([("a", 199), ("b", 1)], style=STYLE))
    assert "b &lt;1%" in texts(share_bar([("a", 999), ("b", 1)], style=STYLE))
    # An honest quarter still prints as one.
    assert "b 25%" in texts(share_bar([("a", 3), ("b", 1)], style=STYLE))
