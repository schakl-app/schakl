"""What the document a client receives actually says and how it is laid out (issue #373).

Everything here is about the *presentation* layer between a frozen snapshot and the page — the
half that has no functional test to protect it, because a table that spends its width on a
heading and one that spends it on the source both render, both validate and both print. The
difference is only visible if you measure it or read it aloud.

Four rules, each one a defect that reached a client's desk:

* the table's geometry is stated, not negotiated by the widest column heading;
* a column that says nothing is not the widest thing on the page;
* the long tail is folded into a line that says how big it is, not printed in full;
* a developer's identifier is never what a client reads.

The model reads the same shaped section the page prints (``present.section``), so a fifth rule
comes free: the paragraph cannot describe a column the table dropped.
"""

from __future__ import annotations

import pytest

from app.modules.reporting import present
from app.modules.reporting.render import context as ctx

pytestmark = pytest.mark.anyio


SEVEN = [
    "sessions",
    "newUsers",
    "totalUsers",
    "screenPageViews",
    "avg_engagement_time",
    "engagementRate",
    "keyEvents",
]


def _referrals(count: int, *, tail_sessions: int = 1) -> dict:
    """A referral table: four real sources and a tail of one-session referrers."""
    rows = [
        {"label": "mail.google.com", "sessions": 14, "newUsers": 0, "totalUsers": 1,
         "screenPageViews": 14, "avg_engagement_time": 151.0, "engagementRate": 0.57,
         "keyEvents": 0},
        {"label": "l.wl.co", "sessions": 5, "newUsers": 2, "totalUsers": 2,
         "screenPageViews": 8, "avg_engagement_time": 104.0, "engagementRate": 0.6,
         "keyEvents": 0},
        {"label": "startgoogle.startpagina.nl", "sessions": 5, "newUsers": 1, "totalUsers": 1,
         "screenPageViews": 53, "avg_engagement_time": 2053.0, "engagementRate": 1.0,
         "keyEvents": 0},
        {"label": "garagebaas.nl", "sessions": 4, "newUsers": 0, "totalUsers": 1,
         "screenPageViews": 17, "avg_engagement_time": 324.0, "engagementRate": 0.75,
         "keyEvents": 0},
    ]
    rows += [
        {"label": f"noise{index}.example", "sessions": tail_sessions, "newUsers": 1,
         "totalUsers": 1, "screenPageViews": 1, "avg_engagement_time": 3.0,
         "engagementRate": 0.0, "keyEvents": 0}
        for index in range(count)
    ]
    return {
        "kind": "referral_sources",
        "columns": list(SEVEN),
        "rows": rows,
        "totals": {},
        "compare": None,
        "chart": None,
    }


# --------------------------------------------------------------------------------------- #
# Geometry
# --------------------------------------------------------------------------------------- #
def test_a_table_states_its_widths_and_the_name_column_gets_the_remainder() -> None:
    """The allocation is decided up front, and the name column is never the loser.

    The defect this pins: an auto-layout table allocates by *content demand*, and the loudest
    demand on a traffic table is the heading BELANGRIJKE GEBEURTENISSEN — one unbreakable phrase
    at 7pt — competing with cells holding two digits. It won, so every hostname broke mid-token
    while a column of zeros took twice its width.
    """
    for count in (1, 2, 3, 4, 5, 6, 7):
        name, per, _ = ctx.column_widths(count)
        assert name + per * count == pytest.approx(100.0, abs=0.5)
        # The name column always holds the largest single share: it is the only column whose
        # content is genuinely long.
        assert name > per, (count, name, per)
        # …and never so narrow that a hostname has to break inside a word again.
        assert name >= 24.0, (count, name)


