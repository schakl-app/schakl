"""The "Zoekmachines" section, which had that name and answered a different question (#381).

It was GA4's ``organic_sources`` split. On a Dutch client that is one row reading ``google``
and a pie chart with a single slice — and on a real July report it read *google · 14 sessies*
against 511 Search Console clicks in the same month, because the gatherer had picked one of the
client's two GA4 properties for its live calls and the other for its totals.

Where a client has a rank tracker the section now answers *waar sta ik, per zoekmachine*, which
is a question Google Analytics structurally cannot answer: it knows which engine sent a session
and nothing about a position. Where they have none, the organic split stays — for that client
it is the honest reading of the same heading.

The shapes below are trimmed from live responses (``/positions``,
``/sites/{id}/search-engines``, ``/system/search-engines``), because two of the three things
this code has to get right are things a plausible implementation gets wrong silently:

* ``/positions`` answers **per engine** and the key is ``site_engine_id`` — *this project's*
  engine row, which is 1104694 where the catalogue that names it stops at 889;
* the catalogue's ``id`` is a **string** and the project row's ``search_engine_id`` is an
  **int**, so a lookup that does not go through ``str()`` matches nothing and every engine
  prints as a number.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import pytest

from app.modules.marketing.report_sections import (
    _CACHE_ATTR,
    GatheredMarketing,
    Part,
    _search_engines,
)
from app.modules.marketing.sources import seranking as se
from app.modules.marketing.sources.seranking import SeRankingAdapter
from app.registry import ReportWindow

pytestmark = pytest.mark.anyio

ADAPTER = SeRankingAdapter()


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
        self.calls: list[str] = []

    async def get(self, url: str, params: dict | None = None) -> _Response:  # noqa: ARG002
        self.calls.append(url)
        for suffix, payload in self.routes.items():
            if suffix in url:
                return _Response(payload)
        return _Response({}, status=404)


def _term(name: str, begin: int, end: int) -> dict[str, Any]:
    return {
        "id": name,
        "name": name,
        "group_id": 1,
        "positions": [
            {"date": "2026-07-01", "pos": begin},
            {"date": "2026-07-31", "pos": end},
        ],
    }


#: Two engines, so the per-engine split is actually load-bearing — the failure this pins is a
#: parser that flattens and reports one engine holding everybody's keywords.
POSITIONS = {
    "data": [
        {
            "site_engine_id": 1104694,
            "keywords": [
                _term("rolhek winkel", 9, 2),
                _term("inbraakwerende rolhekken", 4, 3),
                _term("automatische deur", 30, 12),
                # Tracked, never seen. Counts as tracked and in nothing else.
                _term("snelloopdeur prijs", 0, 0),
            ],
        },
        {
            "site_engine_id": 1104695,
            "keywords": [_term("rolhek winkel", 40, 28)],
        },
    ]
}

SITE_ENGINES = [
    {"site_engine_id": 1104694, "search_engine_id": 320, "lang_code": "nl", "keyword_count": 4},
    {"site_engine_id": 1104695, "search_engine_id": 215, "lang_code": "nl", "keyword_count": 1},
]

CATALOGUE = [
    {"id": "320", "name": "Google Netherlands", "region_id": "120", "type": "google"},
    {"id": "215", "name": "Google Belgium", "region_id": "17", "type": "google"},
]


@pytest.fixture(autouse=True)
def _no_catalogue_carryover() -> Any:
    """The catalogue is cached process-wide on purpose; a test must not inherit another's."""
    se._ENGINE_CATALOGUE, se._ENGINE_CATALOGUE_AT = None, 0.0
    yield
    se._ENGINE_CATALOGUE, se._ENGINE_CATALOGUE_AT = None, 0.0


def _client() -> _Client:
    return _Client(
        {
            "/positions": POSITIONS,
            f"/sites/{123}/search-engines": SITE_ENGINES,
            "/system/search-engines": CATALOGUE,
        }
    )


async def test_a_project_with_two_engines_answers_with_two_rows() -> None:
    """The per-engine envelope, kept rather than flattened.

    ``_keyword_entries`` exists because reading ``body["keywords"]`` finds nothing; this is the
    same trap one level along — reading every engine's keywords into one bag answers "145
    tracked" for a client tracking two engines and tells them nothing about either.
    """
    rows = await ADAPTER.engine_rows(
        _client(),  # type: ignore[arg-type]
        "123",
        date(2026, 7, 1),
        date(2026, 7, 31),
    )

    assert [row["label"] for row in rows] == ["Google Netherlands", "Google Belgium"]
    assert [row["keywords_tracked"] for row in rows] == [4.0, 1.0]


async def test_a_term_that_ranks_nowhere_is_tracked_and_counted_nowhere_else() -> None:
    """``pos == 0`` is *not ranking*, not "position zero" — this adapter's founding trap.

    Counting it would put an invisible keyword in the top three and drag the average toward
    perfection, which is the one arithmetic error a client would notice.
    """
    rows = await ADAPTER.engine_rows(
        _client(),  # type: ignore[arg-type]
        "123",
        date(2026, 7, 1),
        date(2026, 7, 31),
    )
    google = rows[0]

    assert google["keywords_tracked"] == 4.0
    assert google["keywords_ranking"] == 3.0
    assert google["top3"] == 2.0
    assert google["top10"] == 2.0
    assert google["top30"] == 3.0
    # (2 + 3 + 12) / 3, and not (0 + 2 + 3 + 12) / 4.
    assert google["avg_position"] == pytest.approx(5.7, abs=0.05)


