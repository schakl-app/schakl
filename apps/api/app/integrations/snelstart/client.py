"""SnelStart B2B-API v2 client (epic #377, issue #31). Business-licensed — see LICENSE.

Written against the **live** API with a working koppelsleutel, not from memory (§11):
``docs/SNELSTART.md`` §1 records every call that was actually made and what it answered. Where
this file is defensive it is because the live API surprised us, and the comment says how.

Five properties of SnelStart's design shape everything here.

1. **Two credentials that belong to two different people.** The ``Ocp-Apim-Subscription-Key``
   is the *partner's* — one per install, ours or a self-hoster's own — and identifies the
   integration to Azure API Management. The ``clientkey`` (koppelsleutel) is the *tenant's* and
   names one SnelStart administration. Neither works without the other, and mixing them up is
   the single most likely configuration mistake, so they are separate arguments with separate
   error paths: a rejected subscription key is a *deployment* fault and a rejected koppelsleutel
   is a *tenant* fault, and telling an admin the wrong one wastes an afternoon.

2. **The bearer token is minted, not stored.** ``POST /b2b/token`` with
   ``grant_type=clientkey`` returns a token valid ``expires_in`` seconds — measured at 3599.
   It is cached in memory on the client instance, never in the database: it is derivable from a
   credential we already hold, it outlives nothing, and a token at rest is a second secret to
   rotate for no benefit.

3. **``$filter`` support is per endpoint, and a wrong answer is silent.** ``/relaties``,
   ``/grootboeken`` and ``/artikelen`` honour it and *reject* an unknown property with
   ``Could not find a property named 'Nonsense'``. ``/landen`` and ``/dagboeken`` ignore it
   entirely and answer ``200`` with the whole list — ``?$filter=Nonsense eq 'x'`` returns all
   250 countries. A client that trusts the filter and takes ``[0]`` picks Nederland for every
   country on earth. So :meth:`fetch` takes an optional ``match`` predicate and **re-applies it
   locally**; ``$filter`` is a bandwidth optimisation and never a guarantee.

4. **There is no paging metadata at all.** Max 500 rows, ``$top``/``$skip``, and no
   ``nextLink`` — SnelStart's own advice is to ask for the next page only while the current one
   came back full. :meth:`fetch_all` does exactly that and **refuses to return a prefix**: a
   page that answers short of what it was asked for ends the read, and anything else raises
   rather than quietly reporting half a ledger (the ``paginate`` rule, CLAUDE.md §10).

5. **Errors carry a structured code.** ``{resource}-{number}`` — ``BOE-0021`` is *"het
   factuurnummer bestaat al"*, which is the one error that must never be retried and must never
   surface as a failure: it means the invoice is already there. :class:`SnelstartError` keeps
   the code so a caller can branch on the fact rather than on a Dutch sentence.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

logger = logging.getLogger("schakl.snelstart")

API_BASE = "https://b2bapi.snelstart.nl/v2"
TOKEN_URL = "https://auth.snelstart.nl/b2b/token"

#: The activation link a tenant follows to hand us a koppelsleutel without generating one by
#: hand. ``{shortname}`` is issued to the partner by SnelStart in the registration mail.
ACTIVATION_URL = "https://web.snelstart.nl/couplings/activate/{shortname}"

#: SnelStart is a dependency of a *sync*, not of a page load. Reads are generous because a
#: full ``/grootboeken`` is 233 rows of a chart of accounts; a write is bounded tighter because
#: a write that has not answered is a write we must go and look for (see ``_is_replayable``).
_TIMEOUT = httpx.Timeout(connect=5.0, read=45.0, write=30.0, pool=5.0)

#: Retried, and only where a retry can help.
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

#: Refuse a response larger than this before decoding it (§17: every cap is checked *before*
#: the work it bounds). The largest legitimate page here is 500 relations with addresses.
MAX_RESPONSE_BYTES = 16 * 1024 * 1024

#: SnelStart's own hard cap on ``$top``, stated in the OData documentation and confirmed live.
PAGE_SIZE = 500

#: Guard against an unbounded loop if the server ever stops honouring ``$skip``. 100 pages is
#: 50 000 rows, far past any administration this integration is aimed at; hitting it is a bug
#: worth an exception rather than a silent stop.
MAX_PAGES = 100

#: A duplicate invoice number. Not a failure: it is how SnelStart says *"that boeking is
#: already here"*, and the correct response is to go and find it, never to try again.
CODE_DUPLICATE_INVOICE_NUMBER = "BOE-0021"

#: ``{resource}-{number}`` — three uppercase letters, four digits. Parsed out of whatever shape
#: the body happens to have, because the API answers errors in at least three of them.
_CODE_RE = re.compile(r"\b([A-Z]{3}-\d{4})\b")

#: A koppelsleutel is base64-ish and long; a subscription key is 32 hex characters. Both are
#: matched so that neither can survive into a log line or a ``last_error`` column.
_SECRET_RE = re.compile(r"(clientkey=)[^&\s]+", re.IGNORECASE)

#: Test seam — an ``httpx`` transport used instead of the network. Never set in production.
_transport: httpx.AsyncBaseTransport | None = None


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Point the client at a fake (``tests/snelstart_fake.py``). Test-only."""
    global _transport
    _transport = transport


