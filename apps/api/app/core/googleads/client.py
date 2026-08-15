"""The Google Ads REST transport, shared by every module that speaks to Google Ads.

It lives in core rather than in a module because two modules need it — ``google_ads`` owns the
accounts and the depth, ``marketing`` draws the spend tile on a client's dashboard — and §6
forbids either importing the other's internals.

**No `google-ads` SDK.** The official library is synchronous gRPC over protobuf; this API is
async on Python 3.12, already carries ``httpx`` and ``authlib``, and speaks to every other Google
product over plain REST. The REST interface is a first-class surface (``googleAds:search`` and
per-resource ``:mutate``), so the SDK would buy nothing and cost a large synchronous dependency
in the middle of an async request path.

Three things here are not obvious from the API docs and cost a day each to find out:

* **Paging is `pageToken` only.** ``pageSize`` exists on the request message and Google ignores
  it: the page is a fixed 10 000 rows. So a bounded read cannot be expressed by asking for fewer
  rows — it is expressed by a `LIMIT` in the GAQL and by :data:`MAX_ROWS` here, and hitting the
  ceiling **raises** rather than returning a prefix (CLAUDE.md §17: silently importing the first
  2000 rows of 2500 is the worst outcome available, because it looks like it worked).
* **A retry is safe for a read and never for a write.** ``googleAds:search`` is idempotent;
  ``campaigns:mutate`` is not, and a retried create is a second campaign spending a second
  budget. So the backoff ladder is on :meth:`AdsClient.search` alone — the rule ``oxxa`` learned
  the same way with its command allow-list. A ``validateOnly`` mutate is a read in disguise and
  is retried like one.
* **Google says how long to wait, and it is better at it than we are.** ``QuotaErrorDetails``
  carries ``retryDelay``; the jittered ladder below is only what happens when it does not.

The network is off in tests: :data:`_transport` is the only seam, and a test that forgets to
install one fails on connect against ``googleads.googleapis.com`` rather than quietly passing.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx

from app.config import settings
from app.core.googleads.errors import (
    AdsError,
    AdsQuotaError,
    AdsUnavailable,
    classify,
    scrub,
)

if TYPE_CHECKING:
    from authlib.integrations.httpx_client import AsyncOAuth2Client

logger = logging.getLogger("schakl.googleads")

API_HOST = "https://googleads.googleapis.com"

#: The version this release is built against. Google sunsets an Ads API version roughly a year
#: after it ships and then answers **404** on every path under it — which is not a credential
#: problem, an account problem or a scope problem, so nothing in the picker's teaching states
#: fits it and the module simply looks broken (v18 sunset 2025-08-20). Overridable per install
#: via ``SCHAKL_GOOGLE_ADS_API_VERSION`` so a box that outlives this release can be bumped from
#: its compose file; keep this constant current anyway — the env var is the escape hatch, not
#: the plan. v25 shipped 2026-07-22.
DEFAULT_API_VERSION = "v25"

#: Ads is a dependency of a *screen*: fail fast rather than hold a request open. Reads are
#: allowed longer than the rest of Google because a 90-day search-terms query genuinely takes
#: seconds, and the caller has already released its database connection.
_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=20.0, pool=5.0)

#: Hard cap on one paginated read. An unbounded read is a build break (CLAUDE.md §9), and at
#: Google's fixed 10 000-row page this is one round trip in the common case and five at worst.
MAX_ROWS = 50_000

#: And the mechanical cap beside it, for the same reason ``cloudflare`` carries two: an endpoint
#: that keeps handing back a `nextPageToken` must not be walked forever just because the rows
#: stay under the product limit.
MAX_PAGES = 20

#: Attempts for a *read* that failed retryably. Three is the ladder Google's own libraries use.
MAX_ATTEMPTS = 3

#: Base seconds for the jittered exponential backoff, used only when Google names no delay.
_BACKOFF_BASE = 0.5
_BACKOFF_CAP = 8.0

#: Test seam — an ``httpx`` transport used instead of the network. Never set in production.
_transport: httpx.AsyncBaseTransport | None = None


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Install (or clear) the transport every Ads client uses. Tests only."""
    global _transport
    _transport = transport


def api_base() -> str:
    """``https://googleads.googleapis.com/<version>`` — resolved per call, never at import, so
    the version is a setting an operator can change without rebuilding the image."""
    version = (settings.google_ads_api_version or DEFAULT_API_VERSION).strip().strip("/")
    return f"{API_HOST}/{version}"


def normalise_customer_id(raw: str | None) -> str:
    """``"123-456-7890"`` → ``"1234567890"``. Every id Google takes in a path is bare digits.

    Applied at every boundary rather than trusted from one, because the same account arrives
    written three ways: hyphenated from a human, bare from the picker, and as the resource name
    ``customers/1234567890`` from a GAQL row.
    """
    text = str(raw or "")
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    return "".join(ch for ch in text if ch.isdigit())


