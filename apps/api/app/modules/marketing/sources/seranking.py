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
    engines = _rows(body)
    keywords: list[dict] = []
    for engine in engines:
        nested = engine.get("keywords")
        if isinstance(nested, list):
            keywords.extend(row for row in nested if isinstance(row, dict))
        elif "positions" in engine:
            keywords.append(engine)  # already flat
    if not keywords and isinstance(body, dict) and isinstance(body.get("keywords"), list):
        keywords = [row for row in body["keywords"] if isinstance(row, dict)]
    return keywords


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
        """
        keywords = await self._positions(client, external_id, start, end, landing_pages=True)
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
        params: dict[str, Any] = {
            "date_from": start.isoformat(),
            "date_to": end.isoformat(),
        }
        if landing_pages:
            params["with_landing_pages"] = "1"
        response = await client.get(f"{API4}/sites/{external_id}/positions", params=params)
        response.raise_for_status()
        return _keyword_entries(response.json())

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