def test_a_column_that_carries_its_change_is_wider_and_the_name_column_still_wins() -> None:
    """``4.124 ▲ +26,5%`` is a number and a badge in one cell, and the cell is told so.

    The budget is the same one: a table that folded its VERSCHIL column into its SESSIES column
    spends about what it spent before, so the name column — the hostnames — keeps its share.
    """
    for count in (2, 3, 4, 5):
        name, per, host = ctx.column_widths(count, hosting=1)
        assert name + per * (count - 1) + host == pytest.approx(100.0, abs=0.5)
        assert host > per
        assert name > host, (count, name, host)
        assert name >= 24.0
    # No host, no third width to speak of — and the old answer exactly.
    assert ctx.column_widths(3)[:2] == ctx.column_widths(3, hosting=0)[:2]


# --------------------------------------------------------------------------------------- #
# The change rides the number it is about
# --------------------------------------------------------------------------------------- #
def _channels() -> dict:
    return {
        "kind": "channels",
        "columns": ["sessions", "compare_sessions", "delta", "share"],
        "rows": [
            {"label": "Organic Search", "sessions": 1240, "compare_sessions": 980,
             "delta": 26.5, "share": 62.0},
            {"label": "Direct", "sessions": 760, "compare_sessions": 800,
             "delta": -5.0, "share": 38.0},
        ],
        "totals": {},
        "compare": None,
        "chart": None,
    }


def test_a_difference_column_is_folded_into_the_column_it_describes() -> None:
    """Three cells for two facts, with the one a reader wants at the far end of the row."""
    shaped = ctx.shape_section(_channels(), "nl")
    assert shaped["columns"] == ["sessions", "compare_sessions", "share"]
    assert shaped["changes"] == {"sessions": "delta"}
    # The rows are untouched: the snapshot stays the record of what the source said.
    assert shaped["rows"][0]["delta"] == 26.5

    # A move in places belongs to the position it produced — the engine table's average, the
    # rankings table's end rank.
    engines = ctx.shape_section(
        {
            "kind": "engines",
            "columns": ["keywords_tracked", "top3", "top10", "avg_position", "change"],
            "rows": [{"label": "Google", "keywords_tracked": 145, "top3": 21, "top10": 60,
                      "avg_position": 19.4, "change": 3.0}],
            "totals": {},
            "compare": None,
            "chart": None,
        },
        "nl",
    )
    assert engines["columns"] == ["keywords_tracked", "top3", "top10", "avg_position"]
    assert engines["changes"] == {"avg_position": "change"}
    # A table with no change column is handed back as it was.
    assert "changes" not in ctx.shape_section(_referrals(0), "nl")


def test_a_change_badge_says_direction_with_an_arrow_and_verdict_with_a_colour() -> None:
    """Two signals, deliberately separate: an average position that *fell* is *good*."""
    up = str(ctx.change_badge("delta", 26.5, "nl"))
    assert 'class="badge up"' in up and "+26,5%" in up and 'class="arrow"' in up
    down = str(ctx.change_badge("delta", -5.0, "nl"))
    assert 'class="badge down"' in down and "-5,0%" in down
    # A move in places is a signed count, not a percentage.
    assert "+3" in str(ctx.change_badge("change", 3.0, "nl"))
    assert "%" not in str(ctx.change_badge("change", 3.0, "nl"))
    # No movement: no arrow, muted. Nothing to compare against: nothing at all.
    flat = str(ctx.change_badge("delta", 0, "nl"))
    assert 'class="badge neutral"' in flat and "arrow" not in flat
    assert str(ctx.change_badge("delta", None, "nl")) == ""
    assert str(ctx.change_badge("delta", "n/a", "nl")) == ""


