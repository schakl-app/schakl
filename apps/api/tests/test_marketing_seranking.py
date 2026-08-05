"""The SE Ranking adapter (#300), against the shapes the live API actually returns.

Every fixture below is trimmed from a real response, which is the point: the three things this
adapter has to get right are all things a plausible implementation written from memory gets
wrong, and each one fails *silently* — as a clean site, an empty ranking table, or an average
that is quietly nonsense.

The n8n workflow this replaces got the audit one wrong. It reads ``sections[].checks[]``; the
API answers ``sections[].props{}``. Every field it slims out of that comes back ``undefined``,
so the analysis the marketeer reads was written from an audit containing nothing but a score
and a list of section names. ``test_audit_reads_the_shape_the_api_actually_returns`` is that
bug, pinned.
"""

from __future__ import annotations

from datetime import date

from app.modules.marketing.sources.seranking import (
    SeRankingAdapter,
    _keyword_entries,
    _rows,
    _status,
    previous_period,
)

ADAPTER = SeRankingAdapter()


class _Response:
    def __init__(self, payload: object, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status
        self.content = b"x"

    def json(self) -> object:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Client:
    """Routes by URL suffix — enough to drive the adapter, nothing more."""

    def __init__(self, routes: dict[str, object]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    async def get(self, url: str, params: dict | None = None) -> _Response:  # noqa: ARG002
        self.calls.append(url)
        for suffix, payload in self.routes.items():
            if suffix in url:
                return payload if isinstance(payload, _Response) else _Response(payload)
        return _Response({}, status=404)


# The per-engine envelope, verbatim in shape from GET /sites/{id}/positions.
_POSITIONS = {
    "data": [
        {
            "site_engine_id": 1070773,
            "keywords": [
                {
                    "id": "1",
                    "name": "zonnepanelen goes",
                    "group_id": 145829,
                    "volume": 320,
                    "positions": [
                        {"date": "2026-07-01", "pos": 8},
                        {"date": "2026-07-15", "pos": 5},
                        {"date": "2026-07-31", "pos": 3},
                    ],
                    "landing_pages": [
                        {"url": "https://x.nl/oud", "date": "2026-06-01"},
                        {"url": "https://x.nl/zonnepanelen", "date": "2026-07-20"},
                    ],
                },
                {
                    "id": "2",
                    "name": "nooit zichtbaar",
                    "group_id": 145829,
                    "positions": [
                        {"date": "2026-07-01", "pos": 0},
                        {"date": "2026-07-31", "pos": 0},
                    ],
                },
                {
                    "id": "3",
                    "name": "weggezakt",
                    "group_id": 999,
                    "positions": [
                        {"date": "2026-07-01", "pos": 4},
                        {"date": "2026-07-31", "pos": 0},
                    ],
                },
            ],
        }
    ]
}

_GROUPS = {"data": [{"id": "145829", "name": "Zonnepanelen"}, {"id": "999", "name": "Overig"}]}


def test_keyword_entries_survives_the_per_engine_envelope() -> None:
    """``/positions`` answers per search engine. Reading ``body["keywords"]`` finds nothing."""
    assert len(_keyword_entries(_POSITIONS)) == 3
    # A second tracked engine doubles the entries rather than being dropped.
    two = {"data": [_POSITIONS["data"][0], _POSITIONS["data"][0]]}
    assert len(_keyword_entries(two)) == 6
    # A flat shape still parses, so a future normalisation upstream cannot break this.
    assert len(_keyword_entries({"keywords": _POSITIONS["data"][0]["keywords"]})) == 3
    assert _keyword_entries({}) == []
    assert _keyword_entries("nonsense") == []


def test_rows_tolerates_every_envelope_the_api_uses() -> None:
    assert _rows({"data": [{"a": 1}]}) == [{"a": 1}]
    assert _rows({"items": [{"a": 1}]}) == [{"a": 1}]
    assert _rows([{"a": 1}]) == [{"a": 1}]
    assert _rows({"nope": 1}) == []


async def test_daily_aggregates_exclude_the_not_ranking_sentinel() -> None:
    """``pos: 0`` means *not in the results*, not "position zero".

    Averaging it in reports a better average the worse a client does, which is the single most
    misleading number this adapter could produce.
    """
    client = _Client({"/positions": _POSITIONS})
    daily = await ADAPTER.fetch_daily(
        client, "7457072", date(2026, 7, 1), date(2026, 7, 31), {}
    )
    first = next(d for d in daily if d.day == date(2026, 7, 1))
    # Ranking keywords on 1 July are 8 and 4 — average 6, not (8+0+4)/3 = 4.
    assert first.metrics["avg_position"] == 6.0
    assert first.metrics["keywords_tracked"] == 3
    assert first.metrics["keywords_ranking"] == 2
    assert first.metrics["top10"] == 2
    assert first.metrics["top3"] == 0

    last = next(d for d in daily if d.day == date(2026, 7, 31))
    assert last.metrics["avg_position"] == 3.0  # only "zonnepanelen goes" still ranks
    assert last.metrics["top3"] == 1
    assert last.metrics["keywords_ranking"] == 1


async def test_keyword_rows_report_movement_the_way_a_client_reads_it() -> None:
    """Rank 8 → 3 is an improvement of five places, even though the number went down."""
    client = _Client({"/positions": _POSITIONS, "/keyword-groups/": _GROUPS})
    rows = await ADAPTER.keyword_rows(client, "7457072", date(2026, 7, 1), date(2026, 7, 31))

    by_name = {row["keyword"]: row for row in rows}
    # Never visible at either end: nothing a client can act on, so it is not printed.
    assert "nooit zichtbaar" not in by_name
    assert set(by_name) == {"zonnepanelen goes", "weggezakt"}

    winner = by_name["zonnepanelen goes"]
    assert (winner["begin"], winner["end"], winner["change"]) == (8, 3, 5)
    assert winner["status"] == "improved"
    assert winner["group"] == "Zonnepanelen"  # group_id int 145829 ↔ group id str "145829"
    # The *latest* landing page, not the first one on file.
    assert winner["landing_page"] == "https://x.nl/zonnepanelen"

    lost = by_name["weggezakt"]
    assert lost["status"] == "dropped"
    assert lost["change"] == 0  # no honest delta against "not present"


def test_status_names_every_way_a_keyword_can_move() -> None:
    assert _status(8, 3) == "improved"
    assert _status(3, 8) == "declined"
    assert _status(5, 5) == "unchanged"
    assert _status(5, 0) == "dropped"
    assert _status(0, 5) == "new"
    assert _status(0, 0) == "unchanged"


async def test_audit_reads_the_shape_the_api_actually_returns() -> None:
    """Findings live in ``sections[].props{}`` keyed by check code — not in ``checks[]``.

    This is the bug in the workflow being replaced: a ``checks[]``-shaped parse finds no key
    of that name, contributes nothing, and hands the model an audit that looks like a clean
    site. Reporting no audit at all would have been safer than reporting a false one.
    """
    report = {
        "score_percent": 63,
        "weighted_score_percent": 63,
        "total_pages": 111,
        "total_errors": 108,
        "total_warnings": 81,
        "total_notices": 244,
        "audit_time": "2026-07-25 09:26:48",
        "sections": [
            {
                "uid": "crawling_v2",
                "name": "Crawlen & Indexatie",
                "props": {
                    "http4xx": {
                        "code": "http4xx",
                        "status": "error",
                        "name": "4XX Http-status codes",
                        "value": 53,
                    },
                    "http5xx": {
                        "code": "http5xx",
                        "status": "error",
                        "name": "5XX HTTP Statuscodes",
                        "value": 0,
                    },
                },
            },
            {
                "uid": "content_v2",
                "name": "Content",
                "props": {
                    "image_no_alt": {
                        "code": "image_no_alt",
                        "status": "warning",
                        "name": "Missende alt-tekst",
                        "value": 37,
                    },
                    "title_long": {
                        "code": "title_long",
                        "status": "notice",
                        "name": "Titel is te lang",
                        "value": 10,
                    },
                },
            },
        ],
    }
    client = _Client(
        {
            "/site-audit/audits/report": report,
            "/site-audit/audits": {
                "items": [
                    {"id": 2595964, "site_id": 2595964, "status": "finished",
                     "last_update": "2026-07-25"},
                    {"id": 111, "site_id": 2595964, "status": "processing",
                     "last_update": "2026-08-01"},
                    {"id": 222, "site_id": 999, "status": "finished",
                     "last_update": "2026-08-04"},
                ]
            },
        }
    )
    audit = await ADAPTER.audit(client, "2595964")
    assert audit is not None
    assert audit["score"] == 63
    assert audit["audit_id"] == 2595964  # the finished one for *this* site, not the newest
    codes = [f["code"] for f in audit["findings"]]
    # Errors first, then warnings, then notices; a check with zero affected pages is not a
    # finding at all.
    assert codes == ["http4xx", "image_no_alt", "title_long"]
    assert audit["findings"][0]["pages"] == 53
    assert audit["findings"][0]["section"] == "Crawlen & Indexatie"


async def test_audit_is_none_when_no_finished_audit_belongs_to_this_project() -> None:
    """Never another client's audit: the match is ``site_id``, never a domain search."""
    client = _Client(
        {"/site-audit/audits": {"items": [{"id": 9, "site_id": 5, "status": "finished"}]}}
    )
    assert await ADAPTER.audit(client, "7457072") is None


async def test_ai_search_pairs_each_engine_with_its_own_statistics() -> None:
    client = _Client(
        {
            "/ai-result-tracker/llm-engines/4975/statistics": {
                "llm_id": 4975,
                "stats": {"last_update": "2026-08-05", "prompts_count": 12},
                "presence": {
                    "link_percent_in_top": 25,
                    "link_diff": 5,
                    "mention_percent_in_top": 40,
                    "mention_diff": -2,
                },
                "sources_presence": {"answers_with_sources": 8},
            },
            "/ai-result-tracker/llm-engines": {
                "data": [{"id": 4975, "base_name": "chatgpt"}]
            },
        }
    )
    rows = await ADAPTER.ai_search(client, "7457072", date(2026, 7, 1), date(2026, 7, 31))
    assert rows == [
        {
            "engine": "chatgpt",
            "prompts": 12,
            "link_percent": 25.0,
            "link_change": 5.0,
            "mention_percent": 40.0,
            "mention_change": -2.0,
            "answers_with_sources": 8.0,
            "last_update": "2026-08-05",
        }
    ]


async def test_an_engine_whose_statistics_fail_is_skipped_not_fatal() -> None:
    """One engine's outage must not cost the report its whole AI-search section."""
    client = _Client(
        {
            "/ai-result-tracker/llm-engines/1/statistics": _Response({}, status=500),
            "/ai-result-tracker/llm-engines": {"data": [{"id": 1, "base_name": "gemini"}]},
        }
    )
    assert await ADAPTER.ai_search(client, "1", date(2026, 7, 1), date(2026, 7, 31)) == []


def test_previous_period_is_the_same_span_a_year_earlier() -> None:
    assert previous_period(date(2026, 7, 1), date(2026, 7, 31)) == (
        date(2025, 7, 1),
        date(2025, 7, 31),
    )
    # 29 February has no counterpart; the comparison lands on the 28th rather than raising
    # in a background job at midnight.
    start, end = previous_period(date(2024, 2, 1), date(2024, 2, 29))
    assert end == date(2023, 2, 28)
    assert (end - start).days == 28


async def test_accounts_are_listed_for_linking_never_guessed_from_a_domain() -> None:
    client = _Client(
        {
            "/sites": {
                "data": [
                    {"id": 2, "title": "Zeta", "name": "zeta.nl", "keyword_count": 4},
                    {"id": 1, "title": "Alpha", "name": "alpha.nl", "keyword_count": 9},
                ]
            }
        }
    )
    options = await ADAPTER.list_accounts(client)
    assert [option.display_name for option in options] == ["Alpha", "Zeta"]
    assert options[0].external_id == "1"
    assert options[0].config["keyword_count"] == 9
