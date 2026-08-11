"""A scriptable stand-in for the Google Ads API.

Installed through ``app.core.googleads.set_transport``, which is the module's only network seam.
Unset, every call goes to ``googleads.googleapis.com``, so a test that forgets to install this
fails loudly on connect rather than quietly passing.

It stubs at the **transport**, not at ``acting_as`` or at the adapter, and that is the point: a
request made through it travels the real OAuth client, the real header builder, the real paging
loop and the real error classifier. A fake one layer higher would let a header bug, a
``pageToken`` bug or a misread ``errorCode`` through untouched — which is exactly the class of
bug that only shows up against the live API, i.e. the most expensive place to find it.

Queries are matched by **substring of the GAQL**, because that is the one part of a request that
says what was asked for. ``fake.script("FROM campaign", rows)`` reads as the sentence it is.

Mutations are scripted **per resource** (``fake.mutation("campaignBudgets", …)``), which is not
cosmetic: the collection is the only thing separating a budget write from a keyword write, and one
shared answer for all of them is a fake that would pass a test asserting a budget was created
while the code posted to ``adGroupCriteria``. :meth:`FakeGoogleAds.mutations` reads back what was
actually sent, so the operations and the ``updateMask`` a test cares about are assertable.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

import httpx

TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"


def failure(
    group: str,
    value: str,
    *,
    status: int = 400,
    message: str = "nope",
    details: dict | None = None,
) -> httpx.Response:
    """A real ``GoogleAdsFailure``, in the shape v25 actually sends it.

    Built here rather than hand-written per test so every error path is exercised against the
    same three-levels-down envelope the classifier has to walk.
    """
    error: dict[str, Any] = {"errorCode": {group: value}, "message": message}
    if details:
        error["details"] = details
    return httpx.Response(
        status,
        json={
            "error": {
                "code": status,
                "message": message,
                "status": "INVALID_ARGUMENT",
                "details": [
                    {
                        "@type": (
                            "type.googleapis.com/google.ads.googleads.v25.errors.GoogleAdsFailure"
                        ),
                        "errors": [error],
                        "requestId": "req-fake",
                    }
                ],
            }
        },
    )


class FakeGoogleAds:
    """One Google Ads, scripted per query fragment."""

    def __init__(self) -> None:
        #: Customer ids ``listAccessibleCustomers`` reports as directly granted.
        self.accessible: list[str] = []
        #: ``(fragment, response)`` in insertion order; the **first** match wins, so a test can
        #: script a narrow answer before a broad one.
        self._scripts: list[tuple[str, Any]] = []
        #: Answers for the custom verbs (``generateKeywordIdeas``), keyed by verb name.
        self.verbs: dict[str, Any] = {}
        #: Answers for ``:mutate``, keyed by the **collection** (``campaignBudgets``,
        #: ``adGroupCriteria``, …). Keyed by collection rather than by the bare verb because
        #: every mutate path ends in the same ``:mutate`` — one shared answer would let a test
        #: assert a budget was created while the code posted to the keywords endpoint.
        self.mutates: dict[str, Any] = {}
        #: Every request that arrived: ``(method, url, headers, body)``. Assertions about the
        #: `login-customer-id` header and the number of round trips read from here.
        self.calls: list[tuple[str, str, dict[str, str], dict[str, Any]]] = []

    # --- scripting ----------------------------------------------------------------------- #

    def script(self, fragment: str, rows: list[dict] | httpx.Response, *, pages: int = 1) -> None:
        """Answer any GAQL containing ``fragment`` with ``rows``.

        ``pages`` > 1 splits them across that many ``nextPageToken`` responses, which is how the
        paging loop gets exercised: a client that ignores the token silently returns a prefix,
        and no functional assertion about the first page would ever notice.
        """
        if isinstance(rows, httpx.Response):
            self._scripts.append((fragment, rows))
            return
        if pages <= 1:
            self._scripts.append((fragment, {"results": rows}))
            return
        size = max(1, -(-len(rows) // pages))
        chunks = [rows[i : i + size] for i in range(0, len(rows), size)] or [[]]
        self._scripts.append(
            (
                fragment,
                [
                    {
                        "results": chunk,
                        **(
                            {"nextPageToken": f"page-{index + 1}"}
                            if index + 1 < len(chunks)
                            else {}
                        ),
                    }
                    for index, chunk in enumerate(chunks)
                ],
            )
        )

    def _answer_for(self, query: str) -> Any:
        for fragment, payload in self._scripts:
            if fragment in query:
                return payload
        return {"results": []}

    def mutation(
        self,
        resource: str,
        *,
        resource_names: list[str | None] | None = None,
        partial_failure: list[tuple[int, str, str]] | None = None,
        response: httpx.Response | None = None,
    ) -> None:
        """Script one ``<resource>:mutate``.

        ``resource_names`` is one entry per operation; ``None`` leaves that slot **empty**, which
        is exactly what Google sends for an operation refused inside a partial-failure batch and
        the shape a client that assumes "a result means it worked" gets wrong.

        ``partial_failure`` is ``(operation index, error group, error value)``, assembled into the
        real ``partialFailureError`` envelope: a bare ``google.rpc.Status`` on an **HTTP 200**,
        with the index carried in ``location.fieldPathElements``. Built here so no test can assert
        against a shape the API never sends.
        """
        if response is not None:
            self.mutates[resource] = response
            return
        payload: dict[str, Any] = {
            "results": [
                {} if name is None else {"resourceName": name} for name in (resource_names or [])
            ]
        }
        if partial_failure:
            payload["partialFailureError"] = {
                "code": 3,
                "message": "partial failure",
                "details": [
                    {
                        "@type": (
                            "type.googleapis.com/google.ads.googleads.v25.errors.GoogleAdsFailure"
                        ),
                        "errors": [
                            {
                                "errorCode": {group: value},
                                "message": f"{value.lower().replace('_', ' ')}",
                                "location": {
                                    "fieldPathElements": [
                                        {"fieldName": "operations", "index": index}
                                    ]
                                },
                            }
                            for index, group, value in partial_failure
                        ],
                        "requestId": "req-fake",
                    }
                ],
            }
        self.mutates[resource] = payload

    # --- transport ------------------------------------------------------------------------ #

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        body: dict[str, Any] = {}
        if request.content:
            try:
                body = json.loads(request.content)
            except ValueError:
                body = {}
        self.calls.append((request.method, url, dict(request.headers), body))

        if url.startswith(TOKEN_ENDPOINT):
            # authlib refreshes before the first call because the stored access token is stale.
            return httpx.Response(
                200,
                json={
                    "access_token": "ya29.fake-access-token",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )

        path = urlparse(url).path
        if path.endswith("/customers:listAccessibleCustomers"):
            return httpx.Response(
                200,
                json={"resourceNames": [f"customers/{cid}" for cid in self.accessible]},
            )

        last = path.rsplit("/", 1)[-1]
        if last.endswith(":mutate") and not path.endswith("googleAds:mutate"):
            # `campaignBudgets:mutate` → `campaignBudgets`. Keyed on the collection, because the
            # verb is `mutate` for every resource and a shared answer would make a budget test
            # pass against a keyword write.
            resource = last.rsplit(":", 1)[0]
            answer = self.mutates.get(resource, {"results": []})
            if isinstance(answer, httpx.Response):
                return answer
            return httpx.Response(200, json=answer)

        if ":" in last and not path.endswith(
            ("googleAds:search", "googleAds:searchStream", "googleAds:mutate")
        ):
            verb = path.rsplit(":", 1)[-1]
            answer = self.verbs.get(verb, {})
            if isinstance(answer, httpx.Response):
                return answer
            return httpx.Response(200, json=answer)

        if path.endswith("googleAds:search"):
            answer = self._answer_for(str(body.get("query", "")))
            if isinstance(answer, httpx.Response):
                return answer
            if isinstance(answer, list):
                # A multi-page script: which page is decided by the token we were handed.
                token = body.get("pageToken")
                index = int(str(token).rsplit("-", 1)[-1]) if token else 0
                return httpx.Response(200, json=answer[min(index, len(answer) - 1)])
            return httpx.Response(200, json=answer)

        return httpx.Response(200, json={})

    # --- assertions ------------------------------------------------------------------------ #

    def queries(self) -> list[str]:
        """Every GAQL that was actually sent, in order."""
        return [
            str(body.get("query", ""))
            for _method, url, _headers, body in self.calls
            if url.endswith("googleAds:search")
        ]

    def last_headers(self) -> dict[str, str]:
        return self.calls[-1][2] if self.calls else {}

    def mutations(self, resource: str | None = None) -> list[tuple[str, dict[str, Any]]]:
        """``(collection, body)`` for every ``:mutate`` that was sent, in order.

        The body is what actually went to Google, so a test asserts on the real operations and the
        real ``updateMask`` rather than on the arguments a service was called with — which is the
        difference between proving the request is right and proving the code is self-consistent.
        """
        out: list[tuple[str, dict[str, Any]]] = []
        for _method, url, _headers, body in self.calls:
            last = urlparse(url).path.rsplit("/", 1)[-1]
            if not last.endswith(":mutate") or last == "googleAds:mutate":
                continue
            collection = last.rsplit(":", 1)[0]
            if resource is None or collection == resource:
                out.append((collection, body))
        return out


# --- row builders ---------------------------------------------------------------------------- #
#
# Google's JSON casing and its int64-as-string convention, applied here rather than in each test,
# so a test cannot accidentally assert against a shape the real API never sends.


def metrics(
    *,
    impressions: int = 0,
    clicks: int = 0,
    cost_micros: int = 0,
    conversions: float = 0.0,
    conversions_value: float = 0.0,
    all_conversions: float | None = None,
    **extra: Any,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        # int64 → a JSON **string**, which is what Google sends and what a client that treats it
        # as a number gets subtly wrong.
        "impressions": str(impressions),
        "clicks": str(clicks),
        "costMicros": str(cost_micros),
        "conversions": conversions,
        "conversionsValue": conversions_value,
    }
    if all_conversions is not None:
        out["allConversions"] = all_conversions
    out.update(extra)
    return out


def campaign_row(
    campaign_id: int,
    name: str,
    *,
    status: str = "ENABLED",
    channel: str = "SEARCH",
    budget_micros: int | None = None,
    target_cpa_micros: int | None = None,
    maximize_conversions_cpa_micros: int | None = None,
    **metric_kwargs: Any,
) -> dict[str, Any]:
    campaign: dict[str, Any] = {
        "id": str(campaign_id),
        "name": name,
        "status": status,
        "advertisingChannelType": channel,
        "biddingStrategyType": "TARGET_CPA",
        "startDateTime": "2026-01-01 00:00:00",
    }
    if target_cpa_micros is not None:
        campaign["targetCpa"] = {"targetCpaMicros": str(target_cpa_micros)}
    if maximize_conversions_cpa_micros is not None:
        # The *other* home of the same number — the fallback a client that reads only
        # `targetCpa` gets wrong for half of an agency's campaigns.
        campaign["maximizeConversions"] = {"targetCpaMicros": str(maximize_conversions_cpa_micros)}
    row: dict[str, Any] = {"campaign": campaign, "metrics": metrics(**metric_kwargs)}
    if budget_micros is not None:
        row["campaignBudget"] = {
            "amountMicros": str(budget_micros),
            "deliveryMethod": "STANDARD",
        }
    return row


def keyword_row(
    text: str,
    *,
    match_type: str = "EXACT",
    criterion_id: int = 1,
    quality_score: int | None = None,
    **metric_kwargs: Any,
) -> dict[str, Any]:
    criterion: dict[str, Any] = {
        "criterionId": str(criterion_id),
        "keyword": {"text": text, "matchType": match_type},
        "status": "ENABLED",
    }
    if quality_score is not None:
        criterion["qualityInfo"] = {"qualityScore": quality_score}
    return {
        "campaign": {"id": "1", "name": "Zoeken"},
        "adGroup": {"id": "11", "name": "Merk"},
        "adGroupCriterion": criterion,
        "metrics": metrics(**metric_kwargs),
    }


def search_term_row(term: str, *, status: str = "NONE", **metric_kwargs: Any) -> dict[str, Any]:
    return {
        "searchTermView": {"searchTerm": term, "status": status},
        "segments": {"searchTermMatchType": "BROAD"},
        "campaign": {"id": "1", "name": "Zoeken"},
        "adGroup": {"id": "11", "name": "Merk"},
        "metrics": metrics(**metric_kwargs),
    }