def redact(value: str) -> str:
    """Whatever this text is, it must not carry a credential out of the process.

    The koppelsleutel travels in a POST body rather than a query string, so a URL in an
    exception does not leak it — but ``httpx`` embeds request content in some error strings and
    ``last_error`` is rendered verbatim on a settings screen, so the substitution happens
    anyway. Cheap insurance against a class of leak that is invisible in review.
    """
    return _SECRET_RE.sub(r"\1***", value)


class SnelstartError(Exception):
    """SnelStart refused, or could not be reached.

    ``code`` is SnelStart's own ``{resource}-{number}`` where it gave one — the fact a caller
    branches on. ``http_status`` is ``None`` when nothing answered at all, which is a different
    thing from a rejection and is what makes a write *unknown* rather than *failed*.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status


class SnelstartAuthError(SnelstartError):
    """The koppelsleutel was rejected — the *tenant's* credential is wrong or withdrawn."""


class SnelstartSubscriptionError(SnelstartError):
    """The subscription key was rejected — a *deployment* fault, not the tenant's.

    Kept apart from :class:`SnelstartAuthError` on purpose. Both are 401/403 and both read as
    "SnelStart said no", but only one of them is something the agency can fix: telling an admin
    to check their koppelsleutel when the install's partner key expired (they last 90 days on
    the free developer product) sends them to re-do the one thing that was already right.
    """


class SnelstartUnknownWriteError(SnelstartError):
    """A write neither succeeded nor was refused — nothing answered.

    #31's hard requirement in one exception: *treat a network timeout as unknown, not failed*.
    A caller that sees this must **look the document up** before it may retry, because the
    boeking may well be there.
    """


