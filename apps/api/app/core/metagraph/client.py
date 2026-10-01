"""The Graph / Marketing API transport. One pipe for Pages, Instagram and ads.

Written from Meta's published reference (docs/META.md) and **never exercised against a live
token** — so every parse here is defensive, and docs/META.md §11 is the checklist for the day
one arrives (the OXXA rule, CLAUDE.md §10).

What is settled here so no caller has to think about it:

* **The version is pinned in the path** and is a setting, not a constant spelled at call sites.
  A Marketing API version lives about a year; an unversioned call silently follows whatever the
  app dashboard says.
* **The host is a setting too** (``SCHAKL_META_GRAPH_URL``), Microsoft's rule: a test stack
  points it at a stand-in without a code change, and nothing else may spell the host.
* **The token travels in the ``Authorization`` header**, never in the query string, so it cannot
  reach an access log through a URL. ``appsecret_proof`` rides every call where the app secret
  is known: harmless when the app does not require it, mandatory the day somebody switches
  "Require App Secret" on.
* **A retry is safe for a read and never for a write.** Meta has no idempotency key, so a
  retried create is a second post on a client's Page or a second campaign on a second budget.
  Only ``GET`` is retried; a write that got no answer is of *unknown* outcome and the caller
  looks before it tries again.
* **Paging follows ``paging.next`` until it is absent** — a short page is not the last page, and
  an empty page may still carry ``next``. Over the cap **raises** rather than returning a prefix
  (§17).
* **Nested values are JSON-encoded form fields**, which is how the Marketing API takes
  ``targeting`` and ``object_story_spec``.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import random
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from typing import Any

import httpx

from app.config import settings
from app.core.metagraph.errors import (
    MetaAppSecretError,
    MetaError,
    MetaUnavailable,
    classify,
    is_retryable,
    scrub,
)

logger = logging.getLogger("schakl.meta")

#: Meta is a dependency of a screen, so fail fast. Writes get longer: publishing a photo makes
#: Meta fetch or receive the bytes before it answers.
_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=60.0, pool=5.0)

#: Hard cap on one paginated read, and the mechanical cap beside it (cloudflare carries two for
#: the same reason: an endpoint that keeps handing back ``next`` must not be walked for ever).
MAX_ITEMS = 2_000
MAX_PAGES = 40
#: Rows asked for per page. Meta's default is 25; most edges allow 100.
PAGE_SIZE = 100

#: Where the reels flow uploads to. A different host from the Graph API, named by Meta in the
#: ``upload_url`` it answers with — and checked against this before a token is sent there.
UPLOAD_HOST = "rupload.facebook.com"

MAX_ATTEMPTS = 3
_BACKOFF_BASE = 0.5
_BACKOFF_CAP = 8.0

#: Test seam — an ``httpx`` transport used instead of the network. Never set in production.
_transport: httpx.AsyncBaseTransport | None = None


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Install (or clear) the transport every Meta client uses. Tests only."""
    global _transport
    _transport = transport


def graph_base() -> str:
    """``https://graph.facebook.com/v26.0`` — read per call, never frozen at import."""
    return f"{settings.meta_graph_url.rstrip('/')}/{settings.meta_api_version.strip('/')}"


def graph_url(path: str) -> str:
    """A relative Graph path → the absolute URL, refused if it is not a path.

    Ids reach this from our own rows, from Meta's answers and from callers, so the shape is
    checked here rather than trusted from any of them: a path that escapes upwards or carries a
    scheme must never become a request to another host.
    """
    clean = str(path or "").strip().strip("/")
    if (
        not clean
        or ".." in clean.split("/")
        or "://" in clean
        or clean.startswith("//")
        or any(ch in clean for ch in ("?", "#", " "))
    ):
        raise MetaError(f"invalid graph path: {path!r}")
    return f"{graph_base()}/{clean}"


def appsecret_proof(token: str, app_secret: str) -> str:
    """HMAC-SHA256 of the token keyed with the app secret, hex — Meta's proof of server."""
    return hmac.new(app_secret.encode(), token.encode(), hashlib.sha256).hexdigest()


def encode_params(params: dict[str, Any] | None) -> dict[str, str]:
    """Graph parameters as form fields: nested values JSON-encoded, booleans as words.

    ``None`` values are dropped, which is what lets a caller pass an optional field
    unconditionally — and is why "clear this field" is never expressed as ``None`` here.
    """
    out: dict[str, str] = {}
    for key, value in (params or {}).items():
        if value is None:
            continue
        if isinstance(value, bool):
            out[key] = "true" if value else "false"
        elif isinstance(value, dict | list | tuple):
            out[key] = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
        else:
            out[key] = str(value)
    return out


