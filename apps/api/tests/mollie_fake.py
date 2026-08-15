"""A scriptable stand-in for the Mollie v2 API (epic #269, issue #267).

Modelled on :mod:`tests.cloudflare_fake` and :mod:`tests.oxxa_fake` — state in plain dicts, a
recorded call log, and a way to make one call fail — because what matters here is not a single
request but a *conversation*: open a payment, hand back a checkout link, and answer a later
re-fetch differently. Four things about Mollie make it its own animal, and each of them is a
hazard this fake exists to keep honest.

* **The webhook carries an id, form-encoded, and no signature at all.** So a test can never
  settle an invoice by posting a body: it settles one by moving *this fake's* state and then
  letting the callback trigger the authenticated re-fetch. That is Mollie's own security model
  (``client.py``, rule 3) and the reason :meth:`FakeMollie.pay` exists while there is no way at
  all to hand a status to the callback. A fake that read a status out of the request body would
  have quietly stopped testing the one property the whole design rests on.
* **The credential travels in an ``Authorization`` header**, so this fake records **no headers**
  — only the method, the path and the JSON body. ``test_the_fake_never_records_the_api_key``
  asserts it. OXXA's fake has the same rule for the opposite reason (its credential is in the
  query string); the lesson generalises: a harness that logs the whole request puts the tenant's
  secret in every pytest failure output, which is the leak ``redact`` exists to prevent,
  reintroduced one layer down.
* **The key declares its own world.** Mollie keys are self-typed (``test_…`` / ``live_…``) and
  the two datasets are fully isolated, so a payment created here derives its ``mode`` from the
  Bearer prefix exactly as :func:`app.integrations.mollie.client.mode_of` derives it there. That is
  not decoration: ``mode`` is what decides whether a settled payment writes a ledger row at all
  (``invoicing/payments.py``), so a fake that always said ``live`` would make the test-mode
  dead end untestable and a fake that always said ``test`` would make every settle test pass
  for the wrong reason.
* **There is no refund, deliberately.** ``app.core.payments`` ships no ``refund`` because moving
  money back is not reversible and nothing should inherit that power by accident. Every refund
  path is therefore in :data:`FORBIDDEN_FRAGMENTS` and raises ``AssertionError`` rather than
  answering — checked **before** ``require_key`` and before any scripted failure, so no test
  setup can turn "we tried to refund somebody" into a tidy reportable error.

Money is a decimal **string in an object** (``{"currency": "EUR", "value": "10.00"}``) on the
way in and out, because that is what Mollie sends and the whole point of the boundary is that a
float never crosses it.
"""

from __future__ import annotations

import itertools
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

#: Mollie's versioned base path. ``client.API_BASE`` ends in it, so every request arrives here
#: with it on the front.
PREFIX = "/v2"

#: Path fragments this fake refuses to answer at all, loudly.
#:
#: A refund and a chargeback both move money *out* of the agency's account, and neither is
#: undoable. The seam has no method for either (``app/core/payments/backend.py``: *"There is
#: deliberately no ``refund``"*), so any request whose path names one means a regression has
#: grown the capability by accident. Answering it with an ordinary 404 would let that regression
#: report a tidy failure and stay green-ish; an ``AssertionError`` escapes the app and fails the
#: test that caused it, by name.
FORBIDDEN_FRAGMENTS = frozenset({"refund", "chargeback"})

#: What a fresh fake credential reports from ``GET /methods``. Three, and deliberately the three
#: an NL agency actually sees: the list is an *observation* the settings screen renders, so a
#: single-element stub would not exercise "which of my two keys is which".
DEFAULT_METHODS = ("ideal", "creditcard", "bancontact")

_METHOD_LABELS = {
    "ideal": "iDEAL",
    "creditcard": "Card",
    "bancontact": "Bancontact",
    "paypal": "PayPal",
    "banktransfer": "Bank transfer",
}

#: Where Mollie's hosted checkout lives. The payer never reaches it in a test; what matters is
#: that the link is present while a payment is payable and **gone** once it is not.
CHECKOUT_BASE = "https://www.mollie.com/checkout/select-method"


def _now() -> str:
    """Mollie's ISO-8601 instant (``2026-08-05T21:15:02+00:00``)."""
    return datetime.now(UTC).isoformat()


def _error(status: int, title: str, detail: str, *, field: str | None = None) -> httpx.Response:
    """Mollie's error envelope. ``detail`` is the sentence a human reads; ``field`` names the
    offending input where Mollie knows it (a 422 on ``amount.value``, say)."""
    body: dict[str, Any] = {"status": status, "title": title, "detail": detail}
    if field is not None:
        body["field"] = field
    return httpx.Response(status, json=body)