def _json_default(value: Any) -> Any:
    """``Decimal`` → a JSON **string**, ``date``/``datetime`` → SnelStart's own format.

    Money is a ``Decimal`` everywhere in schakl and must not become a float on the way out
    (#31). Both obvious encodings do exactly that: ``float(amount)`` openly, and
    ``json.loads(str(amount))`` silently — it parses the text back into a Python float, so
    ``Decimal("1428.00")`` leaves as ``1428.0`` and every value with awkward cents leaves as
    whatever binary floating point happened to land on.

    So the amount travels as its own decimal text. **Verified against the live API**: a
    ``verkoopboeking`` posted with ``"factuurbedrag": "121.00"`` is accepted (201) and read back
    as the number ``121.00``, because .NET parses a JSON string into a ``decimal`` exactly. That
    is the one encoding in which no float exists at any point on the wire.
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        # SnelStart's fields are wall-clock ``yyyy-MM-ddTHH:mm:ss`` with no zone. An aware
        # instant is converted to UTC first so the same moment always writes the same text.
        moment = value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value
        return moment.isoformat(timespec="seconds")
    if isinstance(value, date):
        return f"{value.isoformat()}T00:00:00"
    raise TypeError(f"cannot serialise {type(value).__name__} for SnelStart")


def parse_moment(value: Any) -> datetime | None:
    """One of SnelStart's naive timestamps → an aware UTC instant.

    Every ``date-time`` the API returns is zone-less local wall clock (``modifiedOn``,
    ``factuurDatum``, …). Reading one as naive and comparing it to ``datetime.now(UTC)``
    raises; reading it as UTC is the only interpretation that round-trips through
    :func:`_json_default`, which is what an incremental ``ModifiedOn gt …`` filter depends on.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        moment = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def parse_amount(value: Any) -> Decimal | None:
    """A SnelStart money field → ``Decimal``, via ``str`` so a float never rounds twice."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None


def odata_string(value: str) -> str:
    """A string literal for a ``$filter``. Single quotes are doubled, as OData requires.

    Not decoration: an agency called *O'Brien & Zn* is an ordinary client name, and an
    unescaped apostrophe turns the filter into a syntax error that the API reports as a 400
    naming nothing the admin typed.
    """
    return "'" + value.replace("'", "''") + "'"


def odata_datetime(moment: datetime) -> str:
    """``datetime'…'`` as OData v3 wants it — SnelStart's incremental-sync idiom."""
    naive = moment.astimezone(UTC).replace(tzinfo=None) if moment.tzinfo else moment
    return f"datetime'{naive.isoformat(timespec='seconds')}'"


