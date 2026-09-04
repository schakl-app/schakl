"""The Search Console transport: two Google hosts, one credential (docs/GOOGLE_SEARCH_CONSOLE.md).

Business-licensed — see LICENSE.

Search Console answers on **two hosts** and a caller has to know which. The Webmasters API
(``www.googleapis.com/webmasters/v3``) is the old name for the same product and still carries
sites, sitemaps and the whole Search Analytics query surface; the URL Inspection API lives on
``searchconsole.googleapis.com/v1`` and nowhere else. Both are one Google API to enable in the
Cloud project ("Google Search Console API"), both ride the one ``webmasters.readonly`` scope, and
both are named once here so nothing else in the package spells a host.

Everything goes through the ``google`` integration's ``acting_as`` client. **Raw tokens never
reach this module** — it is handed a client, never a credential.

What the vocabulary below is, and why it is written down rather than passed through:

* Every list is **Google's own enum, verified against the discovery document** rather than
  remembered (revision :data:`API_REVISION_CHECKED`, CLAUDE.md §11: an integration is written
  from a document, never from memory). The lower-case spellings are the ones the REST body
  accepts; the discovery document lists them upper-case and the two are interchangeable.
* :data:`GENERATIVE_AI_SEARCH_TYPES` is **empty on purpose**. Search Console gained a
  *Generative AI performance report* in June 2026 — impressions in AI Overviews and AI Mode by
  page, country, device and date — and as of the discovery document checked above the Search
  Analytics API still refuses every value but the six in :data:`SEARCH_TYPES`. The report is UI
  and export-button only. The tuple is the seam: the day Google publishes a search type for it,
  it is added here and ``ai_visibility`` starts answering numbers instead of a state — nothing
  else in the package, on the dashboard or in the tool catalog changes shape.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

WEBMASTERS_API = "https://www.googleapis.com/webmasters/v3"
INSPECTION_API = "https://searchconsole.googleapis.com/v1"
CONSOLE_URL = "https://search.google.com/search-console"

#: The revision of Google's discovery document (``searchconsole.googleapis.com/$discovery/rest``)
#: the vocabulary below was read from. A reader who finds a value missing here checks that
#: document against this number before assuming the omission was ours.
API_REVISION_CHECKED = "20260902"

#: ``type`` on a Search Analytics query. ``web`` is Google's default and the only one that
#: includes AI Overviews — folded into it without a way to split them out, which is exactly the
#: gap the Generative AI report exists to fill and the API does not.
SEARCH_TYPES: tuple[str, ...] = ("web", "image", "video", "news", "discover", "googleNews")
#: See the module docstring. Empty until Google's API says otherwise.
GENERATIVE_AI_SEARCH_TYPES: tuple[str, ...] = ()
#: The report's own place in the console, relative to :data:`CONSOLE_URL`.
GENERATIVE_AI_REPORT_PATH = "performance/search-analytics/ai"

DIMENSIONS: tuple[str, ...] = (
    "query",
    "page",
    "country",
    "device",
    "searchAppearance",
    "date",
    "hour",
)
#: The dimensions a ``dimensionFilterGroups`` clause may name: everything but the two clocks.
FILTER_DIMENSIONS: tuple[str, ...] = ("query", "page", "country", "device", "searchAppearance")
AGGREGATIONS: tuple[str, ...] = ("auto", "byProperty", "byPage", "byNewsShowcasePanel")
DATA_STATES: tuple[str, ...] = ("final", "all", "hourly_all")
METRICS: tuple[str, ...] = ("clicks", "impressions", "ctr", "position")
#: The metric of the four where a fall is an improvement.
LOWER_IS_BETTER: frozenset[str] = frozenset({"position"})

#: How far back the ``hour`` dimension answers. Google keeps hourly rows for the last ten days
#: (the April 2025 announcement); asking for more is not refused, it is answered empty.
HOURLY_DAYS = 10
#: Ceilings. Google will return 25 000 rows and a model will read none of them.
MAX_ROWS = 1000
DEFAULT_ROWS = 25
#: How many rows a locally-applied ``order`` is computed over. Google ranks by clicks and
#: offers no other sort, so "top pages by impressions" is a sort over this many clicks-ranked
#: rows — stated in the answer's warnings, because a top-25 by impressions out of the first
#: thousand by clicks is not the same list as a top-25 by impressions.
ORDER_WINDOW = 1000

#: The one network seam, at the **transport** (the ``google_analytics`` rule). A request made
#: through a fake installed here still travels the real OAuth client, the real path builder
#: and the real error classifier. Unset, every call goes to Google, so a test that forgot to
#: install one fails loudly on connect rather than quietly passing.
_transport: httpx.AsyncBaseTransport | None = None


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Install (or clear) the test transport. Nothing in the app calls this."""
    global _transport
    _transport = transport


def transport() -> httpx.AsyncBaseTransport | None:
    return _transport


def site_url(raw: str) -> str:
    """A Search Console ``siteUrl`` from whatever the caller was holding.

    Both property kinds are in circulation and neither is what a person types: a domain
    property is ``sc-domain:klant.nl`` and a URL-prefix property is ``https://www.klant.nl/``,
    trailing slash included. A bare hostname (``klant.nl``) is read as the domain property,
    because that is the one an agency verifies today and the one a model will spell.
    """
    value = str(raw or "").strip()
    if not value:
        return value
    if value.startswith("sc-domain:") or "://" in value:
        return value
    return f"sc-domain:{value}"


def site_key(url: str) -> str:
    """The ``siteUrl`` as a URL path segment — every character encoded, ``/`` included."""
    return quote(site_url(url), safe="")


def display_name(url: str) -> str:
    """What a list prints for a property: the domain without its ``sc-domain:`` prefix."""
    value = site_url(url)
    return value[len("sc-domain:") :] if value.startswith("sc-domain:") else value


def site_type(url: str) -> str:
    return "domain" if site_url(url).startswith("sc-domain:") else "url_prefix"


def console_url(url: str, path: str = "") -> str:
    """A deep link into the console for ``url`` — the property picker's own ``resource_id``."""
    base = f"{CONSOLE_URL}/{path}" if path else CONSOLE_URL
    return f"{base}?resource_id={site_key(url)}"


def generative_ai_report_url(url: str) -> str:
    """The Generative AI performance report for this property, in the console.

    The one place the AI-visibility numbers exist today (see the module docstring), and the
    link the marketing dashboard, the integration and the assistant all hand out — one function,
    so three surfaces cannot point at three URLs.
    """
    return console_url(url, GENERATIVE_AI_REPORT_PATH)


def num(raw: Any) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


async def get(client: Any, url: str, params: dict[str, Any] | None = None) -> dict:
    response = await client.get(url, params=params or {})
    response.raise_for_status()
    return response.json() or {}


async def post(client: Any, url: str, body: dict[str, Any]) -> dict:
    response = await client.post(url, json=body)
    response.raise_for_status()
    return response.json() or {}


def rows_out(rows: list[dict], dimensions: list[str]) -> list[dict[str, Any]]:
    """Search Analytics rows as ``{dimensions: {...}, metrics: {...}}``.

    Google answers ``keys`` as a positional list in the order the request named its
    dimensions; a row for a query with no dimensions carries no ``keys`` at all. The four
    metrics are always present and always numbers — ``ctr`` a fraction, ``position`` an
    average — so the shape is fixed and the reader never has to know the order.
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        keys = [str(value) for value in (row.get("keys") or [])]
        out.append(
            {
                "dimensions": dict(zip(dimensions, keys, strict=False)),
                "metrics": {metric: num(row.get(metric)) for metric in METRICS},
            }
        )
    return out