def test_the_document_draws_the_change_beside_the_number_and_has_no_difference_column() -> None:
    """The head no longer says VERSCHIL; the sessions cell says ``1.240 ▲ +26,5%``."""
    from app.modules.reporting.render.engine import ENGINE

    snapshot = {
        "company": {"name": "Acme B.V."},
        "period": {"label": "juli 2026"},
        "compare": {"label": "juli 2025"},
        "order": ["marketing.traffic_channels", "marketing.rankings"],
        "sections": {
            "marketing.traffic_channels": _channels(),
            "marketing.rankings": {
                "kind": "rankings",
                "columns": ["begin", "end", "change"],
                "rows": [],
                "groups": [{"name": "Thema", "rows": [
                    {"keyword": "zonnepanelen", "begin": 8, "end": 3, "change": 5,
                     "status": "improved", "landing_page": None},
                    {"keyword": "nieuw", "begin": 0, "end": 7, "change": 0,
                     "status": "new", "landing_page": None},
                ]}],
                "totals": {},
                "compare": None,
                "chart": None,
            },
        },
    }

    class _Report:
        title = "Maandrapport"
        company_name = "Acme B.V."

    context = ctx.build_context(
        report=_Report(), snapshot=snapshot, narrative={}, section_titles={},
        brand_name="Bureau", logo_uri=None, cover_uri=None, client_logo_uri=None,
        accent=None, intro_text=None, footer_text=None, locale="nl", internal=False,
    )
    html = ENGINE.render_html(context, {})
    # No column is headed VERSCHIL any more — the word survives only as the end-position
    # column's title attribute, which is where a reader hovering the preview learns what the
    # badge is.
    assert "Verschil</th>" not in html
    # Whitespace-insensitive: the cell holds the number, then the badge, then nothing else.
    flat = " ".join(html.split())
    assert '1.240 <span class="badge up"><svg class="arrow"' in flat
    assert "+26,5%" in flat and "-5,0%" in flat
    # The rankings table: the end rank carries its move, a new term its status.
    assert "+5</span>" in flat
    assert "nieuw</span>" in flat


def test_the_model_still_reads_the_change_the_page_folded_away() -> None:
    """A paragraph that says "a quarter up on last year" needs the figure whichever cell
    draws it."""
    presented = present.section(_channels(), locale="nl", title="Kanalen")
    assert presented["rows"][0]["Verandering"] == "+26,5%"


def test_a_column_heading_is_the_short_name_and_the_tile_keeps_the_long_one() -> None:
    """Said once, in the place with room for it — not abbreviated everywhere."""
    assert ctx.metric_short("keyEvents", "nl") == "Doelen"
    assert ctx.metric_label("keyEvents", "nl") == "Belangrijke gebeurtenissen"
    # And the long name is what a *tile* gets — the point being that a short form narrows the
    # heading without hiding the metric's name anywhere it has room.
    assert ctx.metric_short("keywords_tracked", "nl") == "Gevolgd"
    assert ctx.metric_label("keywords_tracked", "nl") == "Gevolgde zoekwoorden"
    # A metric with no short form falls back rather than printing a message key. `top3` is
    # already two characters and a digit, which is the case that needs no short form at all.
    assert ctx.metric_short("top3", "nl") == ctx.metric_label("top3", "nl") == "Top 3"


def test_a_metric_glyph_is_inline_svg_or_nothing() -> None:
    """An invented mark is worse than a bare heading: a reader will try to learn it."""
    assert "<svg" in ctx.metric_icon("sessions")
    assert ctx.metric_icon("a_metric_nobody_has_drawn") == ""


def test_a_tile_strip_is_balanced_and_padded_to_a_rectangle() -> None:
    """Four figures are four quarters, not three and one full-bleed leftover.

    Search Console's strip is exactly four, and at ``flex: 1 1 22%`` it wrapped 3 + 1 with the
    fourth tile stretched across the page — which reads as a rendering fault, not a layout.
    """
    tiles = [{"key": str(index)} for index in range(6)]
    rows = ctx.tile_rows(tiles)
    assert [len(row) for row in rows] == [3, 3]
    # Five balance 3 + 2, and the short row is padded so every tile is the same width.
    rows = ctx.tile_rows(tiles[:5])
    assert [len(row) for row in rows] == [3, 3]
    assert rows[1][-1] is None
    assert ctx.tile_rows([]) == []


