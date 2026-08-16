"""SE Ranking adapter — rankings, the site audit and AI-search visibility (issue #300).

The first source that is **not** Google, which is why the protocol grew an ``auth`` kind. Where
GA4/GSC/Ads ride a per-user OAuth grant with scopes and a reconnect flow, SE Ranking is one
API key per *agency*, stored encrypted on ``marketing_settings`` beside the Ads developer
token. Its failure modes are different in kind and are named differently: there is no
"connect", no scope, and no per-user connection — only *configured* or not.

Written against the live API rather than from memory (CLAUDE.md §11), which changed three
things a plausible-looking implementation would have got wrong:

* ``/positions`` answers **per search engine** — ``{"data": [{"site_engine_id", "keywords": []}]}``
  — not one flat keyword list. A client tracking Google NL and Google BE has two entries, and
  reading only the first silently halves the report.
* ``pos: 0`` means *not ranking*, not "position zero". Every average here filters it out; a
  keyword that entered the top 10 from nowhere would otherwise drag the average to 0.
* Ids arrive as **strings in one endpoint and integers in another** (``keyword-groups`` returns
  ``id: "2906659"``; a keyword's ``group_id`` is ``145829``). Everything is keyed on ``str()``.

Two hosts share one credential, which is SE Ranking's own arrangement rather than ours: the
project endpoints live on ``api4`` and the audit / AI-tracker endpoints on ``api.seranking.com/v1``.
Both are overridable in config so a test or a proxy can point them elsewhere.

Every parse below is defensive on purpose. ``docs/REPORTING.md`` §SE Ranking carries the
checklist of what to re-verify the day the API changes shape.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from app.modules.marketing.models import MarketingSource
from app.modules.marketing.sources.base import (
    AUTH_ORG_KEY,
    AccountOption,
    DailyMetrics,
    DrilldownRow,
    DrilldownTable,
    register,
)

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger("schakl.marketing")

#: The project/rankings host. Keyword groups and positions live here.
API4 = "https://api4.seranking.com"
#: The audit + AI-result-tracker host. Same key, different base — SE Ranking's arrangement.
API_V1 = "https://api.seranking.com/v1"

#: A keyword ranked outside this is "not in sight" for a client report. The workflow this
#: replaces used the same threshold to decide which rows are worth printing at all.
VISIBLE_DEPTH = 25

#: SE Ranking's global search-engine catalogue, cached process-wide — see
#: :meth:`SeRankingAdapter._engine_catalogue` for why that is safe on a multi-tenant box.
_ENGINE_CATALOGUE: dict[str, dict[str, Any]] | None = None
_ENGINE_CATALOGUE_AT: float = 0.0
#: A day. The list gains a row when SE Ranking starts tracking a new country.
_CATALOGUE_TTL = 86_400.0


class SeRankingNotConfigured(RuntimeError):
    """No API key stored for this org — the picker teaches, it does not call."""


def _num(raw: Any, default: float = 0.0) -> float:
    """SE Ranking mixes ``"45"`` and ``45`` in the same field across endpoints."""
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _int(raw: Any, default: int = 0) -> int:
    return int(_num(raw, default))


def _rows(body: Any, *keys: str) -> list[dict]:
    """The list inside a response, whichever envelope this endpoint happens to use.

    ``{"data": [...]}``, ``{"items": [...]}`` and a bare list are all shapes this API returns
    from neighbouring endpoints. Guessing one and getting it wrong reads as "the client has no
    keywords", which is indistinguishable from the truth on a screen.
    """
    if isinstance(body, list):
        return [row for row in body if isinstance(row, dict)]
    if isinstance(body, dict):
        for key in (*keys, "data", "items", "result"):
            value = body.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    return []


def _keyword_entries(body: Any) -> list[dict]:
    """Every keyword across every search engine in a ``/positions`` response.

    The per-engine envelope is the trap this function exists for: a client tracking two
    engines has two entries, and ``body["keywords"]`` — the shape the flow being replaced
    assumed — is simply absent.
    """
    return [keyword for _, keywords in _engine_entries(body) for keyword in keywords]


def _engine_summary(keywords: list[dict]) -> dict[str, Any]:
    """One engine's position table folded into the figures a client reads.

    Measured at the **ends of the period**, not averaged across it: "where do I stand" is a
    question about now, and the movement beside it is what the month did. That is the same
    begin/end shape :meth:`SeRankingAdapter.keyword_rows` prints per keyword, one level up, so
    the two tables in one document cannot tell different stories about the same month.

    ``pos == 0`` is *not ranking* rather than "position zero" — the trap this whole adapter is
    written around. A tracked term that appears nowhere counts in ``keywords_tracked`` and in
    nothing else, which is what stops an invisible keyword improving the average.
    """
    tracked = 0
    begin_positions: list[int] = []
    end_positions: list[int] = []
    for keyword in keywords:
        entries = [
            entry
            for entry in (keyword.get("positions") or [])
            if isinstance(entry, dict) and _parse_date(entry.get("date")) is not None
        ]
        if not entries:
            continue
        tracked += 1
        entries.sort(key=lambda entry: str(entry.get("date")))
        first, last = _int(entries[0].get("pos")), _int(entries[-1].get("pos"))
        if first > 0:
            begin_positions.append(first)
        if last > 0:
            end_positions.append(last)
    if not tracked:
        return {}
    average = round(sum(end_positions) / len(end_positions), 1) if end_positions else 0.0
    before = round(sum(begin_positions) / len(begin_positions), 1) if begin_positions else 0.0
    return {
        "keywords_tracked": float(tracked),
        "keywords_ranking": float(len(end_positions)),
        "top3": float(sum(1 for pos in end_positions if pos <= 3)),
        "top10": float(sum(1 for pos in end_positions if pos <= 10)),
        "top30": float(sum(1 for pos in end_positions if pos <= 30)),
        "avg_position": average,
        # Positive = improved, the convention the keyword table already prints (rank 8 → 3
        # is +5). Zero when either end has nothing to compare, never a move invented out of an
        # empty set.
        "change": round(before - average, 1) if (before and average) else 0.0,
    }


def _engine_entries(body: Any) -> list[tuple[str, list[dict]]]:
    """The same ``/positions`` response kept **per engine** (#381).

    :func:`_keyword_entries` flattens, which is right for a keyword table and destroys the one
    fact the "Zoekmachines" section is about. Both read the same body so a report can fetch it
    once: at 145 keywords over a 31-day month that payload is the largest thing SE Ranking
    returns, and asking twice for two views of it is a call nobody would defend out loud.

    The engine key is ``site_engine_id`` — *this project's* engine row, not the catalogue's
    ``search_engine_id``. Naming it needs ``/sites/{id}/search-engines`` to bridge the two, and
    conflating them resolves engine 1104694 against a catalogue that stops at 889.
    """
    out: list[tuple[str, list[dict]]] = []
    for engine in _rows(body):
        nested = engine.get("keywords")
        if isinstance(nested, list):
            out.append(
                (
                    str(engine.get("site_engine_id") or ""),
                    [row for row in nested if isinstance(row, dict)],
                )
            )
        elif "positions" in engine:
            # Already flat — one unnamed engine, which is what the fallbacks below also produce.
            out.append(("", [engine]))
    if not out and isinstance(body, dict) and isinstance(body.get("keywords"), list):
        out.append(("", [row for row in body["keywords"] if isinstance(row, dict)]))
    merged: dict[str, list[dict]] = {}
    for key, keywords in out:
        merged.setdefault(key, []).extend(keywords)
    return list(merged.items())


class SeRankingAdapter:
    """Rankings as tier-1 daily aggregates; the detail is fetched per report and snapshotted."""

    source = MarketingSource.SERANKING.value
    auth = AUTH_ORG_KEY
    #: No OAuth scope exists for an API key. Kept for protocol shape; never checked.
    scope = ""
    drilldowns = ("keywords", "keyword_groups", "audit", "ai_search")

    # --- picker ------------------------------------------------------------------------- #
    async def list_accounts(self, client: httpx.AsyncClient) -> list[AccountOption]:
        """The agency's SE Ranking projects, so a client is *linked* rather than guessed.

        The workflow this replaces matched an audit to a client by fuzzy-searching its domain
        across every audit on the account and sorting the candidates by status. An explicit
        link removes the whole class of "the report showed another client's audit".
        """
        response = await client.get(f"{API4}/sites")
        response.raise_for_status()
        options: list[AccountOption] = []
        for site in _rows(response.json(), "sites"):
            site_id = site.get("id")
            if site_id is None:
                continue
            title = str(site.get("title") or site.get("name") or site_id)
            options.append(
                AccountOption(
                    external_id=str(site_id),
                    display_name=title,
                    config={
                        "url": str(site.get("name") or ""),
                        "keyword_count": _int(site.get("keyword_count")),
                    },
                    account_hint=str(site.get("name") or ""),
                )
            )
        return sorted(options, key=lambda option: option.display_name.lower())

    # --- tier 1: daily aggregates ------------------------------------------------------- #
    async def fetch_daily(
        self,
        client: httpx.AsyncClient,
        external_id: str,
        start: date,
        end: date,
        config: dict,
    ) -> list[DailyMetrics]:
        """One row per day, folded out of the per-keyword position series.

        Derived from ``/positions`` rather than from a chart endpoint because the report needs
        that payload anyway and this keeps the source of a number and the source of its trend
        identical — a dashboard average that disagreed with the report's would be unexplainable.
        """
        keywords = await self._positions(client, external_id, start, end, landing_pages=False)
        per_day: dict[date, list[int]] = defaultdict(list)
        for keyword in keywords:
            for entry in keyword.get("positions") or []:
                if not isinstance(entry, dict):
                    continue
                day = _parse_date(entry.get("date"))
                if day is None:
                    continue
                per_day[day].append(_int(entry.get("pos")))

        daily: list[DailyMetrics] = []
        for day, positions in sorted(per_day.items()):
            # `pos == 0` is "not ranking". Averaging it in would report a perfect position for
            # a keyword that appears nowhere at all.
            ranking = [pos for pos in positions if pos > 0]
            daily.append(
                DailyMetrics(
                    day=day,
                    metrics={
                        "avg_position": round(sum(ranking) / len(ranking), 2) if ranking else 0.0,
                        "top3": float(sum(1 for pos in ranking if pos <= 3)),
                        "top10": float(sum(1 for pos in ranking if pos <= 10)),
                        "top30": float(sum(1 for pos in ranking if pos <= 30)),
                        "keywords_ranking": float(len(ranking)),
                        "keywords_tracked": float(len(positions)),
                    },
                )
            )
        return daily

    # --- tier 2: live detail, snapshotted by whoever asked ------------------------------ #
    async def drilldown(
        self,
        client: httpx.AsyncClient,
        external_id: str,
        kind: str,
        start: date,
        end: date,
        config: dict,
    ) -> DrilldownTable:
        if kind == "audit":
            return await self._audit_table(client, external_id)
        if kind == "ai_search":
            return await self._ai_search_table(client, external_id, start, end)
        rows = await self.keyword_rows(client, external_id, start, end)
        if kind == "keyword_groups":
            return _group_table(rows)
        return DrilldownTable(
            kind="keywords",
            columns=["begin", "end", "change"],
            rows=[
                DrilldownRow(
                    label=row["keyword"],
                    metrics={
                        "begin": float(row["begin"]),
                        "end": float(row["end"]),
                        "change": float(row["change"]),
                    },
                    href=row.get("landing_page"),
                )
                for row in rows[:50]
            ],
        )

    async def keyword_rows(
        self,
        client: httpx.AsyncClient,
        external_id: str,
        start: date,
        end: date,
        *,
        max_position: int = VISIBLE_DEPTH,
        body: Any = None,
    ) -> list[dict[str, Any]]:
        """Per-keyword begin/end positions for the period, with its group and landing page.

        This is the rankings section of a report. It is deliberately **not** stored per day in
        ``marketing_metrics_daily``: a keyword × day warehouse for every client is a large
        table answering one question a month, and a report snapshots its own answer anyway.

        ``max_position`` is the client's own ``rankings.max_position`` (#381). It used to be
        :data:`VISIBLE_DEPTH` and nothing else, while the Search Console adapter was handed the
        setting — so one control on one screen drew the line at 25 for one client and wherever
        they asked for it for the next, depending on which integration the agency happened to
        hold. The default stays :data:`VISIBLE_DEPTH`, which is the same number the setting
        defaults to, so nothing moves for a caller that does not care.

        ``body`` is a ``/positions`` response the caller already holds, so a report producing
        both keyword rows *and* the per-engine table pays for that payload once
        (:meth:`positions_body`). Left out, this fetches its own, which is what the drilldown
        wants.
        """
        if body is None:
            body = await self.positions_body(client, external_id, start, end)
        keywords = _keyword_entries(body)
        groups = await self._keyword_groups(client, external_id)
        rows: list[dict[str, Any]] = []
        for keyword in keywords:
            entries = [
                entry
                for entry in (keyword.get("positions") or [])
                if isinstance(entry, dict) and _parse_date(entry.get("date")) is not None
            ]
            if not entries:
                continue
            entries.sort(key=lambda entry: str(entry.get("date")))
            begin, finish = _int(entries[0].get("pos")), _int(entries[-1].get("pos"))
            # A keyword that was invisible at both ends says nothing a client can act on and
            # would fill the table with dashes.
            if not (0 < begin <= max_position or 0 < finish <= max_position):
                continue
            pages = [p for p in (keyword.get("landing_pages") or []) if isinstance(p, dict)]
            pages.sort(key=lambda page: str(page.get("date") or ""))
            rows.append(
                {
                    "keyword": str(keyword.get("name") or ""),
                    "group": groups.get(str(keyword.get("group_id")), ""),
                    "begin": begin,
                    "end": finish,
                    # Positive = improved. Rank 8 -> 3 is +5, which reads the way a client
                    # expects even though the number went down.
                    "change": (begin - finish) if (begin > 0 and finish > 0) else 0,
                    "status": _status(begin, finish),
                    "landing_page": (pages[-1].get("url") if pages else None),
                    "volume": _int(keyword.get("volume")),
                }
            )
        rows.sort(key=lambda row: (row["group"].lower(), row["keyword"].lower()))
        return rows

    async def engine_rows(
        self,
        client: httpx.AsyncClient,
        external_id: str,
        start: date,
        end: date,
        *,
        body: Any = None,
    ) -> list[dict[str, Any]]:
        """One row per search engine this project tracks — the "Zoekmachines" section (#381).

        The section had that name and answered a different question: it was GA4's
        ``organic_sources`` split, which on a Dutch client is a single row reading ``google``
        and a pie chart with one slice. Correct, and not something an agency's customer asks.
        *Waar sta ik, en per zoekmachine* is, and it is a question only a rank tracker can
        answer — Google Analytics knows which engine sent a session and nothing about a position.

        Three reads, and each is a different kind of fact:

        * ``/positions`` — the positions themselves, shared with :meth:`keyword_rows`;
        * ``/sites/{id}/search-engines`` — *this project's* engines, which is the only thing
          that bridges a ``site_engine_id`` to a catalogue ``search_engine_id``;
        * ``/system/search-engines`` — the catalogue, so 320 prints as *Google Netherlands*
          rather than as 320. Cached process-wide: it is SE Ranking's own reference list, the
          same 690 rows for every tenant, and no part of it is anybody's data.
        """
        if body is None:
            body = await self.positions_body(client, external_id, start, end, landing_pages=False)
        per_engine = _engine_entries(body)
        if not per_engine:
            return []
        meta = await self._site_engines(client, external_id)
        catalogue = await self._engine_catalogue(client)
        rows: list[dict[str, Any]] = []
        for site_engine_id, keywords in per_engine:
            info = meta.get(site_engine_id) or {}
            engine = catalogue.get(str(info.get("search_engine_id") or "")) or {}
            summary = _engine_summary(keywords)
            if not summary:
                continue
            rows.append(
                {
                    # The catalogue's own name, then whatever the project row can say, then the
                    # bare id. Never an invented "Google" — a project tracking Bing would then
                    # print a row naming the wrong search engine, which is worse than a number.
                    "label": str(
                        engine.get("name")
                        or info.get("region_name")
                        or (f"#{site_engine_id}" if site_engine_id else "")
                    ),
                    **summary,
                }
            )
        # Loudest first: the engine somebody tracks most terms on is the one they mean.
        rows.sort(key=lambda row: float(row.get("keywords_tracked") or 0), reverse=True)
        return rows

    async def positions_body(
        self,
        client: httpx.AsyncClient,
        external_id: str,
        start: date,
        end: date,
        *,
        landing_pages: bool = True,
    ) -> Any:
        """The raw ``/positions`` payload, for a caller that wants two views of one read."""
        return await self._positions_body(
            client, external_id, start, end, landing_pages=landing_pages
        )

    async def audit(self, client: httpx.AsyncClient, external_id: str) -> dict[str, Any] | None:
        """The latest finished site audit for this project, flattened to what a report prints.

        Found by ``site_id``, never by fuzzy-matching a domain across the account's audits.
        The findings live in ``sections[].props{}`` — a **dict keyed by check code**, each with
        its own ``status`` and a ``value`` that is the number of affected pages. (An
        implementation expecting ``sections[].checks[]`` parses to nothing at all and reports a
        clean site, which is worse than reporting no audit.)
        """
        audit_id = await self._latest_audit_id(client, external_id)
        if audit_id is None:
            return None
        response = await client.get(
            f"{API_V1}/site-audit/audits/report", params={"audit_id": audit_id}
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            return None
        findings: list[dict[str, Any]] = []
        for section in body.get("sections") or []:
            if not isinstance(section, dict):
                continue
            props = section.get("props")
            if not isinstance(props, dict):
                continue
            for check in props.values():
                if not isinstance(check, dict):
                    continue
                value = _int(check.get("value"))
                if value <= 0:
                    continue
                findings.append(
                    {
                        "section": str(section.get("name") or section.get("uid") or ""),
                        "code": str(check.get("code") or ""),
                        # SE Ranking answers in the *account's* language. It is their string,
                        # not ours: passed through as data, never run through i18n.
                        "name": str(check.get("name") or ""),
                        "status": str(check.get("status") or "notice"),
                        "pages": value,
                    }
                )
        order = {"error": 0, "warning": 1, "notice": 2}
        findings.sort(key=lambda f: (order.get(f["status"], 3), -f["pages"]))
        return {
            "audit_id": audit_id,
            "score": _int(body.get("score_percent")),
            "weighted_score": _int(body.get("weighted_score_percent")),
            "pages": _int(body.get("total_pages")),
            "errors": _int(body.get("total_errors")),
            "warnings": _int(body.get("total_warnings")),
            "notices": _int(body.get("total_notices")),
            "audited_at": str(body.get("audit_time") or ""),
            "findings": findings,
        }

    async def ai_search(
        self, client: httpx.AsyncClient, external_id: str, start: date, end: date
    ) -> list[dict[str, Any]]:
        """Per-LLM presence from the AI Result Tracker — one row per configured engine."""
        response = await client.get(
            f"{API_V1}/projects/{external_id}/ai-result-tracker/llm-engines"
        )
        response.raise_for_status()
        engines = _rows(response.json(), "engines")
        out: list[dict[str, Any]] = []
        for engine in engines:
            engine_id = engine.get("id") or engine.get("llm_id")
            if engine_id is None:
                continue
            stats = await client.get(
                f"{API_V1}/projects/{external_id}/ai-result-tracker/llm-engines/"
                f"{engine_id}/statistics",
                params={"from": start.isoformat(), "to": end.isoformat()},
            )
            if stats.status_code >= 400:
                logger.info(
                    "seranking ai-search stats unavailable for engine %s (%s)",
                    engine_id,
                    stats.status_code,
                )
                continue
            body = stats.json() if stats.content else {}
            if not isinstance(body, dict):
                continue
            presence = body.get("presence") if isinstance(body.get("presence"), dict) else {}
            summary = body.get("stats") if isinstance(body.get("stats"), dict) else {}
            sources = (
                body.get("sources_presence")
                if isinstance(body.get("sources_presence"), dict)
                else {}
            )
            out.append(
                {
                    "engine": str(engine.get("base_name") or engine.get("name") or engine_id),
                    "prompts": _int(summary.get("prompts_count")),
                    "link_percent": _num(presence.get("link_percent_in_top")),
                    "link_change": _num(presence.get("link_diff")),
                    "mention_percent": _num(presence.get("mention_percent_in_top")),
                    "mention_change": _num(presence.get("mention_diff")),
                    "answers_with_sources": _num(sources.get("answers_with_sources")),
                    "last_update": str(summary.get("last_update") or ""),
                }
            )
        return out

    def deep_link(self, external_id: str, config: dict) -> str:
        return f"https://online.seranking.com/research.keyword.html#/?site_id={external_id}"

    # --- plumbing ------------------------------------------------------------------------ #
    async def _positions(
        self,
        client: httpx.AsyncClient,
        external_id: str,
        start: date,
        end: date,
        *,
        landing_pages: bool,
    ) -> list[dict]:
        body = await self._positions_body(
            client, external_id, start, end, landing_pages=landing_pages
        )
        return _keyword_entries(body)

    async def _positions_body(
        self,
        client: httpx.AsyncClient,
        external_id: str,
        start: date,
        end: date,
        *,
        landing_pages: bool,
    ) -> Any:
        params: dict[str, Any] = {
            "date_from": start.isoformat(),
            "date_to": end.isoformat(),
        }
        if landing_pages:
            params["with_landing_pages"] = "1"
        response = await client.get(f"{API4}/sites/{external_id}/positions", params=params)
        response.raise_for_status()
        return response.json()

    async def _site_engines(
        self, client: httpx.AsyncClient, external_id: str
    ) -> dict[str, dict[str, Any]]:
        """This project's own engine rows, keyed by ``site_engine_id``.

        Soft on failure like :meth:`_keyword_groups`: without it the per-engine table loses its
        *names*, which is a poorer section, while raising would lose the section entirely.
        """
        response = await client.get(f"{API4}/sites/{external_id}/search-engines")
        if response.status_code >= 400:
            return {}
        return {
            str(row.get("site_engine_id")): row
            for row in _rows(response.json())
            if row.get("site_engine_id") is not None
        }

    async def _engine_catalogue(self, client: httpx.AsyncClient) -> dict[str, dict[str, Any]]:
        """``search_engine_id`` → ``{name, type}``, from SE Ranking's global list.

        Cached for the life of the process rather than per request. Two things make that safe
        and one makes it worth doing: the payload is the same 690 rows for every tenant and
        holds no tenant data (so a shared cache crosses no boundary — CLAUDE.md §5 is about
        *rows*, and these are the vendor's own reference list); it changes when SE Ranking adds
        a country, which is not a thing that happens during a report run; and it is ~52 KB,
        which is not a thing to re-download once per client in a nightly batch of thirty.

        ``id`` arrives as a **string** here and ``search_engine_id`` as an **int** on the
        project row — the mismatch this file's docstring already warns about for keyword
        groups, in a second place. Everything is keyed on ``str()``.
        """
        global _ENGINE_CATALOGUE, _ENGINE_CATALOGUE_AT
        now = time.monotonic()
        if _ENGINE_CATALOGUE is not None and now - _ENGINE_CATALOGUE_AT < _CATALOGUE_TTL:
            return _ENGINE_CATALOGUE
        response = await client.get(f"{API4}/system/search-engines")
        if response.status_code >= 400:
            return _ENGINE_CATALOGUE or {}
        catalogue = {
            str(row.get("id")): {
                "name": str(row.get("name") or ""),
                "type": str(row.get("type") or ""),
            }
            for row in _rows(response.json())
            if row.get("id") is not None
        }
        if catalogue:
            _ENGINE_CATALOGUE, _ENGINE_CATALOGUE_AT = catalogue, now
        return catalogue

    async def _keyword_groups(
        self, client: httpx.AsyncClient, external_id: str
    ) -> dict[str, str]:
        response = await client.get(f"{API4}/keyword-groups/{external_id}")
        if response.status_code >= 400:
            return {}
        return {
            str(group.get("id")): str(group.get("name") or "")
            for group in _rows(response.json(), "groups")
            if group.get("id") is not None
        }

    async def _latest_audit_id(
        self, client: httpx.AsyncClient, external_id: str
    ) -> int | None:
        response = await client.get(f"{API_V1}/site-audit/audits", params={"limit": 200})
        if response.status_code >= 400:
            return None
        finished = [
            audit
            for audit in _rows(response.json(), "audits")
            if str(audit.get("site_id") or "") == str(external_id)
            and str(audit.get("status") or "") == "finished"
        ]
        if not finished:
            return None
        finished.sort(key=lambda audit: str(audit.get("last_update") or ""), reverse=True)
        best = finished[0].get("id")
        return int(best) if best is not None else None

    async def _audit_table(
        self, client: httpx.AsyncClient, external_id: str
    ) -> DrilldownTable:
        audit = await self.audit(client, external_id)
        if audit is None:
            return DrilldownTable(kind="audit", columns=["pages"], rows=[])
        return DrilldownTable(
            kind="audit",
            columns=["pages"],
            rows=[
                DrilldownRow(label=f["name"], metrics={"pages": float(f["pages"])})
                for f in audit["findings"][:10]
            ],
        )

    async def _ai_search_table(
        self, client: httpx.AsyncClient, external_id: str, start: date, end: date
    ) -> DrilldownTable:
        rows = await self.ai_search(client, external_id, start, end)
        return DrilldownTable(
            kind="ai_search",
            columns=["link_percent", "mention_percent"],
            rows=[
                DrilldownRow(
                    label=row["engine"],
                    metrics={
                        "link_percent": row["link_percent"],
                        "mention_percent": row["mention_percent"],
                    },
                )
                for row in rows
            ],
        )


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _status(begin: int, end: int) -> str:
    """How a keyword moved, in the vocabulary the report's i18n keys use."""
    if begin > 0 and end > 0:
        if end < begin:
            return "improved"
        return "declined" if end > begin else "unchanged"
    if begin > 0:
        return "dropped"
    return "new" if end > 0 else "unchanged"


def _group_table(rows: list[dict[str, Any]]) -> DrilldownTable:
    """Keyword groups rolled up — how a theme moved, not how one phrase did."""
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[row["group"] or ""].append(row)
    table_rows: list[DrilldownRow] = []
    for group, members in sorted(buckets.items()):
        ranking = [m for m in members if m["end"] > 0]
        table_rows.append(
            DrilldownRow(
                label=group,
                metrics={
                    "keywords": float(len(members)),
                    "top10": float(sum(1 for m in ranking if m["end"] <= 10)),
                    "avg_position": (
                        round(sum(m["end"] for m in ranking) / len(ranking), 1)
                        if ranking
                        else 0.0
                    ),
                },
            )
        )
    return DrilldownTable(
        kind="keyword_groups", columns=["keywords", "top10", "avg_position"], rows=table_rows
    )


def previous_period(start: date, end: date) -> tuple[date, date]:
    """The same span a year earlier — the comparison a monthly client report leads with."""
    span = end - start
    try:
        compare_end = end.replace(year=end.year - 1)
    except ValueError:  # 29 February
        compare_end = end.replace(year=end.year - 1, day=28)
    return compare_end - span, compare_end


def trailing_window(today: date, days: int) -> tuple[date, date]:
    end = today - timedelta(days=1)
    return end - timedelta(days=days - 1), end


register(SeRankingAdapter())
