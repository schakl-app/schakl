"""The Tag Manager v2 REST transport. Business-licensed — see LICENSE.

Written against the API's own **discovery document**
(``https://tagmanager.googleapis.com/$discovery/rest?version=v2``, revision 20260812) rather than
from memory — CLAUDE.md §11 — which is what settled four things that are easy to get wrong:

* **There is no page size.** Every ``list`` method takes ``pageToken`` and *nothing else*: no
  ``pageSize``, no ``maxResults``. A bounded read therefore cannot be expressed by asking for
  fewer rows, only by :data:`MAX_ITEMS` and :data:`MAX_PAGES` here — and hitting either
  **raises** rather than returning a prefix (§17: silently returning the first 200 tags of 250
  is the worst answer available, because it looks like it worked).
* **The list key is the singular noun**, not ``items``: ``{"tag": [...]}``, ``{"container":
  [...]}``, ``{"containerVersionHeader": [...]}``. A caller names it; there is no guessing here.
* **A concurrent edit is a query parameter.** ``update`` takes ``fingerprint`` in the *query
  string*, and a mismatch is a 409. That is the whole of GTM's optimistic concurrency, and it is
  the difference between "your change landed" and "your change silently replaced somebody's".
* **``built_in_variables.create`` takes a repeated ``type`` query parameter and an empty body.**

**A retry is safe for a read and never for a write** — the rule ``google_ads`` and ``oxxa`` both
arrived at. ``tags.create`` is not idempotent: a retried create is a second tag firing a second
time on a client's website, and this class cannot tell a retryable create from a dangerous one, so
it does not try. Only ``GET`` is retried.

The network is off in tests: :data:`_transport` is the only seam, and a test that forgets to
install one fails on connect against ``tagmanager.googleapis.com`` rather than quietly passing.
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

from app.integrations.google_tag_manager.errors import (
    GtmError,
    GtmQuotaError,
    GtmUnavailable,
    classify,
)

if TYPE_CHECKING:
    from authlib.integrations.httpx_client import AsyncOAuth2Client

logger = logging.getLogger("schakl.gtm")

API_BASE = "https://tagmanager.googleapis.com/tagmanager/v2"

#: GTM is a dependency of a *screen*, so fail fast rather than hold a request open. Writes get a
#: longer write timeout than reads because a ``create_version`` on a large container genuinely
#: takes seconds — it compiles the whole container.
_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=20.0, pool=5.0)

#: Hard cap on one paginated read. GTM's own container limits sit far below this (a container
#: tops out in the hundreds of tags), so reaching it means something is wrong rather than large.
MAX_ITEMS = 2_000
#: And the mechanical cap beside it, for the reason ``cloudflare`` carries two: an endpoint that
#: keeps handing back a token must not be walked forever just because the rows stay under a limit.
MAX_PAGES = 20

#: Attempts for a *read* that failed retryably. Three is the ladder Google's own libraries use.
MAX_ATTEMPTS = 3
_BACKOFF_BASE = 0.5
_BACKOFF_CAP = 8.0

#: Test seam — an ``httpx`` transport used instead of the network. Never set in production.
_transport: httpx.AsyncBaseTransport | None = None


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Install (or clear) the transport every GTM client uses. Tests only."""
    global _transport
    _transport = transport


def api_url(path: str) -> str:
    """``accounts/1/containers/2`` → the absolute URL, with the path refused if it is not one.

    Ids reach this from three places — our own rows, a GTM response, and a caller naming a tag —
    so the segment shape is checked here rather than trusted from any one of them. A path that
    escapes upwards or carries a scheme is a bug or an attempt, and neither should become a
    request to some other host.
    """
    clean = str(path or "").strip().strip("/")
    if not clean or ".." in clean.split("/") or "://" in clean or clean.startswith("//"):
        raise GtmError(f"invalid tag manager resource path: {path!r}", status=None)
    return f"{API_BASE}/{clean}"


@dataclass
class GtmStats:
    """What one client instance cost, for the log line and the response envelope."""

    requests: int = 0
    items: int = 0
    writes: int = 0


