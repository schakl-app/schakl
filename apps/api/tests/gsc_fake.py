"""A scriptable stand-in for the Google Search Console APIs.

Installed through ``app.integrations.google_search_console.client.set_transport``, the module's
only network seam. Unset, every call goes to Google, so a test that forgot to install this fails
loudly on connect rather than quietly passing.

It stubs at the **transport**, so a request made through it still travels the real OAuth client,
the real path builder and the real error classifier — the layer where the interesting mistakes
live. The shapes are Search Console's own: a Search Analytics row carries its group-by values as
a positional ``keys`` list and always all four metrics, a sitemap's ``errors`` count is a
**string**, and a query with no dimensions answers one row with no ``keys`` at all.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from urllib.parse import unquote, urlparse

import httpx

TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"  # noqa: S105 — an endpoint, not a secret

SITE = "sc-domain:klant.nl"
PREFIX_SITE = "https://www.klant.nl/"


def row(keys: list[str] | None, clicks: float, impressions: float, position: float) -> dict:
    """One Search Analytics row as Google sends it — ``ctr`` derived, ``keys`` positional."""
    body: dict[str, Any] = {
        "clicks": clicks,
        "impressions": impressions,
        "ctr": (clicks / impressions) if impressions else 0.0,
        "position": position,
    }
    if keys is not None:
        body["keys"] = keys
    return body


class FakeSearchConsole:
    """Both Search Console hosts in memory, with every request kept so a test can assert on it."""

    def __init__(self) -> None:
        self.sites: list[dict[str, Any]] = [
            {"siteUrl": SITE, "permissionLevel": "siteOwner"},
            {"siteUrl": PREFIX_SITE, "permissionLevel": "siteFullUser"},
            {"siteUrl": "sc-domain:ander.nl", "permissionLevel": "siteUnverifiedUser"},
        ]
        self.sitemaps: list[dict[str, Any]] = [
            {
                "path": "https://www.klant.nl/sitemap.xml",
                "lastSubmitted": "2026-08-01T10:00:00.000Z",
                "lastDownloaded": "2026-09-03T04:12:00.000Z",
                "isPending": False,
                "isSitemapsIndex": True,
                "type": "sitemap",
                "errors": "0",
                "warnings": "2",
                "contents": [{"type": "web", "submitted": "143", "indexed": "0"}],
            }
        ]
        #: What a Search Analytics query answers. A callable decides from the request body —
        #: a test scripts "the compared window answers this, the current one that" by looking
        #: at ``startDate``; the default answers one totals row for a dimension-less query and
        #: nothing for anything else.
        self.analytics: Callable[[dict[str, Any]], dict[str, Any]] = self._default_analytics
        self.inspection: dict[str, Any] = {
            "inspectionResult": {
                "inspectionResultLink": "https://search.google.com/search-console/inspect?x=1",
                "indexStatusResult": {
                    "verdict": "PASS",
                    "coverageState": "Submitted and indexed",
                    "robotsTxtState": "ALLOWED",
                    "indexingState": "INDEXING_ALLOWED",
                    "lastCrawlTime": "2026-09-02T11:22:33Z",
                    "pageFetchState": "SUCCESSFUL",
                    "googleCanonical": "https://www.klant.nl/fietsen/",
                    "userCanonical": "https://www.klant.nl/fietsen/",
                    "crawledAs": "MOBILE",
                    "sitemap": ["https://www.klant.nl/sitemap.xml"],
                    "referringUrls": ["https://www.klant.nl/"],
                },
                "richResultsResult": {
                    "verdict": "PASS",
                    "detectedItems": [{"richResultType": "Product snippets", "items": []}],
                },
            }
        }
        #: Every request this fake was sent, newest last: ``(path, body)``.
        self.requests: list[tuple[str, dict[str, Any]]] = []
        #: ``{path needle: (status, reason)}`` to make one call refuse.
        self.failures: dict[str, tuple[int, str | None]] = {}

    @staticmethod
    def _default_analytics(body: dict[str, Any]) -> dict[str, Any]:
        if not body.get("dimensions"):
            return {"rows": [row(None, 120, 4000, 8.4)], "responseAggregationType": "byProperty"}
        return {"rows": []}

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(TOKEN_ENDPOINT):
            # authlib refreshes before the first call, because the stored token is stale.
            return httpx.Response(
                200,
                json={"access_token": "ya29.fake", "expires_in": 3600, "token_type": "Bearer"},
            )
        path = unquote(urlparse(url).path)
        body: dict[str, Any] = {}
        if request.content:
            body = json.loads(request.content)
        self.requests.append((path, body))

        for needle, (status, reason) in self.failures.items():
            if needle in path:
                error: dict[str, Any] = {"code": status, "message": "nope", "status": "ERROR"}
                if reason:
                    error["errors"] = [{"reason": reason, "message": "nope"}]
                return httpx.Response(status, json={"error": error})

        if path.endswith("/urlInspection/index:inspect"):
            return httpx.Response(200, json=self.inspection)
        if path.endswith("/webmasters/v3/sites"):
            return httpx.Response(200, json={"siteEntry": self.sites})
        if path.endswith("/searchAnalytics/query"):
            return httpx.Response(200, json=self.analytics(body))
        if path.endswith("/sitemaps"):
            return httpx.Response(200, json={"sitemap": self.sitemaps})
        if "/sitemaps/" in path:
            wanted = path.rsplit("/sitemaps/", 1)[1]
            for item in self.sitemaps:
                if item["path"] == wanted:
                    return httpx.Response(200, json=item)
            return httpx.Response(404, json={"error": {"code": 404, "message": "no sitemap"}})
        if "/webmasters/v3/sites/" in path:
            wanted = path.rsplit("/sites/", 1)[1]
            for item in self.sites:
                if item["siteUrl"] == wanted:
                    return httpx.Response(200, json=item)
            return httpx.Response(404, json={"error": {"code": 404, "message": "no site"}})
        return httpx.Response(404, json={"error": {"code": 404, "message": "no such resource"}})
