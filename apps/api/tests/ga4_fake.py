"""A scriptable stand-in for the two Google Analytics 4 APIs.

Installed through ``app.integrations.google_analytics.client.set_transport``, the module's only
network seam. Unset, every call goes to Google, so a test that forgot to install this fails
loudly on connect rather than quietly passing.

It stubs at the **transport**, so a request made through it still travels the real OAuth client,
the real path builder, the real Admin-API paging loop and the real error classifier — the layer
where the interesting mistakes live. The shapes are GA4's own: a metric value is a **string**
even when it is an integer, ``rowCount`` is the size of the whole answer rather than of the page,
and ``totals`` is a row of its own rather than a sum of the column.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

import httpx

TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"  # noqa: S105 — an endpoint, not a secret

PROPERTY = "375412345"
ACCOUNT = "98765"


def _metric_values(values: list[float]) -> list[dict[str, str]]:
    """GA4 sends every metric as a string. A parser that assumes numbers works until it does not."""
    return [{"value": str(value)} for value in values]


def report(
    *,
    dimensions: list[str] | None = None,
    metrics: list[str],
    rows: list[tuple[list[str], list[float]]],
    totals: list[float] | None = None,
    row_count: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One Data API report body, in the shape ``runReport`` actually answers it."""
    body: dict[str, Any] = {
        "dimensionHeaders": [{"name": name} for name in dimensions or []],
        "metricHeaders": [{"name": name, "type": "TYPE_INTEGER"} for name in metrics],
        "rows": [
            {
                "dimensionValues": [{"value": value} for value in dims],
                "metricValues": _metric_values(values),
            }
            for dims, values in rows
        ],
        "rowCount": row_count if row_count is not None else len(rows),
        "metadata": metadata or {"currencyCode": "EUR", "timeZone": "Europe/Amsterdam"},
    }
    if totals is not None:
        body["totals"] = [{"metricValues": _metric_values(totals)}]
    return body


class FakeGA4:
    """Both GA4 services in memory, with the last request kept so a test can assert on it."""

    def __init__(self) -> None:
        self.account_summaries: list[dict[str, Any]] = [
            {
                "account": f"accounts/{ACCOUNT}",
                "displayName": "breik.",
                "propertySummaries": [
                    {
                        "property": f"properties/{PROPERTY}",
                        "displayName": "klant.nl",
                        "propertyType": "PROPERTY_TYPE_ORDINARY",
                        "parent": f"accounts/{ACCOUNT}",
                    }
                ],
            }
        ]
        self.property_row: dict[str, Any] = {
            "name": f"properties/{PROPERTY}",
            "displayName": "klant.nl",
            "propertyType": "PROPERTY_TYPE_ORDINARY",
            "currencyCode": "EUR",
            "timeZone": "Europe/Amsterdam",
            "industryCategory": "BUSINESS_AND_INDUSTRIAL_MARKETS",
            "parent": f"accounts/{ACCOUNT}",
        }
        #: ``{listing key: [resource]}`` for the Admin API listings.
        self.listings: dict[str, list[dict[str, Any]]] = {
            "dataStreams": [
                {
                    "name": f"properties/{PROPERTY}/dataStreams/1",
                    "displayName": "klant.nl web",
                    "webStreamData": {"measurementId": "G-ABC123"},
                }
            ],
            "keyEvents": [{"name": f"properties/{PROPERTY}/keyEvents/1", "eventName": "offerte"}],
            "customDimensions": [],
            "customMetrics": [],
            "googleAdsLinks": [],
            "firebaseLinks": [],
        }
        self.retention: dict[str, Any] = {
            "name": f"properties/{PROPERTY}/dataRetentionSettings",
            "eventDataRetention": "FOURTEEN_MONTHS",
        }
        self.metadata_row: dict[str, Any] = {
            "dimensions": [
                {"apiName": "date", "uiName": "Date", "category": "Time"},
                {"apiName": "pagePath", "uiName": "Page path", "category": "Page / screen"},
            ],
            "metrics": [
                {"apiName": "sessions", "uiName": "Sessions", "type": "TYPE_INTEGER"},
                {
                    "apiName": "engagementRate",
                    "uiName": "Engagement rate",
                    "type": "TYPE_FLOAT",
                },
            ],
        }
        #: ``{path suffix: response}``; a Data API call with no script answers an empty report.
        self.scripted: dict[str, Any] = {}
        #: Every Data API body this fake was sent, newest last — a test asserts on the request.
        self.requests: list[tuple[str, dict[str, Any]]] = []
        #: ``{path suffix: (status, reason)}`` to make one call refuse.
        self.failures: dict[str, tuple[int, str | None]] = {}

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(TOKEN_ENDPOINT):
            # authlib refreshes before the first call, because the stored token is stale.
            return httpx.Response(
                200,
                json={
                    "access_token": "ya29.fake",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )
        parsed = urlparse(url)
        host, path = parsed.netloc, parsed.path
        body: dict[str, Any] = {}
        if request.content:
            body = json.loads(request.content)

        for needle, (status, reason) in self.failures.items():
            if needle in path:
                error: dict[str, Any] = {"code": status, "message": "nope", "status": "ERROR"}
                if reason:
                    error["details"] = [{"reason": reason}]
                return httpx.Response(status, json={"error": error})

        if "analyticsadmin" in host:
            return self._admin(path)
        self.requests.append((path, body))
        return self._data(path, body)

    def _admin(self, path: str) -> httpx.Response:
        suffix = path.removeprefix("/v1beta/")
        if suffix == "accountSummaries":
            return httpx.Response(200, json={"accountSummaries": self.account_summaries})
        if suffix == f"properties/{PROPERTY}":
            return httpx.Response(200, json=self.property_row)
        if suffix.endswith("/dataRetentionSettings"):
            return httpx.Response(200, json=self.retention)
        key = suffix.rsplit("/", 1)[-1]
        if key in self.listings:
            return httpx.Response(200, json={key: self.listings[key]})
        return httpx.Response(404, json={"error": {"code": 404, "message": "no such resource"}})

    def _data(self, path: str, body: dict[str, Any]) -> httpx.Response:
        suffix = path.removeprefix("/v1beta/")
        if suffix.endswith("/metadata"):
            return httpx.Response(200, json=self.metadata_row)
        for needle, answer in self.scripted.items():
            if suffix.endswith(needle):
                return httpx.Response(200, json=answer)
        if suffix.endswith(":batchRunReports"):
            count = len(body.get("requests") or [])
            return httpx.Response(
                200,
                json={"reports": [report(metrics=[], rows=[]) for _ in range(count)]},
            )
        return httpx.Response(200, json=report(metrics=[], rows=[]))