# --------------------------------------------------------------------------------------- #
# What the table says
# --------------------------------------------------------------------------------------- #
def test_a_column_that_is_zero_on_every_row_is_not_printed() -> None:
    shaped = ctx.shape_section(_referrals(0), "nl")
    assert "keyEvents" not in shaped["columns"]
    # A single non-zero cell is this period's news and keeps its column.
    data = _referrals(0)
    data["rows"][0]["keyEvents"] = 2
    assert "keyEvents" in ctx.shape_section(data, "nl")["columns"]


def test_a_client_table_drops_the_columns_a_client_does_not_read() -> None:
    """Seven metric columns per referring domain is the marketeer's table on the client's desk."""
    client = ctx.shape_section(_referrals(0), "nl")["columns"]
    assert client == ["sessions", "totalUsers", "avg_engagement_time"]
    # The internal analysis keeps the full set — minus the all-zero one, which nobody wants.
    internal = ctx.shape_section(_referrals(0), "nl", internal=True)["columns"]
    assert internal == SEVEN[:-1]


def test_the_long_tail_folds_into_a_row_that_says_how_big_it_is() -> None:
    """Twelve one-session referrers are not twelve facts; they are one, and it is countable."""
    shaped = ctx.shape_section(_referrals(12), "nl")
    labels = [row["label"] for row in shaped["rows"]]
    assert labels[:4] == [
        "mail.google.com", "l.wl.co", "startgoogle.startpagina.nl", "garagebaas.nl"
    ]
    # Four real sources, then one line for the twelve. Not "four, one arbitrary one-session
    # referrer, then a line for the other eleven" — a rule that shows at least N rows must not
    # promote a piece of the noise it is folding.
    assert len(shaped["rows"]) == 5
    folded = shaped["rows"][-1]
    assert folded["folded"] is True
    assert "12" in folded["label"]
    # Summable metrics are summed; an average over twelve sources is not invented.
    assert folded["sessions"] == 12
    assert "avg_engagement_time" not in folded


def test_a_short_table_is_never_folded() -> None:
    """A table of three rows and a "plus two others" line is not a table."""
    shaped = ctx.shape_section(_referrals(1), "nl")
    assert not any(row.get("folded") for row in shaped["rows"])


def test_a_table_that_is_all_tail_still_shows_rows() -> None:
    """The degenerate case: traffic so evenly spread that the floor eats everything."""
    data = _referrals(0)
    data["rows"] = [
        {"label": f"tiny{index}.example", "sessions": 1, "totalUsers": 1,
         "avg_engagement_time": 1.0, "newUsers": 0, "screenPageViews": 1,
         "engagementRate": 0.0, "keyEvents": 0}
        for index in range(9)
    ]
    shaped = ctx.shape_section(data, "nl")
    kept = [row for row in shaped["rows"] if not row.get("folded")]
    assert len(kept) == 3
    assert shaped["rows"][-1]["folded"] is True


def test_a_closed_vocabulary_is_never_folded() -> None:
    """Channels are Google's twelve and conversions are the client's own goals — folding either
    would hide a choice somebody made rather than a tail nobody chose."""
    channels = {
        "kind": "channels",
        "columns": ["sessions"],
        "rows": [{"label": f"Channel {i}", "sessions": 1} for i in range(14)],
        "totals": {},
        "compare": None,
        "chart": None,
    }
    assert len(ctx.shape_section(channels, "nl")["rows"]) == 14


def test_a_ga4_event_name_reaches_the_client_in_words() -> None:
    """`bedankt_offerte_aanvragen` is a developer's identifier, printed on somebody's report."""
    data = {
        "kind": "conversions",
        "columns": ["keyEvents"],
        "rows": [
            {"label": "bedankt_offerte_aanvragen", "keyEvents": 14},
            {"label": "Telefoon GA4", "keyEvents": 80},
            {"label": "mail.google.com", "keyEvents": 3},
        ],
        "totals": {},
        "compare": None,
        "chart": {"type": "grouped", "labels": ["bedankt_offerte_aanvragen"], "series": []},
    }
    shaped = ctx.shape_section(data, "nl")
    assert [row["label"] for row in shaped["rows"]] == [
        "Bedankt offerte aanvragen",
        # A name somebody wrote is not ours to restyle, and a dotted name is an address.
        "Telefoon GA4",
        "mail.google.com",
    ]
    # The chart is renamed with the table, or the picture beside the rows disagrees with them.
    assert shaped["chart"]["labels"] == ["Bedankt offerte aanvragen"]


