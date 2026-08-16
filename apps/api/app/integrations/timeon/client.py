"""Timeon API client (``https://api.timeon.nl``). Business-licensed — see LICENSE.

Written against the **live** API with a working key, not from its OpenAPI document (§11) — and
that is not a stylistic preference here. The document at ``/swagger/v1/swagger.json`` describes
**no response bodies at all**, and the single most important write endpoint, ``/api/hour/save``,
carries no request body either: it is one line of JSON declaring that it answers ``200``. Every
shape below came from a call that was actually made. ``docs/TIMEON.md`` §3 records them.

Seven behaviours shape this file, and five of them are invisible until you run it.

1. **The API key is not a bearer token.** ``POST /token?grant_type=apitoken&token=<key>`` with
   an explicit ``Content-Length: 0`` — without it the server answers **411**, not 400 — buys an
   access token valid four hours. There is no client-credentials grant and the ``refresh_token``
   the response carries is null for this grant, so a long run re-exchanges on 401 rather than
   refreshing.

2. **Cloudflare fronts the API and blocks the default Python user agent** with HTTP 403 and a
   body of ``error code: 1010``, which reads exactly like a permissions failure. ``curl`` is
   waved through, so a successful curl probe proves nothing about this client. We send a browser
   UA; :func:`_cloudflare_block` recognises the shape so the error at least says what happened.

3. **``hour/save`` is a wholesale PUT, not a patch.** Saving ``{hourID, seconds}`` and nothing
   else clears the remark, detaches the project *and* the customer, and drops ``fromSeconds``.
   Measured, on a row created for the purpose. So :meth:`save_hour` takes a whole row and the
   caller is responsible for having read one first — the same read/write parity rule the web
   app's wholesale PUT blocks already follow.

4. **An hour row has ``createdOn`` and no modified timestamp.** There is therefore *no delta
   cursor for hours*: "what changed since yesterday" is not a question this API can answer. A
   sync must re-read a window and compare against what it last observed, which is why
   :class:`app.integrations.timeon.models.TimeonLink` stores a remote fingerprint at all. The
   one field that looks like an exception is not one — ``billableModified`` is a **boolean**
   meaning "somebody set this flag by hand", measured on the live corpus, and a client that read
   it as a timestamp would have a delta cursor that is always false. (``organisation`` and each
   ``user`` *do* carry a real ``modifiedOn``. Hours, the only rows that matter here, do not.)

5. **``filter.deleted`` is accepted and ignored.** Asking for deleted rows answers the *live*
   ones — the whole corpus, every row with ``deleted: false``. So a deletion in Timeon is
   observable **only as absence from a window we know we read completely**, which is what makes
   :meth:`hours` assert each window's row count against the server's own ``totalItems`` rather
   than trusting the array it was handed.

6. **``hour/list`` is grouped by day** (``resultObject.groups[].hourList[]``) and its
   ``filter.paged`` answers *"not implemented"*. Windows are pulled by date range, never paged.

7. **The envelope is always 200.** A refusal arrives as ``{"success": false, "message": …}`` with
   an HTTP 200, so a client that only checks the status code reports every failure as a write
   that worked.
"""

from __future__ import annotations

import asyncio
import calendar
import json
import logging
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

logger = logging.getLogger("schakl.timeon")

API_BASE = "https://api.timeon.nl"

#: Cloudflare rejects ``python-httpx``/``Python-urllib`` outright (rule 2). This is not
#: impersonation for its own sake — it is the only user agent the edge in front of this API
#: will pass, and the alternative is a 403 that reads as an authorisation failure.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)

#: An access token lives four hours; we re-exchange a minute early rather than on the 401, and
#: keep the 401 path as the backstop for a server that expires it sooner.
TOKEN_SKEW_SECONDS = 60

#: A month of hours is a few hundred rows and the server is not fast. Generous on read, tighter
#: on write for the reason ``snelstart``'s client states: a write that has not answered is a
#: write somebody has to go and look for.
_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=5.0)

