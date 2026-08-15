"""Mollie API client, per **tenant** credential (issue #267). Business-licensed — see LICENSE.

Implements :class:`app.core.payments.PaymentProvider` against Mollie's documented v2 REST API
(``https://api.mollie.com/v2``). Everything here is written from Mollie's official reference
(``docs.mollie.com``, whose pages are also served as markdown and whose endpoint pages embed
the OpenAPI definition — see ``docs/MOLLIE.md`` §1 for exactly which pages, and what to run the
day a live credential arrives). CLAUDE.md §11 bans writing an integration *from memory*; this
one has the document, and says plainly where the document is the only evidence.

Five properties of Mollie's design shape this file:

1. **The key declares its own world.** ``test_…`` and ``live_…`` are separate, fully isolated
   datasets, and an API key belongs to exactly one of them. So mode is *derived*
   (:func:`mode_of`) and the ``testmode`` request parameter — which exists only for
   organization-level and OAuth tokens — is never sent.
2. **Money is a decimal string, in an object.** ``{"currency": "EUR", "value": "10.00"}``.
   Formatted from a ``Decimal`` with :func:`_amount`; a float never touches this boundary in
   either direction.
3. **The webhook carries one field, ``id``, form-encoded, and no signature at all.** Not a
   status, not an amount. Mollie says so in as many words — *"fake calls to your webhook will
   never result in orders being processed without being actually paid"* — and the
   authenticated re-fetch in :meth:`MolliePaymentProvider.fetch_payment` is what makes that
   true. (Mollie's newer dashboard-configured "next-gen" webhooks do sign, with
   ``X-Mollie-Signature``; they are a different system, we do not subscribe to it, and
   :meth:`verify_webhook` is where it would be honoured.)
4. **``POST /payments`` is not idempotent, so it is never retried blind.** Mollie supports the
   ``Idempotency-Key`` header with a one-hour cache; we send one keyed on our own intent id so
   a transport retry cannot open two checkouts for one invoice. Reads retry, writes do not.
5. **The checkout URL is a HAL link, and it disappears.** ``_links.checkout.href`` is present
   while the payment is payable and absent once it is not — which is a fact worth storing
   rather than a field worth defaulting.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar
from urllib.parse import parse_qs

import httpx

from app.core.payments import (
    PaymentProviderAuthError,
    PaymentProviderError,
    PaymentRequest,
    PaymentSnapshot,
    PaymentStatus,
)

logger = logging.getLogger("schakl.mollie")

API_BASE = "https://api.mollie.com/v2"

#: Mollie is a dependency of a *button*, not of a page load, and its own webhook budget is 15
#: seconds. A read that has not answered in 20 is not going to save the request it is holding.
_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=20.0, pool=5.0)

#: Retried once, and only where a retry can help. 429 is included on Mollie's own advice that
#: a rate limit clears after a short period; 4xx other than that never is.
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

#: Refuse a response larger than this before decoding it (§17: every cap is checked *before*
#: the work it bounds). A payment object is a few kilobytes; a megabyte is a provider fault.
MAX_RESPONSE_BYTES = 4 * 1024 * 1024

#: Mollie's payment ids. Used to sanity-check a webhook body before it becomes a lookup key —
#: not as security (the re-fetch is that), but so a garbage POST costs no query at all.
_PAYMENT_ID_RE = re.compile(r"^tr_[A-Za-z0-9]+$")

#: Mollie's hosted-checkout languages (``xx_XX``), from the Create payment reference. A locale
#: we cannot map is simply omitted, and Mollie falls back to the browser's — which is a better
#: outcome than guessing ``en_US`` at a Dutch payer.
_LOCALES = {
    "nl": "nl_NL",
    "nl-be": "nl_BE",
    "en": "en_GB",
    "en-gb": "en_GB",
    "en-us": "en_US",
    "de": "de_DE",
    "de-at": "de_AT",
    "de-ch": "de_CH",
    "fr": "fr_FR",
    "fr-be": "fr_BE",
    "es": "es_ES",
    "it": "it_IT",
    "pt": "pt_PT",
    "pl": "pl_PL",
    "da": "da_DK",
    "sv": "sv_SE",
    "fi": "fi_FI",
    "cs": "cs_CZ",
    "hu": "hu_HU",
    "lt": "lt_LT",
    "lv": "lv_LV",
    "is": "is_IS",
    "ca": "ca_ES",
    "sk": "sk_SK",
    "nb": "nb_NO",
}

#: Mollie's payment statuses → the seam's. Exhaustive as documented; anything Mollie adds later
#: is treated as still-in-flight rather than guessed into a final state, because the only
#: dangerous guess is one that settles an invoice.
_STATUSES = {
    "open": PaymentStatus.OPEN,
    "pending": PaymentStatus.PENDING,
    "authorized": PaymentStatus.AUTHORIZED,
    "paid": PaymentStatus.PAID,
    "failed": PaymentStatus.FAILED,
    "expired": PaymentStatus.EXPIRED,
    "canceled": PaymentStatus.CANCELED,
    # Mollie spells it with one L; accept the other spelling defensively rather than let a
    # cancelled payment read as "still open" forever.
    "cancelled": PaymentStatus.CANCELED,
}

#: Test seam — an ``httpx`` transport used instead of the network. Never set in production;
#: unset, a test that forgot to stub fails loudly on connect instead of reaching Mollie.
_transport: httpx.AsyncBaseTransport | None = None


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Install (or clear) the transport every client uses. Tests only."""
    global _transport
    _transport = transport