def test_two_tiles_never_show_the_same_number_under_two_names() -> None:
    """GA4 answers `keyEvents` and `conversions` with the same figure for nearly every property,
    so every report ever generated printed it twice with the same delta beneath it."""
    data = {
        "kind": "channels",
        "columns": ["sessions"],
        "rows": [],
        "totals": {"sessions": 4124, "keyEvents": 879, "conversions": 879},
        "compare": {"sessions": 2515, "keyEvents": 591, "conversions": 591},
        "chart": None,
    }
    tiles = ctx._tiles(data, "nl", None)
    assert [tile["key"] for tile in tiles] == ["sessions", "keyEvents"]

    # Two metrics that happen to be equal this month but moved differently are two facts.
    data["compare"] = {"sessions": 2515, "keyEvents": 591, "conversions": 800}
    assert len(ctx._tiles(data, "nl", None)) == 3


def test_a_strip_of_figures_is_ordered_by_the_document_not_by_postgres() -> None:
    """``data_snapshot`` is JSONB, and JSONB has no key order — it sorts by length, then bytes.

    A section provider builds its totals in the source's own display order and that order
    survives exactly as far as the first commit. What every report actually printed was
    *NIEUWE GEBRUIKERS · SESSIES · BELANGRIJKE GEBEURTENISSEN · GEBRUIKERS* — an ordering with
    no meaning, on the strip that is the first thing a client reads.

    Invisible in an offline render, because a Python dict *does* keep insertion order: only a
    document read back from the database shows it. So the fixture below is deliberately in
    Postgres' order rather than in a sensible one.
    """
    stored = {
        "newUsers": 2810.0,
        "sessions": 4124.0,
        "keyEvents": 879.0,
        "totalUsers": 3781.0,
        "engagementRate": 0.46,
        # A metric no order names is appended, never dropped into the middle of the strip.
        "somethingNew": 7.0,
    }
    assert [metric for metric, _ in ctx.ordered_metrics(stored)] == [
        "sessions", "totalUsers", "newUsers", "keyEvents", "engagementRate", "somethingNew"
    ]


def test_the_cover_leads_with_the_first_section_that_has_figures() -> None:
    sections = [
        {"totals": []},
        {"totals": [{"key": str(index)} for index in range(6)]},
    ]
    assert len(ctx._headline(sections)) == 4
    assert ctx._headline([{"totals": []}]) == []


def test_a_rankings_row_is_coloured_by_its_move_not_by_its_rank() -> None:
    """A term parked at 22 all year earned a red cell every month for standing still."""
    rows = ctx._ranked(
        [
            {"keyword": "a", "begin": 41, "end": 38, "change": 3, "status": "improved"},
            {"keyword": "b", "begin": 19, "end": 22, "change": -3, "status": "declined"},
            {"keyword": "c", "begin": 7, "end": 7, "change": 0, "status": "stable"},
            {"keyword": "d", "begin": 0, "end": 27, "change": 0, "status": "new"},
        ]
    )
    assert [row["move_class"] for row in rows] == ["up", "down", "", "up"]


# --------------------------------------------------------------------------------------- #
# The model reads the page, not the database
# --------------------------------------------------------------------------------------- #
def test_the_model_is_handed_the_table_the_document_prints() -> None:
    """One shaping function, so a paragraph cannot describe a column the table dropped or name
    thirteen referrers the document folded into one line."""
    presented = present.section(_referrals(12), locale="nl", title="Verwijzend verkeer")
    rows = presented["rows"]
    # The folded line is in the model's copy too, and it says how many it stands for.
    assert any("12" in str(row.get("Bron", "")) for row in rows)
    # …and the dropped columns are not.
    assert not any("Nieuwe gebruikers" in row for row in rows)
    assert not any("Belangrijke gebeurtenissen" in row for row in rows)