async def test_the_move_reads_the_way_the_keyword_table_reads() -> None:
    """Positive is better, on both tables in one document.

    Google NL began the month averaging (9 + 4 + 30) / 3 = 14,3 and ended on 5,7 — a rise of
    roughly nine places. Printing that as -8,6 because the number got smaller would put two
    conventions in one report, which is worse than either.
    """
    rows = await ADAPTER.engine_rows(
        _client(),  # type: ignore[arg-type]
        "123",
        date(2026, 7, 1),
        date(2026, 7, 31),
    )

    assert rows[0]["change"] == pytest.approx(8.6, abs=0.05)


async def test_an_engine_the_catalogue_cannot_name_prints_its_id_not_a_guess() -> None:
    """Never an invented "Google": a project tracking Bing would then print a row naming the
    wrong search engine, which is worse than an unhelpful one."""
    client = _Client({"/positions": POSITIONS, "/sites/123/search-engines": SITE_ENGINES})

    rows = await ADAPTER.engine_rows(
        client,  # type: ignore[arg-type]
        "123",
        date(2026, 7, 1),
        date(2026, 7, 31),
    )

    assert [row["label"] for row in rows] == ["#1104694", "#1104695"]


async def test_the_catalogue_is_fetched_once_across_reports() -> None:
    """690 rows and ~52 KB of SE Ranking's own reference list, unchanged between tenants — so a
    nightly batch of thirty clients downloads it once, not thirty times."""
    client = _client()

    await ADAPTER.engine_rows(client, "123", date(2026, 7, 1), date(2026, 7, 31))  # type: ignore[arg-type]
    await ADAPTER.engine_rows(client, "123", date(2026, 7, 1), date(2026, 7, 31))  # type: ignore[arg-type]

    assert sum(1 for url in client.calls if "/system/search-engines" in url) == 1


async def test_one_positions_read_serves_both_tables() -> None:
    """The keyword table and the per-engine summary are two views of one payload.

    At 145 keywords over a month it is the largest thing SE Ranking returns, and a report that
    asked for it twice would be paying for the same bytes to answer a question it had already
    been answered.
    """
    client = _client()

    body = await ADAPTER.positions_body(client, "123", date(2026, 7, 1), date(2026, 7, 31))  # type: ignore[arg-type]
    keywords = await ADAPTER.keyword_rows(
        client,  # type: ignore[arg-type]
        "123",
        date(2026, 7, 1),
        date(2026, 7, 31),
        body=body,
    )
    engines = await ADAPTER.engine_rows(
        client,  # type: ignore[arg-type]
        "123",
        date(2026, 7, 1),
        date(2026, 7, 31),
        body=body,
    )

    assert sum(1 for url in client.calls if "/positions" in url) == 1
    assert keywords and engines


# --------------------------------------------------------------------------------------- #
# Which of the two questions this client's section answers
# --------------------------------------------------------------------------------------- #
class _Ctx:
    """Seeding ``gather``'s own memo is what reaches a provider without a database."""


def _seeded(data: GatheredMarketing) -> tuple[_Ctx, ReportWindow]:
    window = ReportWindow(
        company_id=uuid.uuid4(),
        start=date(2026, 7, 1),
        end=date(2026, 7, 31),
        compare_start=date(2025, 7, 1),
        compare_end=date(2025, 7, 31),
    )
    ctx = _Ctx()
    setattr(
        ctx,
        _CACHE_ATTR,
        {(window.company_id, window.start, window.end, window.compare_start): data},
    )
    return ctx, window


async def test_a_client_with_a_rank_tracker_gets_positions_per_engine() -> None:
    ctx, window = _seeded(
        GatheredMarketing(
            engines=[{"label": "Google Netherlands", "keywords_tracked": 145.0, "top3": 21.0}]
        )
    )

    payload = await _search_engines(ctx, window)  # type: ignore[arg-type]

    assert payload is not None
    assert payload["kind"] == "engines"
    assert payload["rows"][0]["label"] == "Google Netherlands"
    # One engine is a rectangle, not a chart (#373: a picture nobody perceives as a picture is
    # a printing fault, not a design choice).
    assert payload["chart"] is None


async def test_a_client_without_one_still_gets_the_traffic_split() -> None:
    """The fallback answers a *different* question and that is the point: for a client with no
    rank tracker, "zoekmachines" honestly means which ones sent people."""
    ctx, window = _seeded(
        GatheredMarketing(
            parts={"ga4": [Part(key="ga4", label="", links=())]},
            live={
                "ga4": {
                    "organic_sources": {
                        "columns": ["sessions"],
                        "rows": [{"label": "google", "sessions": 512.0}],
                        "compare_rows": [{"label": "google", "sessions": 480.0}],
                    }
                }
            },
        )
    )

    payload = await _search_engines(ctx, window)  # type: ignore[arg-type]

    assert payload is not None
    assert payload["kind"] == "organic_sources"
    assert payload["rows"][0]["label"] == "google"


async def test_a_client_with_neither_gets_no_section() -> None:
    ctx, window = _seeded(GatheredMarketing())

    assert await _search_engines(ctx, window) is None  # type: ignore[arg-type]
