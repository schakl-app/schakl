"""Keyword positions, and who decides where they come from (issue #373).

Before this, ``marketing.rankings`` was produced from **SE Ranking and nothing else**. A client
without that subscription got no keyword section at all — silently, with nothing on the document
or the review screen to say one had been withheld — while Search Console, connected for
practically every client, answered the question directly and was never asked.

Two halves are tested here and they are deliberately separate:

* the **adapter**, against the shape Search Console actually returns, with no network;
* the **preference**, which is the only thing that decides what a run does, and which three
  surfaces read (the gatherer, the settings screen and the section catalog) — so it has to give
  one answer or a screen will promise a section the run then drops.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.modules.marketing.rankings import (
    RankingSettings,
    RankingSource,
    effective_source,
    parse,
    resolve,
)
from app.modules.marketing.sources.gsc import GSCAdapter

pytestmark = pytest.mark.anyio

ADAPTER = GSCAdapter()


class _Response:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        return None


class _Client:
    """Answers `searchAnalytics/query` from a queue — one entry per call, in order."""

    def __init__(self, *pages: list[dict]) -> None:
        self.pages = list(pages)
        self.bodies: list[dict] = []

    async def post(self, url: str, json: dict) -> _Response:  # noqa: A002, ARG002
        self.bodies.append(json)
        rows = self.pages.pop(0) if self.pages else []
        return _Response({"rows": rows})


def _row(query: str, position: float, impressions: float, clicks: float = 0.0) -> dict:
    """One row in the shape Search Console v3 returns for a `query` dimension."""
    return {
        "keys": [query],
        "clicks": clicks,
        "impressions": impressions,
        "ctr": (clicks / impressions) if impressions else 0.0,
        "position": position,
    }


NOW = [
    _row("airco installatie zeeland", 6.2, 480, 31),
    _row("warmtepomp installateur", 3.4, 1200, 96),
    _row("ventilatiesysteem vervangen", 18.1, 210, 2),
    # Two impressions and an average position of 3 — arithmetically true and meaningless.
    _row("gratis airco", 3.0, 2, 0),
    # Past the visible depth at both ends — nothing a client can act on this month.
    _row("airco kopen amsterdam", 87.4, 300, 0),
]
BEFORE = [
    _row("airco installatie zeeland", 11.8, 400, 12),
    _row("warmtepomp installateur", 3.1, 1100, 90),
    _row("ventilatiesysteem vervangen", 14.0, 190, 3),
]


# --------------------------------------------------------------------------------------- #
# The adapter
# --------------------------------------------------------------------------------------- #
async def test_search_console_answers_the_shape_the_rankings_section_expects() -> None:
    """Same payload as SE Ranking's rows, so the section, the design and the model need to know
    nothing about where a ranking came from."""
    client = _Client(NOW, BEFORE)
    rows = await ADAPTER.keyword_rows(
        client,  # type: ignore[arg-type]
        "sc-domain:example.nl",
        date(2026, 7, 1),
        date(2026, 7, 31),
        date(2025, 7, 1),
        date(2025, 7, 31),
    )
    by_keyword = {row["keyword"]: row for row in rows}

    climbed = by_keyword["airco installatie zeeland"]
    assert (climbed["begin"], climbed["end"]) == (12, 6)
    # Positive = climbed, matching SE Ranking's convention: rank 12 → 6 is +6 even though the
    # number fell. Getting this backwards prints a green badge on every decline.
    assert climbed["change"] == 6
    assert climbed["status"] == "improved"

    slipped = by_keyword["ventilatiesysteem vervangen"]
    assert slipped["change"] == -4
    assert slipped["status"] == "declined"

    # Search Console knows what was searched, not which page answered it. A column of dashes is
    # worse than no column, so the row says so rather than inventing one.
    assert climbed["landing_page"] is None
    assert climbed["group"] == ""
    # "Volume" is impressions — what Search Console can actually observe — not a keyword tool's
    # monthly search volume, and it is not labelled as if it were.
    assert climbed["volume"] == 480

    assert "gratis airco" not in by_keyword, "two impressions is not a ranking"
    assert "airco kopen amsterdam" not in by_keyword, "past the visible depth"

    # Best first: a client reads the top of the table and stops.
    assert [row["keyword"] for row in rows][0] == "warmtepomp installateur"

    # Two calls, one per period, and both named the query dimension.
    assert len(client.bodies) == 2
    assert all(body["dimensions"] == ["query"] for body in client.bodies)


async def test_a_keyword_with_no_history_is_new_rather_than_a_fall_from_zero() -> None:
    """`begin = 0` is "we had never seen it", which is not position zero — and computing a
    change against it would report every new term as a catastrophic decline."""
    client = _Client([_row("nieuwe term", 14.0, 300, 5)], [])
    rows = await ADAPTER.keyword_rows(
        client,  # type: ignore[arg-type]
        "sc-domain:example.nl",
        date(2026, 7, 1),
        date(2026, 7, 31),
        date(2025, 7, 1),
        date(2025, 7, 31),
    )
    assert rows[0]["status"] == "new"
    assert rows[0]["change"] == 0


async def test_with_no_comparison_window_the_adapter_asks_once() -> None:
    client = _Client(NOW)
    rows = await ADAPTER.keyword_rows(
        client, "sc-domain:example.nl", date(2026, 7, 1), date(2026, 7, 31)  # type: ignore[arg-type]
    )
    assert len(client.bodies) == 1
    assert all(row["status"] == "new" for row in rows)


async def test_the_settings_narrow_the_table_rather_than_the_document_doing_it() -> None:
    client = _Client(NOW, BEFORE)
    rows = await ADAPTER.keyword_rows(
        client,  # type: ignore[arg-type]
        "sc-domain:example.nl",
        date(2026, 7, 1),
        date(2026, 7, 31),
        limit=2,
        min_impressions=250,
        max_position=10,
    )
    assert len(rows) <= 2
    assert all(row["volume"] >= 250 and row["end"] <= 10 for row in rows)


# --------------------------------------------------------------------------------------- #
# The preference
# --------------------------------------------------------------------------------------- #
def test_auto_prefers_se_ranking_and_falls_back_to_search_console() -> None:
    """The default, and the only value that is right for a mixed client list without anyone
    having to visit a screen."""
    settings = RankingSettings()
    assert settings.source is RankingSource.AUTO
    assert (
        effective_source(settings, has_seranking=True, has_search_console=True)
        is RankingSource.SERANKING
    )
    assert (
        effective_source(settings, has_seranking=False, has_search_console=True)
        is RankingSource.SEARCH_CONSOLE
    )
    assert effective_source(settings, has_seranking=False, has_search_console=False) is None


def test_a_named_source_is_not_silently_substituted() -> None:
    """An agency that said "Search Console" and gets SE Ranking has two months of reports that
    are not comparable and nothing on the page saying why."""
    named = RankingSettings(source=RankingSource.SEARCH_CONSOLE)
    assert effective_source(named, has_seranking=True, has_search_console=False) is None
    assert (
        effective_source(named, has_seranking=True, has_search_console=True)
        is RankingSource.SEARCH_CONSOLE
    )
    off = RankingSettings(source=RankingSource.OFF)
    assert effective_source(off, has_seranking=True, has_search_console=True) is None


def test_a_clients_settings_are_a_diff_over_the_house_rule() -> None:
    """Raising the house limit reaches every client who never set one — the whole reason the
    per-client row is a diff and not a snapshot."""
    house = {"limit": 40, "min_impressions": 25, "source": "search_console"}
    own = {"limit": 10}
    merged = resolve(house, own)
    assert merged.limit == 10
    assert merged.min_impressions == 25
    assert merged.source is RankingSource.SEARCH_CONSOLE
    # Nothing stored anywhere is the code default, not a form of blanks.
    assert resolve(None, None) == RankingSettings()


def test_a_stored_value_a_screen_could_never_produce_is_clamped_not_obeyed() -> None:
    """A settings blob is JSON in a column: an old release, a hand-edited row or a bad import
    can all put a 5000-row limit in it, and a report is a page of a PDF."""
    parsed = parse({"limit": 5000, "max_position": 0, "min_impressions": -3, "source": "magic"})
    assert parsed.limit == 200
    assert parsed.max_position == 3
    assert parsed.min_impressions == 0
    assert parsed.source is RankingSource.AUTO