@dataclass(frozen=True)
class MetaCredentials:
    """What one call authenticates with. The app secret is optional and improves every call."""

    token: str
    app_id: str | None = None
    app_secret: str | None = None

    def secrets(self) -> tuple[str | None, ...]:
        return (self.token, self.app_secret)


@dataclass
class MetaUsage:
    """What Meta last said about how close this caller is to a limit."""

    #: Highest of call_count / total_cputime / total_time across the usage headers, 0–100+.
    percent: int = 0
    #: Minutes until a throttled caller may call again, where Meta said so.
    regain_minutes: int | None = None
    #: ``development_access`` / ``standard_access`` — the Marketing API tier Meta reports.
    ads_tier: str | None = None


@dataclass
class MetaStats:
    requests: int = 0
    writes: int = 0
    items: int = 0
    usage: MetaUsage = field(default_factory=MetaUsage)


def read_usage(headers: httpx.Headers, into: MetaUsage) -> None:
    """Fold the three usage headers into ``into``. Never raises: a header is a hint."""
    for name in ("x-app-usage", "x-ad-account-usage"):
        _fold(_json(headers.get(name)), into)
    business = _json(headers.get("x-business-use-case-usage"))
    if isinstance(business, dict):
        for entries in business.values():
            for entry in entries if isinstance(entries, list) else ():
                _fold(entry, into)


def _fold(entry: Any, into: MetaUsage) -> None:
    if not isinstance(entry, dict):
        return
    for key in ("call_count", "total_cputime", "total_time", "acc_id_util_pct"):
        try:
            into.percent = max(into.percent, int(float(entry.get(key) or 0)))
        except (TypeError, ValueError):
            continue
    wait = entry.get("estimated_time_to_regain_access")
    try:
        if wait:
            into.regain_minutes = max(into.regain_minutes or 0, int(wait))
    except (TypeError, ValueError):
        pass
    tier = entry.get("ads_api_access_tier")
    if tier:
        into.ads_tier = str(tier)