class GtmClient:
    """Read and write one org's Tag Manager containers over REST.

    The OAuth half is *not* built here: it arrives as an authlib client from
    :func:`app.integrations.google.client.acting_as`, which owns the token vault, the refresh and
    the re-encryption. This class owns everything GTM-specific — the paging, the fingerprint,
    the backoff and the error model.
    """

    def __init__(self, http: AsyncOAuth2Client, *, tool: str = "") -> None:
        self._http = http
        self._tool = tool
        self.stats = GtmStats()

    # -- reads ------------------------------------------------------------------------------- #

    async def get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request("GET", api_url(path), params=params, retryable=True)

    async def list(
        self,
        path: str,
        key: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Every item of a GTM list method, following ``nextPageToken``.

        ``key`` is the singular noun the response wraps its array in (``"tag"``, ``"container"``,
        ``"containerVersionHeader"``). Named by the caller because GTM's key is per-method and a
        guess would silently return an empty list for the one method whose noun we got wrong.
        """
        url = api_url(path)
        out: list[dict[str, Any]] = []
        token: str | None = None
        for _ in range(MAX_PAGES):
            query = dict(params or {})
            if token:
                query["pageToken"] = token
            payload = await self._request("GET", url, params=query, retryable=True)
            out.extend(row for row in (payload.get(key) or []) if isinstance(row, dict))
            self.stats.items = len(out)
            if len(out) > MAX_ITEMS:
                raise GtmError(f"{path} returned more than {MAX_ITEMS} rows", status=None)
            token = payload.get("nextPageToken")
            if not token:
                return out
        raise GtmError(f"{path} did not finish within {MAX_PAGES} pages", status=None)

    # -- writes ------------------------------------------------------------------------------- #

    async def post(
        self,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """A create or a custom verb (``:create_version``, ``:publish``, ``:sync``).

        **Never retried.** A retried ``tags.create`` is a second tag on a client's website, and
        a retried ``:publish`` is a second version live. A caller that knows its call is safe to
        repeat decides that with the operation in hand.
        """
        payload = await self._request("POST", api_url(path), body=body, params=params)
        self.stats.writes += 1
        return payload

    async def put(
        self,
        path: str,
        body: dict[str, Any],
        *,
        fingerprint: str | None = None,
    ) -> dict[str, Any]:
        """An update, guarded by the fingerprint the read handed back.

        Passing it is what turns "somebody edited this in Tag Manager while you had the form
        open" into a 409 instead of a silent overwrite of their work. It is optional in Google's
        API and never optional here: every caller in this module reads before it writes.
        """
        params = {"fingerprint": fingerprint} if fingerprint else None
        payload = await self._request("PUT", api_url(path), body=body, params=params)
        self.stats.writes += 1
        return payload

    async def delete(self, path: str) -> None:
        await self._request("DELETE", api_url(path))
        self.stats.writes += 1

    # -- transport ---------------------------------------------------------------------------- #

    async def _request(
        self,
        method: str,
        url: str,
        *,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        retryable: bool = False,
    ) -> dict[str, Any]:
        attempts = MAX_ATTEMPTS if retryable else 1
        last: GtmError | None = None
        for attempt in range(attempts):
            self.stats.requests += 1
            try:
                response = await self._http.request(
                    method, url, params=params, json=body, timeout=_TIMEOUT
                )
            except httpx.HTTPError as exc:
                last = GtmUnavailable(str(exc))
            else:
                if response.status_code < 400:
                    return self._decode(response)
                last = classify(
                    self._safe_json(response),
                    status=response.status_code,
                    fallback=response.text[:500],
                )
                if not _is_retryable(last):
                    raise last
            if attempt + 1 < attempts:
                await asyncio.sleep(_delay(attempt))
        assert last is not None
        logger.warning("gtm %s %s failed after %s attempts: %s", method, self._tool, attempts, last)
        raise last

    @staticmethod
    def _decode(response: httpx.Response) -> dict[str, Any]:
        # 204 on delete, and ``:revert`` answers an empty body on some resources.
        if response.status_code == 204 or not response.content:
            return {}
        payload = GtmClient._safe_json(response)
        if payload is None:
            # A 2xx that is not JSON is a proxy or a captive portal, never Google.
            raise GtmUnavailable("non-JSON response", status=response.status_code)
        return payload

    @staticmethod
    def _safe_json(response: httpx.Response) -> dict[str, Any] | None:
        try:
            payload = response.json()
        except ValueError:
            return None
        return payload if isinstance(payload, dict) else None


@asynccontextmanager
async def gtm_client(
    session: Any, org: Any, connection: Any, *, tool: str = ""
) -> AsyncIterator[GtmClient]:
    """A :class:`GtmClient` on one Google connection's grant.

    In a request path, enter this **first** and then ``ctx.release_db()`` — it reads the org's
    Google settings from the session, and the pooled connection must still be checked out for
    that (docs/PERFORMANCE.md).
    """
    from app.integrations.google.client import acting_as

    async with acting_as(session, org, connection, transport=_transport) as http:
        yield GtmClient(http, tool=tool)


def _is_retryable(exc: GtmError) -> bool:
    """Whether waiting could plausibly change the answer.

    A quota refusal can — it is a rate. Everything authentication-, scope- or permission-shaped
    cannot: only a human changes those, and retrying spends the quota that is already short.
    """
    if isinstance(exc, GtmQuotaError):
        return True
    if isinstance(exc, GtmUnavailable):
        return True
    return exc.status is not None and exc.status >= 500


def _delay(attempt: int) -> float:
    """Exponential backoff with full jitter. GTM names no ``retryDelay`` of its own."""
    ceiling = min(_BACKOFF_BASE * (2**attempt), _BACKOFF_CAP)
    return random.uniform(0, ceiling)  # noqa: S311 - jitter, not cryptography