_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

#: Refuse a response before decoding it (§17: a cap is checked before the work it bounds). The
#: largest legitimate answer here is a full year of hour rows with their string renderings.
MAX_RESPONSE_BYTES = 32 * 1024 * 1024

#: Timeon holds nothing before this for any tenant we have met; a full read starts here rather
#: than walking back to 1970 one fruitless month at a time. Overridable per call.
FIRST_YEAR = 2024

#: Test seam — an ``httpx`` transport used instead of the network (``tests/timeon_fake.py``).
_transport: httpx.AsyncBaseTransport | None = None


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Point the client at a fake. Test-only; never set in production."""
    global _transport
    _transport = transport


class TimeonError(Exception):
    """Timeon refused, or could not be reached.

    ``http_status`` is ``None`` when nothing answered at all — a different fact from a rejection,
    and the one that makes a write *unknown* rather than *failed*.
    """

    def __init__(
        self, message: str, *, http_status: int | None = None, path: str | None = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.http_status = http_status
        self.path = path


class TimeonAuthError(TimeonError):
    """The API key was refused. A tenant fault, and the only one an agency can fix itself."""


class TimeonBlockedError(TimeonError):
    """The edge refused the request before Timeon saw it (rule 2).

    Its own class because the remedy is nothing like an auth failure's: the credential is fine
    and the *deployment* is being filtered, so telling an admin to re-issue their key would send
    them to fix the one thing that is already right.
    """


def _cloudflare_block(status: int, body: str) -> bool:
    """Is this the edge talking, rather than Timeon?"""
    return status in (403, 1020) and "error code: 10" in body


def month_windows(start: date, end: date) -> Iterator[tuple[date, date]]:
    """The whole-month windows covering ``start..end`` inclusive.

    Month at a time because that is the granularity at which ``hour/list``'s own
    ``summary.totalItems`` can be checked against the rows it returned (rule 5). A single
    six-month request would answer one total for a read whose completeness we could then only
    take on trust.
    """
    if end < start:
        return
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        last = calendar.monthrange(year, month)[1]
        yield (
            max(start, date(year, month, 1)),
            min(end, date(year, month, last)),
        )
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


class TimeonClient:
    """One tenant's Timeon connection, for the length of one sync.

    The access token is cached on the instance and never in the database: it is derivable from a
    credential we already hold, it expires in four hours, and a token at rest is a second secret
    to rotate for no benefit (``snelstart``'s rule).
    """

    def __init__(self, api_key: str, *, base_url: str = API_BASE) -> None:
        self._key = api_key
        self._base = base_url.rstrip("/")
        self._token: str | None = None
        self._token_expires: datetime | None = None
        #: Every path this instance called, for the run report. A sync that reports "Timeon was
        #: unreachable" without saying which question failed is #381's lesson unlearned.
        self.calls: list[str] = []

    # --- transport ----------------------------------------------------------- #
    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base,
            timeout=_TIMEOUT,
            transport=_transport,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=False,
        )

    async def _exchange(self) -> None:
        """Buy a four-hour access token with the API key.

        The explicit ``Content-Length: 0`` is load-bearing: this is a POST with no body, and
        without the header the server answers **411 Length Required**, which surfaces as a
        transport error rather than as anything about the credential.
        """
        async with self._client() as http:
            try:
                res = await http.post(
                    "/token",
                    params={"grant_type": "apitoken", "token": self._key},
                    content=b"",
                    headers={"Content-Length": "0"},
                )
            except httpx.HTTPError as exc:  # pragma: no cover - network shape
                raise TimeonError(f"Timeon unreachable: {exc}", path="/token") from exc
            body = res.text[:2000]
            if _cloudflare_block(res.status_code, body):
                raise TimeonBlockedError(
                    "Timeon's edge refused the request (Cloudflare 1010)",
                    http_status=res.status_code,
                    path="/token",
                )
            try:
                payload = res.json()
            except ValueError:
                raise TimeonError(
                    f"Timeon returned a non-JSON token response ({res.status_code})",
                    http_status=res.status_code,
                    path="/token",
                ) from None
        token = payload.get("access_token")
        if not token:
            raise TimeonAuthError(
                payload.get("errorMessage") or "Timeon refused the API key",
                http_status=res.status_code,
                path="/token",
            )
        self._token = token
        lifetime = int(payload.get("expires_in") or 4 * 3600)
        self._token_expires = datetime.now(UTC) + timedelta(
            seconds=max(60, lifetime - TOKEN_SKEW_SECONDS)
        )

    async def _ensure_token(self) -> str:
        if self._token is None or (
            self._token_expires is not None and datetime.now(UTC) >= self._token_expires
        ):
            await self._exchange()
        assert self._token is not None
        return self._token

    async def call(
        self, path: str, payload: dict[str, Any] | None = None, *, method: str | None = None
    ) -> Any:
        """One API call, returning ``resultObject``.

        The envelope is always HTTP 200 (rule 7), so a ``success: false`` body is raised here
        rather than left for each caller to remember. A 401 mid-run re-exchanges once — the
        token lasts four hours and a full-history read can outlive it.
        """
        verb = method or ("POST" if payload is not None else "GET")
        self.calls.append(f"{verb} {path}")
        for attempt in (1, 2, 3):
            token = await self._ensure_token()
            async with self._client() as http:
                headers = {"Authorization": f"Bearer {token}"}
                if payload is None:
                    # The same 411 that bites the token exchange bites any bodyless POST.
                    headers["Content-Length"] = "0"
                try:
                    res = await http.request(
                        verb,
                        path,
                        json=payload if payload is not None else None,
                        headers=headers,
                    )
                except httpx.HTTPError as exc:  # pragma: no cover - network shape
                    if attempt < 3:
                        await asyncio.sleep(attempt)
                        continue
                    raise TimeonError(f"Timeon unreachable: {exc}", path=path) from exc
                raw = res.content[: MAX_RESPONSE_BYTES + 1]
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise TimeonError(
                        f"Timeon answered more than {MAX_RESPONSE_BYTES} bytes for {path}",
                        http_status=res.status_code,
                        path=path,
                    )
            text = raw.decode(errors="replace")
            if _cloudflare_block(res.status_code, text[:500]):
                raise TimeonBlockedError(
                    "Timeon's edge refused the request (Cloudflare 1010)",
                    http_status=res.status_code,
                    path=path,
                )
            if res.status_code == 401 and attempt < 3:
                self._token = None  # the four-hour token lapsed mid-run
                continue
            if res.status_code in _RETRY_STATUSES and attempt < 3:
                await asyncio.sleep(attempt)
                continue
            if res.status_code >= 400:
                raise TimeonError(
                    f"Timeon answered {res.status_code} for {path}",
                    http_status=res.status_code,
                    path=path,
                )
            try:
                body = json.loads(text) if text else {}
            except ValueError:
                raise TimeonError(
                    f"Timeon returned a non-JSON body for {path}",
                    http_status=res.status_code,
                    path=path,
                ) from None
            if body.get("success") is False:
                raise TimeonError(
                    body.get("message") or body.get("exception") or f"Timeon refused {path}",
                    http_status=res.status_code,
                    path=path,
                )
            return body.get("resultObject")
        raise TimeonError(f"Timeon did not answer {path}", path=path)  # pragma: no cover

    # --- reads --------------------------------------------------------------- #
    async def organisation(self) -> dict[str, Any]:
        """The connected organisation. What ``verify`` reads, for ``snelstart``'s reason: a key
        that merely works still tells an admin nothing about *which* books it just opened."""
        return await self.call("/api/organisation") or {}

    async def users(self) -> list[dict[str, Any]]:
        return list(await self.call("/api/user/search", {}) or [])

    async def customers(self) -> list[dict[str, Any]]:
        return await self._paged("/api/customer/list", {})

    async def projects(self, *, with_budget: bool = True) -> list[dict[str, Any]]:
        """Every project, open and closed.

        ``showHidden`` is deliberately **not** sent: it is a filter, not a widener — ``true``
        answers only hidden rows and ``false`` only visible ones, so omitting it is the only way
        to get both (159 = 67 + 92 on the corpus this was measured against).
        """
        return await self._paged("/api/project/list", {"calculateBudget": with_budget})

    async def _paged(self, path: str, extra: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        page = 1
        while True:
            res = await self.call(path, {"page": page, "pageSize": 100, **extra}) or {}
            items = res.get("items") or []
            out.extend(items)
            pages = int(res.get("nrPages") or 1)
            if page >= pages or not items:
                return out
            page += 1
            if page > 200:  # pragma: no cover - runaway guard
                raise TimeonError(f"{path} did not stop paging", path=path)

    async def hours(self, start: date, end: date) -> list[dict[str, Any]]:
        """Every hour row in ``start..end``, month by month, refusing a short answer.

        The assertion is the whole point. Absence from this list is the *only* signal a Timeon
        deletion gives (rule 5), so a window that quietly came back short would be read as a
        batch of deletions and would delete live work in schakl. A window whose returned rows
        disagree with the server's own ``summary.totalItems`` therefore raises rather than
        returning a prefix (§17).
        """
        out: list[dict[str, Any]] = []
        for win_start, win_end in month_windows(start, end):
            res = (
                await self.call(
                    "/api/hour/list",
                    {"filter": {"from": win_start.isoformat(), "to": win_end.isoformat()}},
                )
                or {}
            )
            rows = [h for g in (res.get("groups") or []) for h in (g.get("hourList") or [])]
            expected = int((res.get("summary") or {}).get("totalItems") or 0)
            if len(rows) != expected:
                raise TimeonError(
                    f"Timeon returned {len(rows)} rows for {win_start}..{win_end} but reports "
                    f"{expected} — refusing a partial window",
                    path="/api/hour/list",
                )
            out.extend(rows)
        return out

    async def hours_by_id(self, hour_ids: list[int]) -> list[dict[str, Any]]:
        """Specific rows, for a targeted re-read. No completeness assertion is possible here —
        a missing id means the row is gone, which is exactly what the caller asked."""
        if not hour_ids:
            return []
        res = await self.call("/api/hour/list", {"filter": {"hourIDs": hour_ids}}) or {}
        return [h for g in (res.get("groups") or []) for h in (g.get("hourList") or [])]

    # --- writes -------------------------------------------------------------- #
    async def save_hour(self, row: dict[str, Any]) -> dict[str, Any]:
        """Create (no ``hourID``) or replace (with one) a single hour row.

        **The whole row, every time.** This endpoint replaces rather than patches: a save
        carrying ``{hourID, seconds}`` was measured to blank the remark and null out both
        ``projectID`` and ``customerID``. Callers build the body from what they last read.
        """
        return await self.call("/api/hour/save", row) or {}

    async def delete_hour(self, hour_id: int) -> None:
        """Soft-delete. Reversible through ``/restore`` inside Timeon's own bin, which is why
        the sync never uses the ``/definitive`` variant: an integration should not be able to
        destroy a client's record beyond what their own UI can undo."""
        await self.call("/api/hour/delete", {"hourID": hour_id})

    async def approve_hours(self, hour_ids: list[int], *, approved: bool) -> None:
        """Sign hours off, or take the signature back.

        ``hourIDs`` is a **comma-separated string**, not an array — the one place in this API
        where a list is spelled that way.
        """
        if not hour_ids:
            return
        path = "/api/hour/approve" if approved else "/api/hour/disapprove"
        await self.call(path, {"hourIDs": ",".join(str(i) for i in hour_ids)})

    async def create_project(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.call("/api/project/create", payload) or {}

    async def save_project(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Replace a project. ``ProjectUpdate`` in Timeon's own schema, and wholesale like the
        hour save — the caller sends everything it wants kept."""
        return await self.call("/api/project/save", payload) or {}
