"""What is live on a channel, and how it did. Business-licensed — see LICENSE.

Live reads, never stored: what a Page published last week and what it reached are Meta's
facts, and a copy here would be a second opinion about them. The answer to "what did we post
for this client in August" that *is* ours — who wrote it, who approved it — is the posts table.

**Every metric is probed, never assumed.** Meta removed ``impressions`` and the page-fans
family in 2025–26 in favour of ``views`` and ``*_media_view``, and its reference page went on
listing metrics the changelog had removed. Asking for one metric Meta no longer serves fails
the *whole* request, so each is asked for on its own and a refusal costs that one tile and is
named — a failed read costs the column, never the table (docs/REPORTING.md's rule).
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any

from app.core.metagraph import KIND_INSTAGRAM, KIND_PAGE, MetaClient, MetaError
from app.integrations.meta.models import MetaAsset

#: Page metrics that survived the 2025–26 removals (docs/META.md §8), in display order.
PAGE_METRICS: tuple[str, ...] = (
    "page_media_view",
    "page_total_media_view_unique",
    "page_post_engagements",
    "page_views_total",
    "page_follows",
)
#: Instagram account metrics, all served as ``metric_type=total_value``.
INSTAGRAM_METRICS: tuple[str, ...] = (
    "views",
    "reach",
    "accounts_engaged",
    "total_interactions",
    "profile_links_taps",
)
#: The longest window one insights call may span.
MAX_INSIGHT_DAYS = 90
MAX_PUBLISHED = 50


async def published(
    client: MetaClient, asset: MetaAsset, *, limit: int = 25
) -> list[dict[str, Any]]:
    """The most recent posts live on this channel, newest first, in one shape for both."""
    limit = max(1, min(limit, MAX_PUBLISHED))
    if asset.kind == KIND_INSTAGRAM:
        answer = await client.get(
            f"{asset.external_id}/media",
            {
                "fields": (
                    "id,caption,media_type,media_product_type,permalink,timestamp,"
                    "thumbnail_url,media_url,like_count,comments_count"
                ),
                "limit": limit,
            },
        )
        return [
            {
                "external_id": str(row.get("id") or ""),
                "channel": "instagram",
                "text": str(row.get("caption") or ""),
                "kind": str(row.get("media_product_type") or row.get("media_type") or ""),
                "permalink": str(row.get("permalink") or "") or None,
                "published_at": _stamp(row.get("timestamp")),
                "image_url": str(row.get("thumbnail_url") or row.get("media_url") or "") or None,
                "likes": _int(row.get("like_count")),
                "comments": _int(row.get("comments_count")),
                "shares": None,
            }
            for row in (answer.get("data") or [])
            if isinstance(row, dict)
        ]
    answer = await client.get(
        f"{asset.external_id}/published_posts",
        {
            "fields": (
                "id,message,created_time,permalink_url,full_picture,status_type,"
                "shares,likes.summary(true).limit(0),comments.summary(true).limit(0)"
            ),
            "limit": limit,
        },
    )
    return [
        {
            "external_id": str(row.get("id") or ""),
            "channel": "facebook",
            "text": str(row.get("message") or ""),
            "kind": str(row.get("status_type") or ""),
            "permalink": str(row.get("permalink_url") or "") or None,
            "published_at": _stamp(row.get("created_time")),
            "image_url": str(row.get("full_picture") or "") or None,
            "likes": _summary(row.get("likes")),
            "comments": _summary(row.get("comments")),
            "shares": _int((row.get("shares") or {}).get("count"))
            if isinstance(row.get("shares"), dict)
            else None,
        }
        for row in (answer.get("data") or [])
        if isinstance(row, dict)
    ]


async def insights(
    client: MetaClient, asset: MetaAsset, *, date_from: date, date_to: date
) -> dict[str, Any]:
    """Totals over a span, one metric at a time, each failing alone.

    ``metrics`` holds what answered; ``unavailable`` names what did not. A caller draws the
    first and says the second — a tile that silently is not there reads as a zero.
    """
    if (date_to - date_from).days + 1 > MAX_INSIGHT_DAYS:
        date_from = date_to - timedelta(days=MAX_INSIGHT_DAYS - 1)
    names = INSTAGRAM_METRICS if asset.kind == KIND_INSTAGRAM else PAGE_METRICS
    if asset.kind not in (KIND_INSTAGRAM, KIND_PAGE):
        return {"metrics": {}, "unavailable": [], "date_from": date_from, "date_to": date_to}

    async def one(name: str) -> tuple[str, float | None]:
        params: dict[str, Any] = {
            "metric": name,
            "since": date_from.isoformat(),
            # Meta's ``until`` is exclusive; ours is the last day the reader named.
            "until": (date_to + timedelta(days=1)).isoformat(),
            "period": "day",
        }
        if asset.kind == KIND_INSTAGRAM:
            params["metric_type"] = "total_value"
        try:
            answer = await client.get(f"{asset.external_id}/insights", params)
        except MetaError:
            return name, None
        return name, _total(answer)

    results = await asyncio.gather(*(one(name) for name in names))
    metrics = {name: value for name, value in results if value is not None}
    return {
        "metrics": metrics,
        "unavailable": [name for name, value in results if value is None],
        "date_from": date_from,
        "date_to": date_to,
    }


def _total(answer: dict[str, Any]) -> float | None:
    rows = answer.get("data")
    if not isinstance(rows, list) or not rows:
        return None
    row = rows[0]
    if not isinstance(row, dict):
        return None
    total = row.get("total_value")
    if isinstance(total, dict) and total.get("value") is not None:
        return _number(total["value"])
    values = row.get("values")
    if not isinstance(values, list):
        return None
    numbers = [_number(v.get("value")) for v in values if isinstance(v, dict)]
    numbers = [n for n in numbers if n is not None]
    return sum(numbers) if numbers else None


def _summary(value: Any) -> int | None:
    if not isinstance(value, dict):
        return None
    summary = value.get("summary")
    return _int(summary.get("total_count")) if isinstance(summary, dict) else None


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _stamp(value: Any) -> str | None:
    raw = str(value or "")
    if not raw:
        return None
    # ``2026-09-29T10:00:00+0000`` → an offset a JSON date parser accepts.
    if len(raw) >= 5 and raw[-5] in "+-" and raw[-3] != ":":
        raw = f"{raw[:-2]}:{raw[-2:]}"
    return raw