class FakeMollie:
    """One Mollie account, holding payments, answering ``api.mollie.com/v2``.

    A test sets up "this payment has been paid" by calling :meth:`pay` and then delivering the
    callback, which is exactly the order the real thing happens in — the money moves at Mollie,
    and we find out afterwards.
    """

    def __init__(self) -> None:
        #: The methods ``GET /methods`` reports. An observation, never a setting (the tenant
        #: enables methods in Mollie's own dashboard).
        self.methods: list[str] = list(DEFAULT_METHODS)
        #: ``tr_…`` -> the payment object, exactly as Mollie would serialise it.
        self.payments: dict[str, dict[str, Any]] = {}
        #: operation (see :meth:`operation`) -> ``(status, title, detail)``: answer it as a
        #: Mollie error instead of doing the work.
        self.failures: dict[str, tuple[int, str, str]] = {}
        #: When set, a call whose Bearer token does not match is refused the way Mollie refuses
        #: a bad key. It is how a test proves a stored credential survived an unrelated PATCH.
        self.required_key: str | None = None
        #: Every call that arrived, as ``(method, path, body)`` — **never** the headers, and
        #: therefore never the API key. Asserting on what was *not* called is half the safety
        #: story: "the webhook re-fetched" and "the forged token touched nothing" are both
        #: statements about this list.
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        #: The profile every payment claims to belong to. A plain API key cannot read the
        #: Profiles API at all (hence no ``profile_id`` column, see ``mollie/models.py``), but
        #: Mollie does echo one on a payment and the client keeps it on the intent's trail.
        self.profile_id = "pfl_QkEhN94Ba"
        self._ids = itertools.count(1)

    # --- fixtures ---------------------------------------------------------------------- #
    def require_key(self, api_key: str) -> None:
        """Refuse every call that does not present exactly ``api_key``."""
        self.required_key = api_key

    def fail(
        self,
        operation: str,
        *,
        status: int = 422,
        title: str = "Unprocessable Entity",
        detail: str = "The payment could not be created",
    ) -> None:
        """Answer ``operation`` (``methods`` / ``payments.create`` / ``payments.get``) as a
        Mollie error. Cleared with :meth:`recover`."""
        self.failures[operation] = (status, title, detail)

    def recover(self, operation: str) -> None:
        """Stop failing ``operation`` — how a test says "the outage ended, now reconcile"."""
        self.failures.pop(operation, None)

    def add_payment(
        self,
        *,
        payment_id: str | None = None,
        status: str = "open",
        amount: Decimal | str = "10.00",
        currency: str = "EUR",
        mode: str = "live",
        method: str | None = None,
        description: str = "",
        metadata: dict[str, Any] | None = None,
        redirect_url: str | None = None,
        webhook_url: str | None = None,
    ) -> dict[str, Any]:
        """One payment, as ``POST /payments`` would have left it. Also usable directly, to set
        up a payment this schakl never created (somebody else's, or a forged reference)."""
        payment_id = payment_id or f"tr_{next(self._ids):010d}"
        row: dict[str, Any] = {
            "resource": "payment",
            "id": payment_id,
            "mode": mode,
            "createdAt": _now(),
            "amount": {"currency": currency, "value": str(amount)},
            "description": description,
            "method": method,
            "metadata": metadata or {},
            "status": status,
            "isCancelable": status in ("open", "pending"),
            "profileId": self.profile_id,
            "sequenceType": "oneoff",
            "redirectUrl": redirect_url,
            "webhookUrl": webhook_url,
            "_links": {"self": {"href": f"https://api.mollie.com/v2/payments/{payment_id}"}},
        }
        if status in ("open", "pending"):
            # The checkout link is a HAL link that *disappears* once a payment is no longer
            # payable — a fact worth modelling rather than a field worth defaulting.
            row["_links"]["checkout"] = {
                "href": f"{CHECKOUT_BASE}/{payment_id}",
                "type": "text/html",
            }
        self.payments[payment_id] = row
        return row

    def pay(
        self, payment_id: str, *, method: str = "ideal", paid_at: str | None = None
    ) -> dict[str, Any]:
        """The money arrived. The **only** way a test may make schakl settle anything."""
        row = self.payments[payment_id]
        row["status"] = "paid"
        row["method"] = method
        row["paidAt"] = paid_at or _now()
        row["isCancelable"] = False
        row["_links"].pop("checkout", None)
        return row

    def fail_payment(self, payment_id: str, *, detail: str | None = None) -> dict[str, Any]:
        """The payer's card was refused. ``detail`` lands on Mollie's own ``failureMessage``,
        which is untranslatable provider text and therefore belongs on the intent's
        ``last_error`` rather than in an error envelope (§9)."""
        row = self.payments[payment_id]
        row["status"] = "failed"
        row["failedAt"] = _now()
        row["isCancelable"] = False
        row["_links"].pop("checkout", None)
        if detail:
            row["details"] = {"failureMessage": detail}
        return row

    def expire(self, payment_id: str) -> dict[str, Any]:
        """The checkout was abandoned — iDEAL gives fifteen minutes, and clients use all of it."""
        row = self.payments[payment_id]
        row["status"] = "expired"
        row["expiredAt"] = _now()
        row["isCancelable"] = False
        row["_links"].pop("checkout", None)
        return row

    # --- assertions a test reads --------------------------------------------------------- #
    @property
    def last_payment(self) -> dict[str, Any]:
        """The most recently created payment.

        This is how a test learns the ``tr_…`` id, and that is on purpose: the id is *not* on
        ``InvoicePaymentIntentRead``, because a payer has no use for a provider's reference and
        the mapping is ours. A test that wants the id therefore asks the provider, exactly as
        the webhook does.
        """
        return self.payments[next(reversed(self.payments))]

    @property
    def paths(self) -> list[tuple[str, str]]:
        """``(method, path)`` for every call, in order."""
        return [(method, path) for method, path, _ in self.calls]

    def calls_matching(self, method: str, path: str) -> list[dict[str, Any]]:
        """The bodies of the calls that arrived for ``method path``.

        ``[]`` is the interesting answer: it proves a call was **not** made — that two "pay now"
        presses opened one checkout, or that a forged token never reached Mollie at all.
        """
        return [body for m, p, body in self.calls if m == method and p == path]

    # --- transport ------------------------------------------------------------------------ #
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    @staticmethod
    def operation(method: str, path: str) -> str:
        """The call's name, for scripting a failure against.

        Named rather than keyed on the raw path because a payment's id is minted inside this
        fake: a test that wanted "the re-fetch fails" would otherwise have to know the id before
        the payment it names exists.
        """
        if path == "/methods":
            return "methods"
        if path == "/payments":
            return "payments.create" if method == "POST" else "payments.list"
        if path.startswith("/payments/"):
            return "payments.get"
        return f"{method} {path}"

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith(PREFIX):
            path = path[len(PREFIX):]
        method = request.method
        body: dict[str, Any] = {}
        if request.content:
            try:
                parsed = json.loads(request.content)
            except (json.JSONDecodeError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                body = parsed
        # Recorded first, and without a single header: the credential is a Bearer token and
        # this list ends up in failure output.
        self.calls.append((method, path, body))

        lowered = path.lower()
        for fragment in FORBIDDEN_FRAGMENTS:
            if fragment in lowered:
                raise AssertionError(
                    f"the app called {method} {path} — schakl has no refund path, on purpose. "
                    "Moving money back is not reversible, so no code may acquire that power by "
                    "accident (app/core/payments/backend.py)."
                )

        if self.required_key is not None:
            presented = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
            if presented != self.required_key:
                return _error(
                    401,
                    "Unauthorized Request",
                    "Missing authentication, or failed to authenticate",
                )

        operation = self.operation(method, path)
        scripted = self.failures.get(operation)
        if scripted is not None:
            status, title, detail = scripted
            return _error(status, title, detail)

        if path == "/methods" and method == "GET":
            return self._methods()
        if path == "/payments" and method == "POST":
            return self._create_payment(request, body)
        if path.startswith("/payments/") and method == "GET":
            return self._get_payment(path.removeprefix("/payments/"))
        return _error(404, "Not Found", f"unhandled path {method} {path}")

    # --- endpoints -------------------------------------------------------------------- #
    def _methods(self) -> httpx.Response:
        rows = [
            {
                "resource": "method",
                "id": key,
                "description": _METHOD_LABELS.get(key, key),
                "status": "activated",
                "minimumAmount": {"value": "0.01", "currency": "EUR"},
            }
            for key in self.methods
        ]
        # ``count`` beside ``_embedded`` is Mollie's list shape; the client reads only the
        # embedded ids, and keeping the envelope honest is what keeps that true.
        return httpx.Response(200, json={"count": len(rows), "_embedded": {"methods": rows}})

    def _create_payment(self, request: httpx.Request, body: dict[str, Any]) -> httpx.Response:
        amount = body.get("amount") or {}
        key = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        row = self.add_payment(
            amount=amount.get("value", "0.00"),
            currency=amount.get("currency", "EUR"),
            # Derived from the credential, never from the request — same rule as ``mode_of``.
            mode="test" if key.startswith("test_") else "live",
            description=body.get("description", ""),
            metadata=body.get("metadata") or {},
            redirect_url=body.get("redirectUrl"),
            webhook_url=body.get("webhookUrl"),
        )
        return httpx.Response(201, json=row)

    def _get_payment(self, payment_id: str) -> httpx.Response:
        row = self.payments.get(payment_id)
        if row is None:
            # Mollie's own wording. The client turns a 404 here into ``None`` rather than an
            # error: a callback naming a payment this credential never created is somebody
            # else's payment or a forgery, and both are the same answer.
            return _error(
                404, "Not Found", f"No payment exists with token {payment_id}."
            )
        return httpx.Response(200, json=row)
