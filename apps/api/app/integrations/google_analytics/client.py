"""The GA4 transport: two Google APIs, one credential (docs/GOOGLE_ANALYTICS.md).

Business-licensed — see LICENSE.

Google Analytics 4 answers through **two** services and a caller has to know which, because
neither one can answer the other's questions. The **Admin API** knows what exists — properties,
data streams, key events, custom dimensions, what a property is *configured* as. The **Data
API** knows what happened — reports, real-time, and the metadata document listing which
dimensions and metrics that property will actually accept. Asking the wrong one is a 404 about
a resource that plainly exists, which is the least helpful error available here, so the two
bases are named once and every call goes through :func:`get` / :func:`post`.

Both ride the ``google`` integration's ``acting_as`` client on the ``analytics.readonly`` scope
(CLAUDE.md §6a: the credential is a ``google_connections`` row and there is no second way to get
one). **Raw tokens never reach this module** — it is handed a client, never a credential.

Two things about GA4 that a parser written from memory gets wrong, both learned the expensive
way in ``marketing/sources/ga4.py`` and restated here because this module is the one that will
be extended:

* GA4 renamed *conversions* to **keyEvents**. Requesting the retired name does not return zero,
  it 400s the whole report — so nothing here ever asks for ``conversions``, and the display
  alias is added on the way out.
* A metric value arrives as a **string**, always, including integers. ``_num`` is not defensive
  programming, it is the wire format.
"""

from __future__ import annotations

from typing import Any

import httpx

ADMIN_API = "https://analyticsadmin.googleapis.com/v1beta"
DATA_API = "https://analyticsdata.googleapis.com/v1beta"

#: A page of an Admin API listing. Google's own ceiling is 200 and it is not negotiable.
PAGE_SIZE = 200
#: How many pages a listing will walk before it stops and *says* it stopped. An agency with more
#: properties than this has a search problem, not a paging problem (the Tag Manager rule, §3a).
MAX_PAGES = 10

#: The one network seam, and it is deliberately the **transport** rather than ``acting_as``
#: or the service (the ``google_tag_manager`` rule). A request made through a fake installed
#: here still travels the real OAuth client, the real path builder, the real paging loop and
#: the real error classifier — which is the layer the interesting bugs live in. Unset, every
#: call goes to Google, so a test that forgot to install one fails loudly on connect rather
#: than quietly passing.
_transport: httpx.AsyncBaseTransport | None = None


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Install (or clear) the test transport. Nothing in the app calls this."""
    global _transport
    _transport = transport


def transport() -> httpx.AsyncBaseTransport | None:
    return _transport


def property_path(property_id: str) -> str:
    """``properties/123456789`` from whichever half of it the caller was holding.

    Both spellings are in circulation — the Admin API answers ``property`` as the full resource
    name, the URL in somebody's browser carries the bare number — and a module that accepts only
    one of them refuses the id its own listing just handed out.
    """
    raw = str(property_id or "").strip()
    if raw.startswith("properties/"):
        raw = raw.split("/", 1)[1]
    return f"properties/{raw.strip('/')}"


def num(raw: Any) -> float:
    """A GA4 metric value as a number. Every metric arrives as a string, integers included."""
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


async def get(client: Any, base: str, path: str, params: dict[str, Any] | None = None) -> dict:
    response = await client.get(f"{base}/{path.lstrip('/')}", params=params or {})
    response.raise_for_status()
    return response.json() or {}


async def post(client: Any, base: str, path: str, body: dict[str, Any]) -> dict:
    response = await client.post(f"{base}/{path.lstrip('/')}", json=body)
    response.raise_for_status()
    return response.json() or {}


async def list_all(
    client: Any, base: str, path: str, key: str, params: dict[str, Any] | None = None
) -> tuple[list[dict], bool]:
    """Every page of an Admin API listing, and whether the walk was cut short.

    The second half of the return value is the point. A listing that stops at
    :data:`MAX_PAGES` and hands back what it has looks exactly like one that reached the end,
    and "we are not in that account" is the sentence a short list is read as (CLAUDE.md,
    ``google_tag_manager`` §3a). So the caller is told, and says so.
    """
    rows: list[dict] = []
    token: str | None = None
    for _ in range(MAX_PAGES):
        page = dict(params or {})
        page["pageSize"] = PAGE_SIZE
        if token:
            page["pageToken"] = token
        body = await get(client, base, path, page)
        rows.extend(body.get(key) or [])
        token = body.get("nextPageToken")
        if not token:
            return rows, False
    return rows, True


def report_rows(report: dict) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    """A Data API report as ``(dimension names, metric names, rows)``.

    The response carries its own headers, so the shape is read back from the answer rather than
    assumed from the request: a metric Google declined to return would otherwise shift every
    column one to the left, silently, with every number still a plausible number.
    """
    dimensions = [item.get("name", "") for item in report.get("dimensionHeaders") or []]
    metrics = [item.get("name", "") for item in report.get("metricHeaders") or []]
    rows: list[dict[str, Any]] = []
    for row in report.get("rows") or []:
        values = [item.get("value", "") for item in row.get("dimensionValues") or []]
        numbers = [num(item.get("value")) for item in row.get("metricValues") or []]
        rows.append(
            {
                "dimensions": dict(zip(dimensions, values, strict=False)),
                "metrics": dict(zip(metrics, numbers, strict=False)),
            }
        )
    return dimensions, metrics, rows


def totals(report: dict, metrics: list[str]) -> dict[str, float]:
    """The report's own totals row, keyed by metric name.

    Taken from Google rather than summed here, because three of the metrics an agency asks for
    most — ``engagementRate``, ``bounceRate``, ``averageSessionDuration`` — are **weighted**, and
    a column of ratios added up is a number that is not any of them (#381's combined-part rule).
    """
    for row in report.get("totals") or []:
        values = [num(item.get("value")) for item in row.get("metricValues") or []]
        return dict(zip(metrics, values, strict=False))
    return {}