def mode_of(api_key: str) -> str:
    """``test`` or ``live``, from the key's own prefix.

    Mollie keys are self-typed, so this is the credential telling us which world it acts in.
    Anything unrecognised is treated as ``live``: erring towards "this is real money" makes a
    misread key refuse to settle nothing, while erring the other way would book a real payment
    as a test and silently lose it.
    """
    return "test" if api_key.strip().startswith("test_") else "live"


def redact(value: str) -> str:
    """Blank out anything that looks like a Mollie API key in a message.

    Mollie authenticates with a header rather than a query parameter, so this is belt to the
    header's braces: an error body, a misconfigured proxy or a future endpoint could still put
    one in text that ends up on a row's ``last_error`` or in a log line.
    """
    return re.sub(r"\b(test|live)_[A-Za-z0-9]{8,}", r"\1_***", value)


def _amount(value: Decimal) -> str:
    """A ``Decimal`` as Mollie's exact monetary string. Two decimals, never a float."""
    return f"{value:.2f}"


def _parse_amount(node: Any) -> tuple[Decimal | None, str | None]:
    if not isinstance(node, dict):
        return None, None
    raw = node.get("value")
    currency = node.get("currency")
    try:
        amount = Decimal(str(raw)) if raw is not None else None
    except (ArithmeticError, ValueError):
        amount = None
    return amount, currency if isinstance(currency, str) else None


def _parse_instant(raw: Any) -> datetime | None:
    """Mollie's ISO-8601 timestamps (``2024-03-20T09:15:02+00:00``). ``None`` on anything
    unparseable — a missing ``paidAt`` is normal, and a malformed one must not raise inside a
    settle path."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


class MollieError(PaymentProviderError):
    """A Mollie call failed. ``message`` is Mollie's own ``detail``/``title`` — never a key."""


class MollieAuthError(PaymentProviderAuthError, MollieError):
    """The credential was rejected (Mollie answers 401, or 403 for a key without access)."""