def format_customer_id(raw: str | None) -> str:
    """``"1234567890"`` → ``"123-456-7890"`` — the form Google's own UI shows, for display only."""
    digits = normalise_customer_id(raw)
    if len(digits) != 10:
        return digits
    return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"


@dataclass
class QueryStats:
    """What one client instance cost, for the log line and the response envelope."""

    queries: int = 0
    rows: int = 0
    mutations: int = 0


@dataclass(frozen=True)
class AdsCredentials:
    """Everything one Ads call needs beyond the query itself.

    ``login_customer_id`` is the manager account the grant is held on. It is not decoration: a
    user granted access to an MCC has **no direct grant** on the clients beneath it, so without
    this header every call against a client account is made as someone with no access and 403s.
    """

    developer_token: str
    login_customer_id: str | None = None
    linked_customer_id: str | None = None

    def headers(self) -> dict[str, str]:
        out = {"developer-token": self.developer_token}
        if self.login_customer_id:
            out["login-customer-id"] = normalise_customer_id(self.login_customer_id)
        if self.linked_customer_id:
            out["linked-customer-id"] = normalise_customer_id(self.linked_customer_id)
        return out


class AdsClient:
    """Read and write one Google Ads account over REST.

    The OAuth half is *not* built here: it arrives as an authlib client from
    ``app.integrations.google.client.acting_as``, which owns the token vault, the refresh and the
    rotation. This class owns everything Ads-specific — the developer token, the manager header,
    the paging, the backoff and the error model.
    """

    def __init__(
        self,
        http: AsyncOAuth2Client,
        credentials: AdsCredentials,
        *,
        tool: str = "",
    ) -> None:
        self._http = http
        self._credentials = credentials
        self._tool = tool
        self.stats = QueryStats()

    # -- reads ------------------------------------------------------------------------------ #

    async def search(
        self,
        customer_id: str,
        query: str,
        *,
        max_rows: int = MAX_ROWS,
        context: str = "",
    ) -> list[dict[str, Any]]:
        """Every row of ``query`` against ``customer_id``, following ``nextPageToken``.

        Raises rather than truncating when ``max_rows`` or :data:`MAX_PAGES` is reached: a caller
        that wants fewer rows says so with a `LIMIT`, and one that gets a prefix without being
        told has no way to know its answer is wrong.
        """
        cid = normalise_customer_id(customer_id)
        if not cid:
            # Never build ``/customers/None/googleAds:search``: Google answers 404, which this
            # module's own error model reads as "the API version is sunset" — the most
            # misleading sentence available for what is really an unlinked account.
            raise AdsError("no customer id", status=None)

        rows: list[dict[str, Any]] = []
        page_token: str | None = None
        for _ in range(MAX_PAGES):
            body: dict[str, Any] = {"query": query}
            if page_token:
                body["pageToken"] = page_token
            payload = await self._post(
                f"{api_base()}/customers/{cid}/googleAds:search",
                body,
                retryable=True,
                context=context or "search",
            )
            results = payload.get("results") or []
            rows.extend(row for row in results if isinstance(row, dict))
            self.stats.rows = len(rows)
            if len(rows) > max_rows:
                raise AdsError(f"query returned more than {max_rows} rows", status=None)
            page_token = payload.get("nextPageToken")
            if not page_token:
                return rows
        raise AdsError(f"query did not finish within {MAX_PAGES} pages", status=None)

    async def search_one(
        self, customer_id: str, query: str, *, context: str = ""
    ) -> dict[str, Any] | None:
        """The first row, or ``None``. For the single-row reads (``FROM customer``)."""
        rows = await self.search(customer_id, query, max_rows=2, context=context)
        return rows[0] if rows else None

    # -- writes ----------------------------------------------------------------------------- #

    async def mutate(
        self,
        customer_id: str,
        resource: str,
        operations: list[dict[str, Any]],
        *,
        validate_only: bool = False,
        partial_failure: bool = False,
        context: str = "",
    ) -> dict[str, Any]:
        """One per-resource ``:mutate`` call — ``resource`` is the REST collection name.

        ``campaigns``, ``campaignBudgets``, ``adGroups``, ``adGroupCriteria``,
        ``campaignCriteria``, ``adGroupAds``, ``sharedSets``, ``sharedCriteria``,
        ``campaignSharedSets``.

        **Never retried unless ``validate_only``.** A retried create is a second campaign; a
        retried budget change is fine but this class cannot tell which is which from here, so it
        does not try. A caller that wants a retry decides that with the operation in hand.
        """
        cid = normalise_customer_id(customer_id)
        if not cid:
            raise AdsError("no customer id", status=None)
        body: dict[str, Any] = {"operations": operations}
        if validate_only:
            body["validateOnly"] = True
        if partial_failure:
            body["partialFailure"] = True
        payload = await self._post(
            f"{api_base()}/customers/{cid}/{resource}:mutate",
            body,
            retryable=validate_only,
            context=context or f"{resource}:mutate",
        )
        if not validate_only:
            self.stats.mutations += len(operations)
        return payload

    async def post(
        self, customer_id: str, verb: str, body: dict[str, Any], *, context: str = ""
    ) -> dict[str, Any]:
        """A customer-scoped custom verb (``:generateKeywordIdeas``, ``:listAccessibleCustomers``).

        Read-shaped, so retried like one.
        """
        cid = normalise_customer_id(customer_id)
        return await self._post(
            f"{api_base()}/customers/{cid}:{verb}", body, retryable=True, context=context or verb
        )

    async def accessible_customers(self) -> list[str]:
        """Customer ids this login was granted **directly** — usually just the agency's MCC.

        Deliberately not "the accounts an agency runs": access is granted to the manager, so
        expanding the hierarchy is a separate ``customer_client`` query the caller makes.
        """
        payload = await self._request(
            "GET",
            f"{api_base()}/customers:listAccessibleCustomers",
            None,
            retryable=True,
            context="listAccessibleCustomers",
        )
        return [
            normalise_customer_id(name)
            for name in payload.get("resourceNames") or []
            if normalise_customer_id(name)
        ]

    # -- transport -------------------------------------------------------------------------- #

    async def _post(
        self, url: str, body: dict[str, Any], *, retryable: bool, context: str
    ) -> dict[str, Any]:
        return await self._request("POST", url, body, retryable=retryable, context=context)

    async def _request(
        self,
        method: str,
        url: str,
        body: dict[str, Any] | None,
        *,
        retryable: bool,
        context: str,
    ) -> dict[str, Any]:
        headers = self._credentials.headers()
        attempts = MAX_ATTEMPTS if retryable else 1
        last: AdsError | None = None
        for attempt in range(attempts):
            self.stats.queries += 1
            try:
                response = await self._http.request(
                    method, url, headers=headers, json=body, timeout=_TIMEOUT
                )
            except httpx.TimeoutException as exc:
                last = AdsUnavailable(scrub(str(exc), self._credentials.developer_token))
            except httpx.HTTPError as exc:
                last = AdsUnavailable(scrub(str(exc), self._credentials.developer_token))
            else:
                if response.status_code < 400:
                    return self._decode(response)
                last = classify(
                    self._safe_json(response),
                    status=response.status_code,
                    fallback=response.text[:500],
                    secret=self._credentials.developer_token,
                )
                if not _is_retryable(last):
                    raise last
            if attempt + 1 < attempts:
                await asyncio.sleep(_delay(attempt, last))
        assert last is not None
        logger.warning("google ads %s failed after %s attempts: %s", context, attempts, last)
        raise last

    def _decode(self, response: httpx.Response) -> dict[str, Any]:
        payload = self._safe_json(response)
        if payload is None:
            # A 200 that is not JSON is a proxy or a captive portal, never Google.
            raise AdsUnavailable("non-JSON response", status=response.status_code)
        return payload

    @staticmethod
    def _safe_json(response: httpx.Response) -> dict[str, Any] | None:
        try:
            payload = response.json()
        except ValueError:
            return None
        return payload if isinstance(payload, dict) else None