class SnelstartClient:
    """One SnelStart administration, reached with one tenant's koppelsleutel.

    Constructed per credential and cheap, but **not** per request: the bearer token is cached
    on the instance, so a sync that makes forty calls mints one token rather than forty.
    """

    def __init__(
        self,
        *,
        client_key: str,
        subscription_key: str,
        base_url: str = API_BASE,
        token_url: str = TOKEN_URL,
    ) -> None:
        self._client_key = client_key.strip()
        self._subscription_key = subscription_key.strip()
        self._base_url = base_url.rstrip("/")
        self._token_url = token_url
        self._token: str | None = None
        self._token_expires: datetime | None = None
        self._token_scopes: tuple[str, ...] = ()
        self._lock = asyncio.Lock()

    # --- authentication ---------------------------------------------------------- #
    @property
    def scopes(self) -> tuple[str, ...]:
        """What the last minted token was allowed to do, as SnelStart itself declared it.

        The scopes ride inside the JWT (``relaties:read``, ``boekhouden:write``, …). They are
        an **observation**, recorded so the settings screen can say *"this key cannot write
        invoices"* before a sync fails halfway rather than after — the shape ``cloudflare``
        arrived at for token capabilities the hard way.
        """
        return self._token_scopes

    async def _bearer(self) -> str:
        """A valid token, minted at most once per hour per client instance."""
        async with self._lock:
            now = datetime.now(UTC)
            if self._token and self._token_expires and now < self._token_expires:
                return self._token
            try:
                async with httpx.AsyncClient(timeout=_TIMEOUT, transport=_transport) as http:
                    response = await http.post(
                        self._token_url,
                        data={"grant_type": "clientkey", "clientkey": self._client_key},
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                    )
            except httpx.HTTPError as exc:
                raise SnelstartError(
                    f"SnelStart's authorisation server could not be reached: "
                    f"{redact(str(exc))}"
                ) from exc

            if response.status_code >= 400:
                # The token endpoint answers ``{"error": "…"}`` in plain English. A rejected
                # koppelsleutel is the tenant's to fix, and it is the *only* thing this call
                # authenticates — the subscription key is not sent here at all.
                raise SnelstartAuthError(
                    self._token_error(response), http_status=response.status_code
                )
            try:
                payload = response.json()
            except (json.JSONDecodeError, ValueError) as exc:
                raise SnelstartError("SnelStart returned an unreadable token") from exc

            token = payload.get("access_token")
            if not isinstance(token, str) or not token:
                raise SnelstartAuthError("SnelStart returned no access token")

            # Expire a minute early. A token that dies mid-sync costs a retry we can avoid by
            # not cutting it fine; ``expires_in`` is 3599 and the margin is free.
            lifetime = payload.get("expires_in")
            seconds = int(lifetime) if isinstance(lifetime, int | float | str) else 3599
            self._token = token
            self._token_expires = datetime.now(UTC) + timedelta(seconds=max(60, seconds - 60))
            self._token_scopes = _scopes_of(token)
            return token

    @staticmethod
    def _token_error(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError):
            payload = None
        if isinstance(payload, dict):
            for key in ("error_description", "error", "Message"):
                text = payload.get(key)
                if isinstance(text, str) and text.strip():
                    return redact(text.strip())[:400]
        return f"SnelStart rejected the connection key (HTTP {response.status_code})"

    # --- transport --------------------------------------------------------------- #
    async def _send(
        self,
        method: str,
        path: str,
        *,
        params: list[tuple[str, str]] | None = None,
        body: Any = None,
        allow_404: bool = False,
    ) -> Any:
        """One request, retried only where a retry cannot double-write.

        The retry rule is an allowlist of reads rather than a status check, because SnelStart
        documents **no idempotency key**: a replayed ``POST /verkoopboekingen`` is a second
        entry in somebody's ledger. A write that gets no answer raises
        :class:`SnelstartUnknownWriteError` and the caller goes looking.
        """
        token = await self._bearer()
        headers = {
            "Authorization": f"Bearer {token}",
            "Ocp-Apim-Subscription-Key": self._subscription_key,
            "Accept": "application/json",
            "User-Agent": "schakl",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        content = (
            json.dumps(body, default=_json_default).encode() if body is not None else None
        )
        replayable = method == "GET"
        attempts = 3 if replayable else 1

        async with httpx.AsyncClient(
            base_url=self._base_url, timeout=_TIMEOUT, transport=_transport
        ) as http:
            for attempt in range(1, attempts + 1):
                try:
                    response = await http.request(
                        method, path, params=params, content=content, headers=headers
                    )
                except httpx.HTTPError as exc:
                    if attempt < attempts:
                        await asyncio.sleep(0.4 * attempt)
                        continue
                    message = f"SnelStart could not be reached: {redact(str(exc))}"
                    if replayable:
                        raise SnelstartError(message) from exc
                    raise SnelstartUnknownWriteError(message) from exc
                if response.status_code in _RETRY_STATUSES and attempt < attempts:
                    await asyncio.sleep(0.4 * attempt)
                    continue
                if response.status_code in _RETRY_STATUSES and not replayable:
                    # A 502 on a write is the same unknown as a timeout: the gateway failed on
                    # the way back, and the boeking may still have been created.
                    raise SnelstartUnknownWriteError(
                        f"SnelStart answered HTTP {response.status_code} on a write",
                        http_status=response.status_code,
                    )
                return self._unwrap(response, allow_404=allow_404)
        raise SnelstartError("SnelStart could not be reached")  # pragma: no cover

    def _unwrap(self, response: httpx.Response, *, allow_404: bool = False) -> Any:
        """A response → parsed JSON, or a typed error carrying SnelStart's own code."""
        if response.status_code == 404 and allow_404:
            return None
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise SnelstartError(
                "SnelStart returned a response too large to read",
                http_status=response.status_code,
            )
        payload: Any = None
        if response.content:
            try:
                payload = response.json()
            except (json.JSONDecodeError, ValueError):
                payload = None

        if response.status_code in (401, 403):
            # Which credential was refused decides who has to act. Azure API Management names
            # the subscription key in its own words; anything else is the koppelsleutel.
            text = self._message(payload, response, "SnelStart rejected the credential")
            if _looks_like_subscription_failure(text, payload):
                raise SnelstartSubscriptionError(text, http_status=response.status_code)
            raise SnelstartAuthError(text, http_status=response.status_code)

        if response.status_code >= 400:
            message = self._message(response=response, payload=payload, fallback="")
            raise SnelstartError(
                message or f"SnelStart answered HTTP {response.status_code}",
                code=_error_code(payload, response.text),
                http_status=response.status_code,
            )
        if response.status_code == 204 or payload is None:
            return None
        return payload

    @staticmethod
    def _message(payload: Any, response: httpx.Response, fallback: str) -> str:
        """SnelStart's own words for a failure, from whichever shape it used this time.

        Measured shapes: ``{"Message": "…"}`` from the OData layer, ``{"message": "…"}`` from
        the gateway, and a ``{"code","message"}`` pair from the business layer. Reading only
        one of them leaves an admin staring at ``HTTP 400`` with the answer in the body.
        """
        if isinstance(payload, dict):
            for key in ("message", "Message", "error_description", "error", "title"):
                text = payload.get(key)
                if isinstance(text, str) and text.strip():
                    return redact(text.strip())[:400]
            code = payload.get("code") or payload.get("Code")
            if isinstance(code, str) and code.strip():
                return redact(code.strip())[:400]
        if isinstance(payload, list) and payload:
            first = payload[0]
            if isinstance(first, dict):
                for key in ("message", "Message"):
                    text = first.get(key)
                    if isinstance(text, str) and text.strip():
                        return redact(text.strip())[:400]
        text = (response.text or "").strip()
        if text and not text.startswith("<"):
            return redact(text)[:400]
        return fallback

    # --- reads ------------------------------------------------------------------- #
    async def fetch(
        self,
        resource: str,
        *,
        filter_: str | None = None,
        top: int | None = None,
        select: str | None = None,
        match: Callable[[Mapping[str, Any]], bool] | None = None,
    ) -> list[dict[str, Any]]:
        """One page of a collection.

        ``match`` is the whole point of this signature. ``$filter`` is honoured by some
        endpoints and **silently ignored** by others (``/landen``, ``/dagboeken``), so a
        predicate that matters for correctness is stated twice: once as a filter, so the
        server does the work where it can, and once as a callable, which decides the answer.
        Passing a ``filter_`` without a ``match`` is a deliberate choice meaning *"a wrong
        answer here is merely slower, not wrong"*.
        """
        params: list[tuple[str, str]] = []
        if filter_:
            params.append(("$filter", filter_))
        if top is not None:
            params.append(("$top", str(min(top, PAGE_SIZE))))
        if select:
            params.append(("$select", select))
        payload = await self._send("GET", f"/{resource.lstrip('/')}", params=params or None)
        rows = _as_rows(payload)
        return [row for row in rows if match(row)] if match else rows

    async def fetch_all(
        self,
        resource: str,
        *,
        filter_: str | None = None,
        select: str | None = None,
        match: Callable[[Mapping[str, Any]], bool] | None = None,
    ) -> list[dict[str, Any]]:
        """Every row, paged with ``$skip``/``$top``.

        SnelStart sends no paging metadata whatsoever, so "is there more?" is answered the only
        way available: a full page means ask again, a short page means stop. That is SnelStart's
        own documented advice and it is exact — the risk it carries is a row inserted between
        two pages, not a page missed.

        **Never returns a prefix.** Running past :data:`MAX_PAGES` raises rather than returning
        what it has, because a truncated ledger that looks like a complete one is the worst
        outcome available (§17).
        """
        rows: list[dict[str, Any]] = []
        for page in range(MAX_PAGES):
            params: list[tuple[str, str]] = [
                ("$top", str(PAGE_SIZE)),
                ("$skip", str(page * PAGE_SIZE)),
            ]
            if filter_:
                params.append(("$filter", filter_))
            if select:
                params.append(("$select", select))
            payload = await self._send("GET", f"/{resource.lstrip('/')}", params=params)
            batch = _as_rows(payload)
            rows.extend(batch)
            if len(batch) < PAGE_SIZE:
                return [row for row in rows if match(row)] if match else rows
        raise SnelstartError(
            f"SnelStart kept answering full pages for /{resource} past "
            f"{MAX_PAGES * PAGE_SIZE} rows; refusing to report a partial read"
        )

    async def get(self, resource: str, entity_id: str) -> dict[str, Any] | None:
        payload = await self._send(
            "GET", f"/{resource.lstrip('/')}/{entity_id}", allow_404=True
        )
        return payload if isinstance(payload, dict) else None

    # --- writes ------------------------------------------------------------------ #
    async def post(self, resource: str, body: Any) -> Any:
        return await self._send("POST", f"/{resource.lstrip('/')}", body=body)

    async def put(self, resource: str, entity_id: str, body: Any) -> Any:
        return await self._send("PUT", f"/{resource.lstrip('/')}/{entity_id}", body=body)

    async def delete(self, resource: str, entity_id: str) -> Any:
        return await self._send("DELETE", f"/{resource.lstrip('/')}/{entity_id}")

    # --- the probes a settings screen needs -------------------------------------- #
    async def company_info(self) -> dict[str, Any]:
        """The administration this koppelsleutel opens.

        This is the verify call. ``GET /companyInfo`` is the cheapest authenticated read that
        proves *both* credentials at once and answers the question an admin actually has —
        **which administration did I just connect?** A ping that returned only "ok" would let
        somebody connect the wrong company's books and find out at the first invoice.
        """
        payload = await self._send("GET", "/companyInfo")
        if not isinstance(payload, dict):
            raise SnelstartError("SnelStart returned no administration details")
        return payload

    async def own_relation(self) -> dict[str, Any] | None:
        """The administration's own relation (``Relatiesoort`` contains ``Eigen``).

        Its address is the seller block SnelStart itself prints, which is what makes it worth
        reading: comparing it with schakl's own seller identity is how a tenant finds out the
        two disagree *before* an accountant does.
        """
        rows = await self.fetch(
            "relaties",
            filter_="Relatiesoort/any(r:r eq 'Eigen')",
            match=lambda row: "Eigen" in (row.get("relatiesoort") or []),
        )
        return rows[0] if rows else None


def _as_rows(payload: Any) -> list[dict[str, Any]]:
    """Whatever came back → a list of rows.

    v2 answers a bare JSON array for collections. The ``{"value": [...]}`` shape is accepted
    too because it is what every other OData server sends and costs one line to tolerate.
    """
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        value = payload.get("value")
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        return [payload]
    return []


def _error_code(payload: Any, text: str) -> str | None:
    """SnelStart's ``{resource}-{number}``, from the body or from the message text."""
    if isinstance(payload, dict):
        for key in ("code", "Code", "errorCode"):
            value = payload.get(key)
            if isinstance(value, str) and _CODE_RE.fullmatch(value.strip()):
                return value.strip()
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        return _error_code(payload[0], "")
    found = _CODE_RE.search(text or "")
    return found.group(1) if found else None


def _looks_like_subscription_failure(message: str, payload: Any) -> bool:
    """Did Azure API Management refuse the *partner* key rather than SnelStart the tenant's?

    Matched on the gateway's own wording (*"invalid subscription key"*, *"Access denied due
    to..."*), which is stable because it is Azure's, not SnelStart's. Getting this wrong in the
    safe direction — reporting a subscription problem as a koppelsleutel problem — is the
    default, because the tenant's credential is the one an agency can actually re-issue.
    """
    haystack = message.lower()
    if isinstance(payload, dict):
        extra = payload.get("message") or payload.get("Message")
        if isinstance(extra, str):
            haystack = f"{haystack} {extra.lower()}"
    return "subscription key" in haystack or "subscription" in haystack and "denied" in haystack


def _scopes_of(token: str) -> tuple[str, ...]:
    """The ``scopes`` claim of the bearer JWT, best-effort.

    Decoded without verifying the signature, deliberately: we are not authenticating this token
    — SnelStart is, on every call — we are reading what it says it may do so a screen can say
    so too. An unreadable token simply reports no scopes and nothing downstream breaks.
    """
    import base64

    try:
        payload = token.split(".")[1]
        padded = payload + "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded))
    except Exception:  # noqa: BLE001 — a token we cannot read is not an error, just opaque
        return ()
    raw = claims.get("scopes")
    if isinstance(raw, str):
        return tuple(sorted(part for part in raw.split() if part))
    if isinstance(raw, list):
        return tuple(sorted(str(part) for part in raw if part))
    return ()