class MolliePaymentProvider:
    """One Mollie API key. Constructed per credential, cheap, holds no connection."""

    key: ClassVar[str] = "mollie"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key.strip()

    # --- transport --------------------------------------------------------------- #
    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=API_BASE,
            timeout=_TIMEOUT,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "schakl",
            },
            transport=_transport,
        )

    async def _send(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        allow_404: bool = False,
    ) -> dict[str, Any] | None:
        """One request, with a single retry for reads only.

        ``POST /payments`` opens a payment and is **not** replayable: a blind retry is a second
        checkout link for one invoice. Mollie's ``Idempotency-Key`` would make it safe, and we
        do send one — but its cache is an hour and keyed to the credential, so the retry
        decision stays an allowlist rather than a trust in a header.
        """
        attempts = 2 if method == "GET" else 1
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        async with self._http() as http:
            for attempt in range(1, attempts + 1):
                try:
                    response = await http.request(
                        method, path, json=json_body, params=params, headers=headers
                    )
                except httpx.HTTPError as exc:
                    # str(exc) is a transport message ("connect timeout"), never the headers —
                    # redacted anyway, because the cost of being wrong is a key in a log.
                    if attempt < attempts:
                        continue
                    raise MollieError(f"Mollie unreachable: {redact(str(exc))}") from exc
                if response.status_code in _RETRY_STATUSES and attempt < attempts:
                    continue
                return self._unwrap(response, allow_404=allow_404)
        raise MollieError("Mollie unreachable")  # pragma: no cover — the loop returns

    def _unwrap(
        self, response: httpx.Response, *, allow_404: bool = False
    ) -> dict[str, Any] | None:
        """Mollie's response → a dict, or a typed error.

        Read defensively at every step: an edge error page is HTML, a gateway timeout is not
        JSON at all, and a 402/422 body is JSON whose useful part is ``detail`` plus an
        optional ``field``.
        """
        if response.status_code == 404 and allow_404:
            return None
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise MollieError("Mollie response too large", http_status=response.status_code)
        payload: Any = None
        if response.content:
            try:
                payload = response.json()
            except (json.JSONDecodeError, ValueError):
                payload = None
        if response.status_code in (401, 403):
            raise MollieAuthError(
                self._message(payload, "credential rejected"),
                http_status=response.status_code,
            )
        if response.status_code >= 400:
            raise MollieError(
                self._message(payload, f"HTTP {response.status_code}"),
                code=str(payload.get("field")) if isinstance(payload, dict) else None,
                http_status=response.status_code,
            )
        if response.status_code == 204 or payload is None:
            return {}
        if not isinstance(payload, dict):
            raise MollieError("Mollie returned an unexpected payload")
        return payload

    @staticmethod
    def _message(payload: Any, fallback: str) -> str:
        if isinstance(payload, dict):
            detail = payload.get("detail") or payload.get("title")
            if isinstance(detail, str) and detail.strip():
                return redact(detail.strip())[:400]
        return fallback

    # --- the seam ---------------------------------------------------------------- #
    async def verify(self) -> dict[str, Any]:
        """Prove the key works, and learn what it can do.

        ``GET /methods`` rather than a dedicated ping, for three reasons: it is the cheapest
        authenticated read Mollie offers; its answer is the one an admin actually wants on the
        settings screen (*which* methods this key can take); and it is reachable **with an API
        key**, which the obvious alternative is not — the Profiles API needs an advanced access
        token or OAuth, so "which profile is this?" is a question a plain key cannot ask.

        In test mode Mollie returns pending methods as well as enabled ones, which is correct:
        they are usable there. That difference is itself worth showing.
        """
        payload = await self._send("GET", "/methods") or {}
        embedded = payload.get("_embedded")
        rows = embedded.get("methods") if isinstance(embedded, dict) else None
        methods = [
            m["id"]
            for m in (rows if isinstance(rows, list) else [])
            if isinstance(m, dict) and isinstance(m.get("id"), str)
        ]
        return {"mode": mode_of(self._api_key), "methods": methods}

    async def create_payment(self, request: PaymentRequest) -> PaymentSnapshot:
        """``POST /payments``. Returns the payment including ``_links.checkout``.

        ``method`` is deliberately not sent, so Mollie's hosted checkout shows its own picker
        (#267's open question, answered the way it leaned): the shopper chooses, the agency
        configures which methods exist in Mollie's own dashboard, and schakl grows no second
        place to get that wrong. Pinning a method would also *remove* Mollie's fallback — a
        failed pinned payment cannot be retried with another method.
        """
        body: dict[str, Any] = {
            "amount": {"currency": request.currency.upper(), "value": _amount(request.amount)},
            "description": request.description[:255],
            "redirectUrl": request.return_url,
            "webhookUrl": request.webhook_url,
            "metadata": request.metadata or {},
        }
        if request.cancel_url:
            body["cancelUrl"] = request.cancel_url
        locale = _LOCALES.get((request.locale or "").lower())
        if locale:
            body["locale"] = locale
        payload = await self._send(
            "POST", "/payments", json_body=body, idempotency_key=request.reference
        )
        if not payload or not isinstance(payload.get("id"), str):
            raise MollieError("Mollie did not return a payment id")
        return self._snapshot(payload)

    async def fetch_payment(self, reference: str) -> PaymentSnapshot | None:
        """``GET /payments/{id}`` — **the authority**, and the only thing a webhook triggers.

        ``None`` for an id this credential does not know: a callback naming somebody else's
        payment, or a forged one, are the same answer and neither is an error.
        """
        if not _PAYMENT_ID_RE.match(reference or ""):
            return None
        payload = await self._send("GET", f"/payments/{reference}", allow_404=True)
        if not payload:
            return None
        return self._snapshot(payload)

    @classmethod
    def references_in_webhook(cls, body: bytes, headers: Mapping[str, str]) -> list[str]:
        """The payment id Mollie posted, and nothing else it says.

        The documented contract is ``application/x-www-form-urlencoded`` with a single ``id``
        field. JSON is accepted too — defensively, because Mollie's next-gen webhooks post the
        whole entity as JSON and an operator who points one here should get the id read out of
        it rather than silence. Either way **only the id is taken**; the status in a JSON body
        is ignored on purpose.
        """
        if not body:
            return []
        try:
            text = body.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001 — a public endpoint parses hostile input
            return []
        found: list[str] = []
        content_type = (headers.get("content-type") or "").lower()
        if "json" in content_type or text.lstrip().startswith("{"):
            try:
                parsed = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                for candidate in (
                    parsed.get("id"),
                    (parsed.get("data") or {}).get("id")
                    if isinstance(parsed.get("data"), dict)
                    else None,
                ):
                    if isinstance(candidate, str) and _PAYMENT_ID_RE.match(candidate):
                        found.append(candidate)
        else:
            for candidate in parse_qs(text).get("id", []):
                if _PAYMENT_ID_RE.match(candidate):
                    found.append(candidate)
        # Dedup, order-preserving: a body naming the same payment twice is one reconcile.
        return list(dict.fromkeys(found))

    def verify_webhook(self, body: bytes, headers: Mapping[str, str]) -> bool:
        """Accept. Mollie's per-payment ``webhookUrl`` contract has no signature, by design —
        the payload carries no status to forge, and the re-fetch is the authentication.

        This is where ``X-Mollie-Signature`` would be checked if we ever subscribed to Mollie's
        next-gen webhooks; that needs a shared secret from their dashboard, which is a
        different credential and therefore a different issue.
        """
        return True

    # --- parsing ----------------------------------------------------------------- #
    def _snapshot(self, payload: dict[str, Any]) -> PaymentSnapshot:
        amount, currency = _parse_amount(payload.get("amount"))
        links = payload.get("_links") if isinstance(payload.get("_links"), dict) else {}
        checkout = links.get("checkout") if isinstance(links, dict) else None
        checkout_url = (
            checkout.get("href")
            if isinstance(checkout, dict) and isinstance(checkout.get("href"), str)
            else None
        )
        raw_status = payload.get("status")
        status = _STATUSES.get(
            raw_status if isinstance(raw_status, str) else "", PaymentStatus.PENDING
        )
        if not isinstance(raw_status, str) or raw_status not in _STATUSES:
            # Loud, because it means Mollie grew a state and this map needs a line. Treating it
            # as pending is the safe default: nothing settles, and the cron keeps asking.
            logger.warning("mollie: unknown payment status %r", raw_status)
        method = payload.get("method")
        mode = payload.get("mode")
        return PaymentSnapshot(
            reference=str(payload.get("id") or ""),
            status=status,
            amount=amount,
            currency=currency,
            method=method if isinstance(method, str) else None,
            mode=mode if mode in ("live", "test") else None,
            paid_at=_parse_instant(payload.get("paidAt")),
            checkout_url=checkout_url,
            detail=self._failure_detail(payload),
            raw=self._safe_payload(payload),
        )

    @staticmethod
    def _failure_detail(payload: dict[str, Any]) -> str | None:
        """Why a card was refused, where Mollie said. Untranslatable provider text, so it goes
        on the intent's ``last_error`` and never into an error envelope (§9)."""
        details = payload.get("details")
        if isinstance(details, dict):
            for key in ("failureMessage", "failureReason", "bankReason"):
                value = details.get(key)
                if isinstance(value, str) and value.strip():
                    return redact(value.strip())
        reason = payload.get("statusReason")
        if isinstance(reason, dict) and isinstance(reason.get("message"), str):
            return redact(reason["message"].strip()) or None
        return None

    @staticmethod
    def _safe_payload(payload: dict[str, Any]) -> dict[str, Any]:
        """The subset of Mollie's answer worth keeping on the intent's trail.

        An allowlist rather than the whole object: ``details`` can carry a payer's IBAN and
        name, and a JSONB column nobody prunes is the wrong place for a third party's personal
        data (and would put it in every activity export). Everything kept here is about the
        payment, not the payer.
        """
        return {
            key: payload[key]
            for key in (
                "id",
                "mode",
                "status",
                "method",
                "amount",
                "createdAt",
                "paidAt",
                "expiresAt",
                "expiredAt",
                "failedAt",
                "canceledAt",
                "profileId",
            )
            if key in payload
        }