@asynccontextmanager
async def ads_client(
    session: Any,
    org: Any,
    connection: Any,
    credentials: AdsCredentials,
    *,
    tool: str = "",
) -> AsyncIterator[AdsClient]:
    """An :class:`AdsClient` on one Google connection's grant.

    The OAuth half belongs to the ``google`` module — it owns the token vault, the refresh and
    the re-encryption — so this only wraps it. The import is deliberately inside the function:
    core must not depend on a module at import time, or an instance with ``google`` disabled
    would fail to boot instead of simply having no Google surfaces. The same lazy-import shape
    ``app.core.permissions.catalog`` uses to reach registered modules.

    In a request path, enter this **first** and then ``ctx.release_db()`` — it reads settings
    from the session, and the pooled connection must still be checked out for that.
    """
    from app.integrations.google.client import acting_as

    async with acting_as(session, org, connection, transport=_transport) as http:
        yield AdsClient(http, credentials, tool=tool)


def _is_retryable(exc: AdsError) -> bool:
    """Whether waiting could plausibly change the answer.

    A quota refusal can — it is a rate, not a verdict — with the one exception of the daily
    allowance, which will not reset inside a request. Everything authentication- or
    permission-shaped cannot: only a human changes those.
    """
    if isinstance(exc, AdsQuotaError):
        return exc.error_code != "quotaError.RESOURCE_EXHAUSTED"
    if isinstance(exc, AdsUnavailable):
        return True
    return exc.status is not None and exc.status >= 500


def _delay(attempt: int, exc: AdsError | None) -> float:
    """Google's own ``retryDelay`` when it gave one, else exponential backoff with full jitter."""
    if isinstance(exc, AdsQuotaError) and exc.retry_after:
        return min(exc.retry_after, _BACKOFF_CAP)
    ceiling = min(_BACKOFF_BASE * (2**attempt), _BACKOFF_CAP)
    return random.uniform(0, ceiling)  # noqa: S311 - jitter, not cryptography