# --------------------------------------------------------------------------------------- #
# A client with two websites (#381)
# --------------------------------------------------------------------------------------- #
def _block(label: str, sessions: float) -> dict:
    return {
        "label": label,
        "columns": ["sessions"],
        "rows": [{"label": "Organic Search", "sessions": sessions}],
        "totals": {"sessions": sessions},
        "compare": None,
        "chart": None,
    }


def _two_website_snapshot() -> dict:
    first = _block("aaprotec.nl", 4124.0)
    second = _block("opentjewereld.nl", 3910.0)
    return {
        "order": ["marketing.traffic_channels"],
        "period": {"label": "juli 2026"},
        "compare": {"label": "juli 2025"},
        "company": {"name": "AAproTec B.V."},
        "sections": {
            "marketing.traffic_channels": {
                "kind": "channels",
                "parts": [first, second],
                **{key: value for key, value in first.items() if key != "label"},
            }
        },
    }


def _built(snapshot: dict) -> dict:
    from types import SimpleNamespace

    return ctx.build_context(
        report=SimpleNamespace(title="Maandrapportage", company_name="AAproTec B.V."),
        snapshot=snapshot,
        narrative={},
        section_titles={"marketing.traffic_channels": "Verkeerskanalen"},
        brand_name="breik.",
        logo_uri=None,
        cover_uri=None,
        client_logo_uri=None,
        accent="#b8860b",
        intro_text=None,
        footer_text=None,
        locale="nl",
        internal=False,
    )


def test_each_website_gets_its_own_block_with_its_own_geometry() -> None:
    """Two properties, two tables, each named — and each with its *own* tiles and widths.

    Sharing one set of column widths across two blocks would be the same class of fault the
    section-level geometry rule already fixed: a width decided by one table and imposed on
    another is a width that is right for neither.
    """
    section = _built(_two_website_snapshot())["sections"][0]

    assert [part["label"] for part in section["parts"]] == ["aaprotec.nl", "opentjewereld.nl"]
    assert section["parts"][0]["totals"] != section["parts"][1]["totals"]
    # …and the section still carries the first block flat, so a tenant's own design renders.
    assert section["rows"] == section["parts"][0]["rows"]


def test_one_website_carries_no_name_to_print() -> None:
    """An empty label is the renderer's instruction not to draw a sub-heading, and it is what a
    client with one property — which is nearly all of them — has."""
    snapshot = _two_website_snapshot()
    section_data = snapshot["sections"]["marketing.traffic_channels"]
    section_data["parts"] = [{**section_data["parts"][0], "label": ""}]

    section = _built(snapshot)["sections"][0]

    assert [part["label"] for part in section["parts"]] == [""]


def test_a_report_stored_before_this_existed_still_renders() -> None:
    """A snapshot is frozen so that a report reopened next December shows what it showed today.
    Every report already in the database predates `parts`, and is its own single block."""
    snapshot = _two_website_snapshot()
    snapshot["sections"]["marketing.traffic_channels"].pop("parts")

    section = _built(snapshot)["sections"][0]

    assert len(section["parts"]) == 1
    assert section["parts"][0]["rows"] == section["rows"]


def test_the_model_reads_each_website_under_its_own_name() -> None:
    """Otherwise a paragraph averages two businesses into one sentence, which is the failure the
    document split exists to prevent — restated one layer along, in prose."""
    data = _two_website_snapshot()["sections"]["marketing.traffic_channels"]

    out = present.section(data, locale="nl", title="Verkeerskanalen")

    assert [entry["website"] for entry in out["websites"]] == [
        "aaprotec.nl",
        "opentjewereld.nl",
    ]
    assert "rows" not in out
