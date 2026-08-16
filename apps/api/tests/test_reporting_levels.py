"""What a client's report prints when a provider answers with a *level* (issue #381).

Found on a real July report before it was sent. Its warnings strip named three things; the two
worse faults were on the client's own page and named nothing:

* the rankings tiles read **4.495 gevolgde zoekwoorden · 2.782 scorend · 639 in top 3** over a
  project tracking 145 terms, because a thirty-one day period had *summed* a daily level;
* the keyword table printed 25 of the 68 terms the same tiles had just counted.

Both are invisible to every test that asserts a section is produced, and to every eye that has
not divided by the length of the month. So they are pinned by arithmetic here.

The third fault is the shape of a refusal: SE Ranking answers three independent questions on one
credential, and a **401 from the AI Result Tracker** — which is what a project without that
product gets, permanently — was reported as the whole source being unreachable.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import pytest

from app.modules.marketing.rankings import RankingSettings, RankingSource
from app.modules.marketing.report_sections import (
    _CACHE_ATTR,
    GatheredMarketing,
    _rankings,
    _seranking_part,
)
from app.modules.marketing.service import aggregate
from app.modules.marketing.sources.base import AVERAGED_METRICS, SERANKING_METRICS
from app.modules.marketing.sources.seranking import SeRankingAdapter
from app.registry import ReportWindow

pytestmark = pytest.mark.anyio

ADAPTER = SeRankingAdapter()


# --------------------------------------------------------------------------------------- #
# A level is not a total
# --------------------------------------------------------------------------------------- #
def test_every_se_ranking_metric_is_a_level_and_says_so() -> None:
    """Not one of the six accumulates over a period, so not one may be summed.

    Written as a sweep over ``SERANKING_METRICS`` rather than as five names, because the way
    this bug arrived was somebody adding four counters beside ``avg_position`` — which was
    already registered, and whose docstring already explained the trap — and not repeating the
    registration. A seventh metric added tomorrow fails here rather than in a client's PDF.
    """
    assert set(SERANKING_METRICS) <= AVERAGED_METRICS


def test_a_month_of_tracked_keywords_is_not_thirty_one_months_of_them() -> None:
    """The exact arithmetic that put 4.495 on a client's document.

    SE Ranking stores one row per day carrying the project's *current* counts. July has 31 of
    them; the project tracks 145 keywords on every one. A sum answers 4.495, which is not a
    number about anything.
    """
    days = 31
    rows = [
        {
            "avg_position": 19.4,
            "top3": 21.0,
            "top10": 36.0,
            "top30": 67.0,
            "keywords_ranking": 90.0,
            "keywords_tracked": 145.0,
        }
        for _ in range(days)
    ]

    totals = aggregate("seranking", rows)

    assert totals["keywords_tracked"] == 145.0
    assert totals["keywords_ranking"] == 90.0
    assert totals["top3"] == 21.0
    assert totals["top10"] == 36.0
    assert totals["top30"] == 67.0
    assert totals["avg_position"] == 19.4


def test_a_level_that_moves_over_the_month_averages_rather_than_freezing() -> None:
    """An average, not the last value — a period figure describes the period.

    The alternative (take the final day) is defensible and is *not* what the aggregator can do:
    it is handed a bag of metrics dicts with no dates on them, ordered by whatever Postgres
    returned. Averaging needs no order, which is the property that makes it correct here rather
    than merely convenient.
    """
    rows = [{"top3": 10.0}, {"top3": 20.0}, {"top3": 30.0}]

    assert aggregate("seranking", rows)["top3"] == 20.0


# --------------------------------------------------------------------------------------- #
# The visible depth is the client's setting, on both sources
# --------------------------------------------------------------------------------------- #
class _Response:
    def __init__(self, payload: object, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def json(self) -> object:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Client:
    def __init__(self, routes: dict[str, object]) -> None:
        self.routes = routes

    async def get(self, url: str, params: dict | None = None) -> _Response:  # noqa: ARG002
        for suffix, payload in self.routes.items():
            if suffix in url:
                return _Response(payload)
        return _Response({}, status=404)


def _keyword(name: str, position: int) -> dict[str, Any]:
    """One tracked term, flat across the month at ``position``."""
    return {
        "id": name,
        "name": name,
        "group_id": 1,
        "volume": 10,
        "positions": [
            {"date": "2026-07-01", "pos": position},
            {"date": "2026-07-31", "pos": position},
        ],
        "landing_pages": [],
    }


_DEPTH_POSITIONS = {
    "data": [
        {
            "site_engine_id": 1,
            "keywords": [
                _keyword("dichtbij", 4),
                _keyword("net binnen", 25),
                _keyword("net buiten", 26),
                _keyword("ver weg", 61),
            ],
        }
    ]
}


async def test_se_ranking_draws_the_visible_line_where_the_client_asked() -> None:
    """``max_position`` is one control on one screen and it used to mean two things (#381).

    The Search Console adapter was handed the setting; SE Ranking hardcoded 25. So an agency
    raising the depth to 60 for a client saw it apply or not depending on which integration they
    happened to hold, with nothing on the screen saying which.
    """
    client = _Client({"/positions": _DEPTH_POSITIONS, "keyword-groups": {}})

    default = await ADAPTER.keyword_rows(
        client,  # type: ignore[arg-type]
        "123",
        date(2026, 7, 1),
        date(2026, 7, 31),
    )
    deeper = await ADAPTER.keyword_rows(
        client,  # type: ignore[arg-type]
        "123",
        date(2026, 7, 1),
        date(2026, 7, 31),
        max_position=60,
    )

    assert [row["keyword"] for row in default] == ["dichtbij", "net binnen"]
    assert [row["keyword"] for row in deeper] == ["dichtbij", "net binnen", "net buiten"]


# --------------------------------------------------------------------------------------- #
# The table prints what the tiles counted
# --------------------------------------------------------------------------------------- #
class _Ctx:
    """Enough of a context to reach a section provider through its own memo.

    ``gather`` caches on an attribute of the context object, so seeding the cache is what lets a
    provider be exercised for what it *decides* without a database, a Google session or an SE
    Ranking key. It is the section's own seam, used the way it was built.
    """


def _seeded(data: GatheredMarketing, window: ReportWindow) -> _Ctx:
    ctx = _Ctx()
    key = (window.company_id, window.start, window.end, window.compare_start)
    setattr(ctx, _CACHE_ATTR, {key: data})
    return ctx


def _window() -> ReportWindow:
    return ReportWindow(
        company_id=uuid.uuid4(),
        start=date(2026, 7, 1),
        end=date(2026, 7, 31),
        compare_start=date(2025, 7, 1),
        compare_end=date(2025, 7, 31),
    )


def _rows(count: int) -> list[dict[str, Any]]:
    return [
        {
            "keyword": f"term {n}",
            "group": "Algemeen",
            "begin": 5,
            "end": 4,
            "change": 1,
            "status": "improved",
            "landing_page": None,
            "volume": 10,
        }
        for n in range(count)
    ]


async def test_a_curated_keyword_list_prints_whole() -> None:
    """68 tracked terms, a house limit of 25, and no truncation.

    ``limit`` exists because a Search Console property answers with every phrase it was ever
    shown for and a report has to pick a slice. An SE Ranking project holds the terms somebody
    sat down and chose; cutting those is not editing for length, and it left the table
    disagreeing with the summary above it.
    """
    window = _window()
    data = GatheredMarketing(
        keywords=_rows(68),
        keyword_source=RankingSource.SERANKING,
        ranking_settings=RankingSettings(limit=25),
    )

    payload = await _rankings(_seeded(data, window), window)  # type: ignore[arg-type]

    assert payload is not None
    assert len(payload["rows"]) == 68
    assert not [note for note in data.notes if note["code"].endswith("truncated")]


async def test_search_console_still_takes_the_slice_it_needs() -> None:
    """The other half of the same rule: an unbounded term list is still capped, and still says
    so on the run's warnings (§17 — a cap that truncates reports it)."""
    window = _window()
    data = GatheredMarketing(
        keywords=_rows(400),
        keyword_source=RankingSource.SEARCH_CONSOLE,
        ranking_settings=RankingSettings(limit=25),
    )

    payload = await _rankings(_seeded(data, window), window)  # type: ignore[arg-type]

    assert payload is not None
    assert len(payload["rows"]) == 25
    assert {"code": "reporting.warning.truncated", "detail": "rankings:400"} in data.notes


# --------------------------------------------------------------------------------------- #
# A refusal names the part, not the credential
# --------------------------------------------------------------------------------------- #
class _Boom(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.response = type("R", (), {"status_code": status})()


async def _raises(status: int) -> Any:
    raise _Boom(status)


async def _answers(value: Any) -> Any:
    return value


async def test_a_refused_ai_tracker_costs_the_ai_section_and_nothing_else() -> None:
    """The 401 that read as an outage.

    SE Ranking's AI Result Tracker answers 401 for a project whose plan does not include it —
    not once, but every run, for ever. One ``try`` around all three questions turned that into
    *"Een gegevensbron was niet bereikbaar"* for a credential that had just answered two of
    them, and left the keyword table's survival to the order the calls happened to be written in.
    """
    out = GatheredMarketing()

    keywords = await _seranking_part(out, "rankings", _answers([{"keyword": "x"}]), default=[])
    ai = await _seranking_part(out, "ai", _raises(401), default=[])

    assert keywords == [{"keyword": "x"}]
    assert ai == []
    assert [note["code"] for note in out.notes] == [
        "reporting.warning.seranking_ai_unavailable"
    ]


async def test_an_outage_and_an_entitlement_are_different_sentences() -> None:
    """A 500 is worth retrying and a 403 never will be, so they do not share a message: telling
    an agency a source was unreachable sends them to re-issue a key that is working."""
    out = GatheredMarketing()

    await _seranking_part(out, "audit", _raises(503), default=None)
    await _seranking_part(out, "rankings", _raises(403), default=[])

    assert [note["code"] for note in out.notes] == [
        "reporting.warning.seranking_audit_failed",
        "reporting.warning.seranking_rankings_unavailable",
    ]
