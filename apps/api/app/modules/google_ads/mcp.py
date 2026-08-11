"""Curated AI tools for Google Ads. Business-licensed — see LICENSE.

Every ``/api/v1`` route is *already* an MCP tool (CLAUDE.md §12), so this file is not how the
surface becomes reachable — it is where a **richer shape than a 1:1 endpoint mapping** earns its
place. Three do:

* ``google_ads.accounts`` — grounding. Every other tool takes an account id, and an assistant
  asked about "AAZET" needs a way to turn a client's name into one.
* ``google_ads.overview`` — the question an agency actually asks ("how is this client doing?"),
  which is otherwise three calls and an arithmetic step the model should not be doing: the
  period, the period before it, and the delta between them.
* ``google_ads.wasted_spend`` — the negative-keyword question the proof-of-competence was built
  around, expressed once instead of as "fetch search terms, then filter, then cross-reference
  what is already excluded". The cross-reference is the part a model gets wrong.

Each handler runs under the caller's own ``RequestContext``: the account is loaded through the
tenant-scoped repository with the company horizon applied, and the tool is offered to the model
only when the caller holds ``google_ads.account.read``. A tool cannot answer across tenants or
beyond what the person asking may already see.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.ai.tools import AIToolSpec, Source, ToolResult
from app.core.googleads import AdsNotConfigured, format_customer_id
from app.core.periods import ComparePeriod, compare_window
from app.errors import AppError
from app.modules.google_ads.reads import GoogleAdsReadService
from app.modules.google_ads.service import GoogleAdsService

_READ = "google_ads.account.read"


def _account_arg(args: dict[str, Any]) -> uuid.UUID:
    raw = args.get("account_id")
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError) as exc:
        raise AppError("validation", "errors.validation", status_code=422) from exc


async def _accounts(ctx: Any, args: dict[str, Any]) -> ToolResult:
    query = str(args.get("query") or "").strip().casefold()
    rows = await GoogleAdsService(ctx).list_accounts(active_only=True)
    if query:
        rows = [
            row
            for row in rows
            if query in row.descriptive_name.casefold() or query in row.customer_id
        ]
    return ToolResult(
        data={
            "accounts": [
                {
                    "account_id": str(row.id),
                    "name": row.descriptive_name,
                    "customer_id": format_customer_id(row.customer_id),
                    "company_id": str(row.company_id) if row.company_id else None,
                    "currency": row.currency_code,
                    "status": row.status,
                }
                for row in rows[:25]
            ]
        },
        sources=tuple(
            Source(type="google_ads_account", id=str(row.id), label=row.descriptive_name)
            for row in rows[:25]
        ),
    )


async def _overview(ctx: Any, args: dict[str, Any]) -> ToolResult:
    """This period against the one before it, with the deltas already computed.

    The comparison defaults to **the same period a year earlier**, which is the platform's own
    default (#312) and the comparison seasonality survives: a campsite's July has nothing to say
    to its June. It is stated in the answer either way, because "up 20 %" over an unnamed span
    is not a claim anyone can check.
    """
    account_id = _account_arg(args)
    period = str(args.get("period") or "30d")
    mode = ComparePeriod.PREVIOUS if args.get("compare") == "previous" else ComparePeriod.YEAR
    service = GoogleAdsReadService(ctx)
    try:
        current = await service.campaigns(account_id, period, None, None)
    except AdsNotConfigured as exc:
        return ToolResult(data={"error": exc.message_key})
    assert current.period is not None
    before_start, before_end = compare_window(
        current.period.date_from, current.period.date_to, mode
    )
    previous = await service.campaigns(account_id, None, before_start, before_end)

    now = current.totals.model_dump() if current.totals else {}
    then = previous.totals.model_dump() if previous.totals else {}
    return ToolResult(
        data={
            "account": current.account.model_dump(mode="json"),
            "currency": current.currency,
            "period": current.period.model_dump(mode="json"),
            "compared_with": {
                "from": before_start.isoformat(),
                "to": before_end.isoformat(),
                "mode": mode.value,
            },
            "totals": now,
            "previous_totals": then,
            "change": {key: _delta(now.get(key), then.get(key)) for key in now},
            "campaigns": current.rows[:15],
            "warnings": current.warnings,
        },
        sources=(
            Source(
                type="google_ads_account",
                id=str(current.account.id),
                label=current.account.descriptive_name,
            ),
        ),
    )


def _delta(now: Any, then: Any) -> dict[str, Any] | None:
    """Absolute and relative change, or ``None`` when there is nothing to compare against.

    A percentage against a zero baseline is not "infinite growth", it is undefined — and a model
    handed ``inf`` will write a sentence about it.
    """
    if not isinstance(now, int | float) or not isinstance(then, int | float):
        return None
    absolute = round(now - then, 4)
    return {
        "from": then,
        "to": now,
        "absolute": absolute,
        "relative": round(absolute / then, 4) if then else None,
    }


async def _wasted_spend(ctx: Any, args: dict[str, Any]) -> ToolResult:
    """Search terms that cost money, converted nothing, and are **not already excluded**.

    The last clause is the whole value of doing this in one tool. A model asked to combine a
    search-terms list with a negatives list will happily propose excluding something that is
    already excluded — and the resulting "recommendation" wastes an account manager's afternoon.
    Matching is exact-text and case-insensitive, which under-claims rather than over-claims: a
    phrase negative that would already catch a term is not detected here, so a proposal may be
    redundant, but nothing already blocked by an identical negative is ever offered.
    """
    account_id = _account_arg(args)
    period = str(args.get("period") or "30d")
    try:
        min_cost = float(args.get("min_cost") or 0)
    except (TypeError, ValueError):
        min_cost = 0.0
    service = GoogleAdsReadService(ctx)
    try:
        terms = await service.search_terms(
            account_id, period, None, None, min_cost=min_cost or None
        )
    except AdsNotConfigured as exc:
        return ToolResult(data={"error": exc.message_key})
    negatives = await service.negatives(account_id)
    blocked = {
        str(row.get("keyword") or "").casefold()
        for row in negatives.rows
        if row.get("keyword")
    }
    candidates = [
        row
        for row in terms.rows
        # `match_status` is Google's own answer to "has somebody already decided about this?"
        # — ADDED_EXCLUDED and EXCLUDED both mean yes.
        if not float(row.get("conversions") or 0)
        and str(row.get("match_status") or "") not in {"EXCLUDED", "ADDED_EXCLUDED"}
        and str(row.get("search_term") or "").casefold() not in blocked
    ]
    candidates.sort(key=lambda row: float(row.get("cost") or 0), reverse=True)
    return ToolResult(
        data={
            "account": terms.account.model_dump(mode="json"),
            "currency": terms.currency,
            "period": terms.period.model_dump(mode="json") if terms.period else None,
            "wasted_cost": round(sum(float(r.get("cost") or 0) for r in candidates), 2),
            "terms": candidates[:50],
            "already_excluded_count": len(blocked),
            "warnings": [
                *terms.warnings,
                # Said out loud so the model does not present this as a decision.
                "google_ads.warning.wasted_spend_is_a_shortlist",
            ],
        },
        sources=(
            Source(
                type="google_ads_account",
                id=str(terms.account.id),
                label=terms.account.descriptive_name,
            ),
        ),
    )


GOOGLE_ADS_MCP_TOOLS: list[AIToolSpec] = [
    AIToolSpec(
        name="google_ads.accounts",
        description=(
            "List the Google Ads accounts this workspace has linked, optionally filtered by "
            "client name or customer ID. Start here: every other Google Ads tool takes an "
            "account_id from this list."
        ),
        input_schema={
            "type": "object",
            "properties": {"query": {"type": ["string", "null"]}},
            "required": [],
            "additionalProperties": False,
        },
        handler=_accounts,
        permission=_READ,
    ),
    AIToolSpec(
        name="google_ads.overview",
        description=(
            "How a Google Ads account is doing over a period, compared with the same period a "
            "year earlier (or the immediately preceding period), with the change already "
            "computed. Returns account totals, the change per metric and the top campaigns. "
            "Costs are in the account's own currency; ctr and conversion_rate are fractions "
            "(0.0453 = 4.53%); a null ratio means not computable, not zero."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "period": {
                    "type": ["string", "null"],
                    "description": "30d, 90d, month, last_month, quarter, 2026-07, 2026-Q3.",
                },
                "compare": {"type": ["string", "null"], "enum": ["year", "previous", None]},
            },
            "required": ["account_id"],
            "additionalProperties": False,
        },
        handler=_overview,
        permission=_READ,
    ),
    AIToolSpec(
        name="google_ads.wasted_spend",
        description=(
            "Search terms in a Google Ads account that cost money, produced no conversions, and "
            "are not already excluded as negative keywords. A shortlist to review, never a "
            "decision: a term with no conversions may still be worth keeping, and excluding one "
            "wrongly costs customers silently."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "period": {"type": ["string", "null"]},
                "min_cost": {
                    "type": ["number", "null"],
                    "description": "Only terms costing at least this, in the account's currency.",
                },
            },
            "required": ["account_id"],
            "additionalProperties": False,
        },
        handler=_wasted_spend,
        permission=_READ,
    ),
]