def _json(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


class MetaClient:
    """Read and write through the Graph API as one token."""

    def __init__(self, http: httpx.AsyncClient, credentials: MetaCredentials, *, tool: str = ""):
        self._http = http
        self.credentials = credentials
        self._tool = tool
        self.stats = MetaStats()

    def acting_as(self, token: str) -> MetaClient:
        """The same connection, a different token — a Page's own, derived from the system user's.

        Shares the stats so one request's cost is still one number.
        """
        other = MetaClient(self._http, replace(self.credentials, token=token), tool=self._tool)
        other.stats = self.stats
        return other

    # -- reads ------------------------------------------------------------------------------- #

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request("GET", graph_url(path), params=params, retryable=True)

    async def get_all(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        max_items: int = MAX_ITEMS,
    ) -> list[dict[str, Any]]:
        """Every row of an edge, following ``paging.next``.

        The first request is built here; every following one is Meta's own ``next`` URL, asked
        for verbatim minus its credentials — the size and the cursor are the provider's to
        choose (the Cloudflare paging rule, CLAUDE.md §10).
        """
        query = dict(params or {})
        query.setdefault("limit", PAGE_SIZE)
        url: str | None = graph_url(path)
        out: list[dict[str, Any]] = []
        for _ in range(MAX_PAGES):
            payload = await self._request("GET", url, params=query, retryable=True)
            rows = payload.get("data")
            out.extend(row for row in (rows or []) if isinstance(row, dict))
            self.stats.items = len(out)
            if len(out) > max_items:
                raise MetaError(f"{path} returned more than {max_items} rows")
            paging = payload.get("paging")
            following = paging.get("next") if isinstance(paging, dict) else None
            if not following:
                return out
            url, query = _next_page(str(following))
        raise MetaError(f"{path} did not finish within {MAX_PAGES} pages")

    # -- writes ------------------------------------------------------------------------------ #

    async def post(
        self,
        path: str,
        data: dict[str, Any] | None = None,
        *,
        files: dict[str, tuple[str, bytes, str]] | None = None,
    ) -> dict[str, Any]:
        """A create, an update or a verb. **Never retried** — see the module docstring."""
        payload = await self._request("POST", graph_url(path), data=data, files=files)
        self.stats.writes += 1
        return payload

    async def delete(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = await self._request("DELETE", graph_url(path), params=params)
        self.stats.writes += 1
        return payload

    async def upload_from_url(self, upload_url: str, file_url: str) -> dict[str, Any]:
        """Tell Meta's upload host to fetch a video from ``file_url`` (the reels flow).

        ``upload_url`` arrives **in a response**, so it is data: the token is only ever sent to
        it when its host is one of the two this integration knows — Meta's resumable-upload
        host, or the configured Graph host (which is what a test stack answers on). Anything
        else is refused rather than trusted.
        """
        target = httpx.URL(upload_url)
        allowed = {UPLOAD_HOST, httpx.URL(settings.meta_graph_url).host}
        if target.scheme not in ("https", httpx.URL(settings.meta_graph_url).scheme) or (
            target.host not in allowed
        ):
            raise MetaError(f"refusing to upload to an unknown host: {target.host!r}")
        self.stats.requests += 1
        try:
            response = await self._http.post(
                str(target),
                headers={
                    "Authorization": f"OAuth {self.credentials.token}",
                    "file_url": file_url,
                },
                timeout=_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise MetaUnavailable(scrub(str(exc), *self.credentials.secrets())) from None
        payload = _safe_json(response)
        if response.status_code < 400 and payload is not None and not _is_error(payload):
            self.stats.writes += 1
            return payload
        raise classify(
            payload,
            status=response.status_code,
            fallback=response.text[:300],
            secrets=self.credentials.secrets(),
        )

    # -- transport --------------------------------------------------------------------------- #

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
        retryable: bool = False,
    ) -> dict[str, Any]:
        query = encode_params(params)
        body = encode_params(data) if data is not None or files else None
        creds = self.credentials
        if creds.app_secret:
            proof = appsecret_proof(creds.token, creds.app_secret)
            if body is not None:
                body["appsecret_proof"] = proof
            else:
                query["appsecret_proof"] = proof
        headers = {"Authorization": f"Bearer {creds.token}"}

        attempts = MAX_ATTEMPTS if retryable else 1
        last: MetaError | None = None
        for attempt in range(attempts):
            self.stats.requests += 1
            try:
                response = await self._http.request(
                    method,
                    url,
                    params=query or None,
                    data=body if body is not None else None,
                    files=files,
                    headers=headers,
                    timeout=_TIMEOUT,
                )
            except httpx.HTTPError as exc:
                last = MetaUnavailable(scrub(str(exc), *creds.secrets()))
            else:
                read_usage(response.headers, self.stats.usage)
                payload = _safe_json(response)
                if response.status_code < 400 and not _is_error(payload):
                    if payload is None:
                        # A 2xx that is not JSON is a proxy or a captive portal, never Meta.
                        raise MetaUnavailable("non-JSON response", status=response.status_code)
                    return payload
                last = classify(
                    payload,
                    status=response.status_code,
                    fallback=response.text[:300],
                    secrets=creds.secrets(),
                    retry_after_minutes=self.stats.usage.regain_minutes,
                )
                if not is_retryable(last):
                    raise last
            if attempt + 1 < attempts:
                await asyncio.sleep(_delay(attempt))
        assert last is not None
        logger.warning(
            "meta %s %s failed after %s attempt(s): %s", method, self._tool, attempts, last
        )
        raise last


def _is_error(payload: dict[str, Any] | None) -> bool:
    """Meta occasionally answers ``200`` with an error body (batch-era habit). Believe the body."""
    return isinstance(payload, dict) and isinstance(payload.get("error"), dict)


def _safe_json(response: httpx.Response) -> dict[str, Any] | None:
    if not response.content:
        return {}
    try:
        payload = response.json()
    except ValueError:
        return None
    if isinstance(payload, bool):
        # ``DELETE`` and a few updates answer a bare ``true``.
        return {"success": payload}
    return payload if isinstance(payload, dict) else None


def _next_page(url: str) -> tuple[str, dict[str, Any]]:
    """Meta's ``next`` URL, split into a URL we trust and the parameters it carried.

    The host is **ours**, not the one in the link: ``next`` is data from a response, and a
    response must never be able to send the token to another host. The credentials it echoes
    (``access_token``, ``appsecret_proof``) are dropped and re-added by the transport.
    """
    parsed = httpx.URL(url)
    base = httpx.URL(settings.meta_graph_url.rstrip("/"))
    path = parsed.path
    safe = str(base.copy_with(path=base.path.rstrip("/") + path))
    params = {
        key: value
        for key, value in parsed.params.multi_items()
        if key not in {"access_token", "appsecret_proof", "appsecret_time"}
    }
    return safe, params


def _delay(attempt: int) -> float:
    ceiling = min(_BACKOFF_BASE * (2**attempt), _BACKOFF_CAP)
    return random.uniform(0, ceiling)  # noqa: S311 - jitter, not cryptography


@asynccontextmanager
async def meta_client(credentials: MetaCredentials, *, tool: str = "") -> AsyncIterator[MetaClient]:
    """A :class:`MetaClient` on one token. Reads nothing from the database — everything it
    needs arrives in ``credentials`` — so it may be entered inside ``ctx.release_db()``."""
    async with httpx.AsyncClient(transport=_transport, follow_redirects=False) as http:
        yield MetaClient(http, credentials, tool=tool)


# --- the token endpoints -------------------------------------------------------------------- #
#
# These authenticate with the **app**, not with a bearer, so they do not go through
# ``MetaClient``. The app secret necessarily travels as a parameter here; it is sent in the
# POST body where Meta accepts that, and scrubbed from anything that is raised.


@dataclass(frozen=True)
class TokenInfo:
    """What ``debug_token`` says about a token. ``expires_at = None`` means it never expires."""

    is_valid: bool
    app_id: str | None
    kind: str | None
    subject_id: str | None
    scopes: tuple[str, ...]
    #: ``{scope: [target ids]}`` — which assets a scope was granted for, where Meta says.
    granular: dict[str, tuple[str, ...]]
    issued_at: int | None
    expires_at: int | None
    data_access_expires_at: int | None
    error: str | None = None


async def debug_token(app_id: str, app_secret: str, token: str) -> TokenInfo:
    """Ask Meta what a token is, as the app. One call, no bearer."""
    payload = await _app_call(
        "GET",
        "debug_token",
        {"input_token": token, "access_token": f"{app_id}|{app_secret}"},
        secrets=(token, app_secret),
    )
    data = payload.get("data")
    if not isinstance(data, dict):
        raise MetaUnavailable("debug_token answered without data")
    error = data.get("error")
    granular: dict[str, tuple[str, ...]] = {}
    for entry in data.get("granular_scopes") or ():
        if isinstance(entry, dict) and entry.get("scope"):
            granular[str(entry["scope"])] = tuple(str(t) for t in (entry.get("target_ids") or ()))
    return TokenInfo(
        is_valid=bool(data.get("is_valid")),
        app_id=str(data.get("app_id") or "") or None,
        kind=str(data.get("type") or "") or None,
        subject_id=str(data.get("user_id") or data.get("profile_id") or "") or None,
        scopes=tuple(str(s) for s in (data.get("scopes") or ())),
        granular=granular,
        issued_at=_stamp(data.get("issued_at")),
        # ``0`` is how Meta spells "never"; store the absence, not the epoch.
        expires_at=_stamp(data.get("expires_at")),
        data_access_expires_at=_stamp(data.get("data_access_expires_at")),
        error=(
            scrub(str(error.get("message") or ""), token, app_secret)[:300]
            if isinstance(error, dict)
            else None
        ),
    )


@dataclass(frozen=True)
class RefreshedToken:
    token: str
    #: Seconds until the new token expires, or ``None`` where Meta named no lifetime.
    expires_in: int | None


async def exchange_token(app_id: str, app_secret: str, token: str) -> RefreshedToken:
    """Refresh an expiring system-user token for another sixty days.

    Must happen while the token is still valid: an expired token cannot be exchanged, which is
    why the cron refreshes weeks early rather than on the day. The old token keeps working
    until its own expiry, so a refresh that is stored and then lost costs nothing.
    """
    payload = await _app_call(
        "GET",
        "oauth/access_token",
        {
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "set_token_expires_in_60_days": True,
            "fb_exchange_token": token,
        },
        secrets=(token, app_secret),
    )
    fresh = str(payload.get("access_token") or "")
    if not fresh:
        raise MetaUnavailable("the token exchange answered without a token")
    expires_in: int | None
    try:
        expires_in = int(payload["expires_in"]) if payload.get("expires_in") else None
    except (TypeError, ValueError):
        expires_in = None
    return RefreshedToken(token=fresh, expires_in=expires_in)


async def _app_call(
    method: str, path: str, params: dict[str, Any], *, secrets: tuple[str | None, ...]
) -> dict[str, Any]:
    url = graph_url(path)
    async with httpx.AsyncClient(transport=_transport, follow_redirects=False) as http:
        try:
            response = await http.request(
                method, url, params=encode_params(params), timeout=_TIMEOUT
            )
        except httpx.HTTPError as exc:
            raise MetaUnavailable(scrub(str(exc), *secrets)) from None
    payload = _safe_json(response)
    if response.status_code < 400 and payload is not None and not _is_error(payload):
        return payload
    failure = classify(
        payload, status=response.status_code, fallback=response.text[:300], secrets=secrets
    )
    error = (payload or {}).get("error") if isinstance(payload, dict) else None
    text = str(error.get("message") or "").casefold() if isinstance(error, dict) else ""
    if "client secret" in text or "application secret" in text or "app secret" in text:
        # The token endpoint answers a wrong secret with a generic OAuth code, and only the
        # sentence says whose fault it was. Reading it is the lesser evil: reporting an app
        # problem as a token problem sends somebody to regenerate the thing that was fine.
        failure = MetaAppSecretError(
            str(failure),
            status=failure.status,
            meta_code=failure.meta_code,
            subcode=failure.subcode,
            trace_id=failure.trace_id,
        )
    raise failure


def _stamp(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None
