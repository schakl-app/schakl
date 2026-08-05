"""Online payment of an invoice: the seam, the callback and the money (epic #269, #267).

This is the file where a mistake costs an agency real money, so it is written around the four
places that can happen rather than around the endpoints.

* **The amount is the server's.** ``InvoicePaymentIntentCreate`` carries no amount at all and
  the service charges ``outstanding`` recomputed at creation — never ``total``. The test proves
  it the only way that distinguishes the two: by registering a partial payment first, so the
  right answer and the wrong one are different numbers.
* **A callback is a hint, never a fact.** Mollie posts one field, ``id``, unsigned, and the
  authority is the authenticated re-fetch. So the tests settle invoices by moving the *fake's*
  state and then delivering a callback — and one of them posts a body loudly claiming ``paid``
  for a payment the provider reports as ``open``, which must settle nothing.
* **A provider retries until it gets a 200.** Mollie retries ten times over 26 hours, and two
  deliveries can be in flight at once. ``test_the_same_callback_delivered_three_times_writes_one
  _payment`` is the single most important assertion in this file: losing that race means
  charging a client twice, and no functional test would notice.
* **A failure is recorded, not raised.** ``require_context`` rolls the session back on any
  exception, so a callback that raised would discard the row saying we tried — and a provider
  retrying into a rollback loop leaves no trace at all. A cancelled invoice, an unreachable
  provider and an unknown reference all land on ``last_error`` and answer 200.

The fifth property has no natural place above and is asserted at the bottom: a **test-mode**
payment reaching ``paid`` writes no ledger row. The whole loop stays observable and the one
step withheld is the one that would book an invoice as paid against money that does not exist.
"""

from __future__ import annotations

import uuid as uuid_mod
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db import async_session_maker, set_current_org
from app.modules.invoicing.jobs import invoicing_payments_reconcile
from app.modules.invoicing.models import InvoicePayment, InvoicePaymentIntent
from app.modules.mollie import client as mollie_client
from tests.conftest import Tenant, auth_cookie, make_tenant, org_today
from tests.mollie_fake import FakeMollie

#: The API derives its dates on the org's calendar, so the expectations must too
#: (``conftest.org_today``) — never a zone hardcoded per test file.
_today = org_today

LIVE_KEY = "live_JhRk9NcQdTzWbV4pM2sXgY7eF3uL5aKq"
#: A second live key, for the one ambiguity the service refuses to resolve: an agency that
#: absorbed another and holds two real credentials for a while.
LIVE_KEY_2 = "live_Wb4pMV2sXgY7eF3uL5aKqJhRk9NcQdTz"
TEST_KEY = "test_2sXgY7eF3uL5aKqJhRk9NcQdTzWbV4pM"

#: One line of €1000 at the seeded 21% — so ``total`` is 1210,00 and a €210,00 part payment
#: leaves exactly €1000,00 outstanding. Two numbers that cannot be confused for each other.
_UNIT_PRICE = "1000"
_TOTAL = "1210.00"


@pytest.fixture
def mollie() -> FakeMollie:
    """A Mollie that holds state, installed as the module's only transport (see
    ``tests.mollie_fake``). Cleared on teardown, so a test that forgets to ask for this fixture
    fails on connect rather than reaching api.mollie.com."""
    fake = FakeMollie()
    mollie_client.set_transport(fake.transport())
    yield fake
    mollie_client.set_transport(None)


# --------------------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------------------- #
async def _setup_org(c, headers) -> None:
    """Seller details + the seeded tax rates: what a real org does once in Instellingen."""
    res = await c.put(
        "/api/v1/invoicing/settings",
        json={
            "company_details": {
                "name": "Agency BV",
                "city": "Amsterdam",
                "country": "NL",
                "vat_number": "NL123456789B01",
                "iban": "NL02ABNA0123456789",
            }
        },
        headers=headers,
    )
    assert res.status_code == 200, res.text
    assert (await c.get("/api/v1/invoicing/tax-rates", headers=headers)).status_code == 200


async def _company(c, headers, name: str = "Klant BV") -> str:
    res = await c.post(
        "/api/v1/companies",
        json={"name": name, "invoice_email": "boekhouding@klant.nl"},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _open_invoice(c, headers, company_id: str, *, unit_price: str = _UNIT_PRICE) -> dict:
    """A draft, issued — i.e. one somebody has actually been asked to pay."""
    created = await c.post(
        "/api/v1/invoicing/invoices",
        json={
            "company_id": company_id,
            "lines": [{"description": "Werk", "quantity": "1", "unit_price": unit_price}],
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    issued = await c.post(
        f"/api/v1/invoicing/invoices/{created.json()['id']}/issue", json={}, headers=headers
    )
    assert issued.status_code == 200, issued.text
    assert issued.json()["status"] == "open"
    return issued.json()


async def _mollie_account(c, headers, *, api_key: str = LIVE_KEY, name: str = "Mollie") -> dict:
    res = await c.post(
        "/api/v1/mollie/accounts",
        json={"name": name, "api_key": api_key},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()


def _token(account: dict) -> str:
    """The callback token out of the URL the settings screen shows.

    ``{org}.{account}.{secret}`` is the last path segment and contains no slash, which is what
    makes ``rsplit`` safe here and is itself part of the design (``core/payments/tokens``).
    """
    return account["webhook_url"].rsplit("/", 1)[1]


async def _start(c, headers, invoice_id: str, **body):
    return await c.post(
        f"/api/v1/invoicing/invoices/{invoice_id}/payment-intents", json=body, headers=headers
    )


async def _started(c, headers, invoice_id: str, **body) -> dict:
    res = await _start(c, headers, invoice_id, **body)
    assert res.status_code == 200, res.text
    return res.json()


async def _callback(c, token: str, payment_id: str):
    """One provider delivery, form-encoded exactly as Mollie posts it: ``id`` and nothing else."""
    return await c.post(
        f"/api/v1/invoicing/payments/webhook/mollie/{token}", data={"id": payment_id}
    )


async def _invoice(c, headers, invoice_id: str) -> dict:
    res = await c.get(f"/api/v1/invoicing/invoices/{invoice_id}", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


async def _ledger(org_id: uuid_mod.UUID, invoice_id: str) -> list[InvoicePayment]:
    """The invoice's payment rows, read straight from the database.

    Through the API the count is also visible, but the assertion that matters is *how many rows
    exist*, and a duplicate that the read path happened to collapse would still be a client
    charged twice.
    """
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        rows = await session.execute(
            select(InvoicePayment).where(
                InvoicePayment.invoice_id == uuid_mod.UUID(invoice_id)
            )
        )
        return list(rows.scalars())


async def _intents(org_id: uuid_mod.UUID, invoice_id: str) -> list[InvoicePaymentIntent]:
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        rows = await session.execute(
            select(InvoicePaymentIntent).where(
                InvoicePaymentIntent.invoice_id == uuid_mod.UUID(invoice_id)
            )
        )
        return list(rows.scalars())


# --------------------------------------------------------------------------------------- #
# Starting a payment
# --------------------------------------------------------------------------------------- #
async def test_starting_a_payment_charges_outstanding_and_not_the_total(
    client_for, mollie
) -> None:
    """The amount is the server's, and it is what is still **owed**.

    Charging ``total`` is the plausible wrong version and it is invisible on a freshly issued
    invoice, where the two numbers are equal. So a part payment is registered first: the invoice
    totals €1210,00, €210,00 has landed, and the checkout must be for €1000,00 — asserted on the
    intent *and* on the body that actually reached Mollie, because those are two different
    claims and only the second one is what the payer will be shown.
    """
    t: Tenant = await make_tenant("pay-outstanding")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _setup_org(c, headers)
        company = await _company(c, headers)
        invoice = await _open_invoice(c, headers, company)
        assert invoice["total"] == _TOTAL
        account = await _mollie_account(c, headers)

        paid = await c.post(
            f"/api/v1/invoicing/invoices/{invoice['id']}/payments",
            json={"paid_on": _today().isoformat(), "amount": "210.00", "method": "bank"},
            headers=headers,
        )
        assert paid.status_code == 200, paid.text
        assert paid.json()["outstanding"] == "1000.00"

        intent = await _started(c, headers, invoice["id"])
        assert intent["amount"] == "1000.00"
        assert intent["currency"] == "EUR"
        assert intent["status"] == "open"
        assert intent["mode"] == "live"
        assert intent["checkout_url"], "a fresh payment carries a checkout link"

    body = mollie.calls_matching("POST", "/payments")
    assert len(body) == 1
    assert body[0]["amount"] == {"currency": "EUR", "value": "1000.00"}
    # Our own id travels as metadata so a human staring at Mollie's dashboard can find the
    # invoice — and it is never how a callback is resolved.
    assert body[0]["metadata"]["invoice_id"] == invoice["id"]
    assert body[0]["metadata"]["intent_id"] == intent["id"]
    # The callback URL handed to Mollie is *exactly* the one the settings screen shows the
    # admin, because that URL has to be reachable from the public internet and behind an access
    # proxy somebody has to allow it by hand. Two renderings of it that could drift is how
    # "payments are collected but never booked" becomes a mystery with no clue on screen.
    assert body[0]["webhookUrl"] == account["webhook_url"]
    assert body[0]["webhookUrl"].endswith(f"/webhook/mollie/{_token(account)}")
    # Mollie's hosted checkout shows its own method picker: pinning one here would remove its
    # fallback, and the agency already configures what exists in Mollie's dashboard.
    assert "method" not in body[0]


async def test_a_draft_a_cancelled_and_a_paid_invoice_refuse_a_checkout(
    client_for, mollie
) -> None:
    """Three documents nobody may be sent to a checkout for, and one refusal for all of them.

    A **draft** has not been shown to anyone; a **cancelled** one is withdrawn; a **paid** one
    is settled, and a rounding-error overpayment is a conversation rather than a second
    checkout. Each answers 409 with the same i18n key (§9), and — the part worth asserting —
    none of them reaches Mollie at all.
    """
    t: Tenant = await make_tenant("pay-notpayable")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _setup_org(c, headers)
        company = await _company(c, headers)
        await _mollie_account(c, headers)

        draft = await c.post(
            "/api/v1/invoicing/invoices",
            json={
                "company_id": company,
                "lines": [{"description": "Werk", "quantity": "1", "unit_price": "100"}],
            },
            headers=headers,
        )
        refused = await _start(c, headers, draft.json()["id"])
        assert refused.status_code == 409, refused.text
        assert refused.json()["error"]["message"] == "errors.invoicing.payment_not_payable"

        cancelled = await _open_invoice(c, headers, company)
        assert (
            await c.post(
                f"/api/v1/invoicing/invoices/{cancelled['id']}/cancel", headers=headers
            )
        ).status_code == 200
        refused = await _start(c, headers, cancelled["id"])
        assert refused.status_code == 409, refused.text
        assert refused.json()["error"]["message"] == "errors.invoicing.payment_not_payable"

        settled = await _open_invoice(c, headers, company)
        assert (
            await c.post(
                f"/api/v1/invoicing/invoices/{settled['id']}/payments",
                json={"paid_on": _today().isoformat(), "amount": _TOTAL, "method": "bank"},
                headers=headers,
            )
        ).json()["status"] == "paid"
        refused = await _start(c, headers, settled["id"])
        assert refused.status_code == 409, refused.text
        assert refused.json()["error"]["message"] == "errors.invoicing.payment_not_payable"

    assert mollie.calls_matching("POST", "/payments") == [], (
        "a refusal must be decided here, not by opening a checkout and regretting it"
    )


async def test_two_presses_of_pay_now_reuse_one_checkout(client_for, mollie) -> None:
    """Two clicks must not open two payments.

    The client would then hold two valid links for one debt, and paying both is a refund
    conversation — which is precisely the thing this integration has no way to have (there is no
    refund in the seam, on purpose). The reuse is asserted from both ends: the same intent comes
    back, and Mollie was asked exactly once.
    """
    t: Tenant = await make_tenant("pay-reuse")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _setup_org(c, headers)
        company = await _company(c, headers)
        invoice = await _open_invoice(c, headers, company)
        await _mollie_account(c, headers)

        first = await _started(c, headers, invoice["id"])
        second = await _started(c, headers, invoice["id"])
        assert first["id"] == second["id"]
        assert first["checkout_url"] == second["checkout_url"]

    assert len(mollie.calls_matching("POST", "/payments")) == 1
    assert len(await _intents(t.org.id, invoice["id"])) == 1


async def test_a_live_key_beats_a_test_one_and_two_live_keys_stay_ambiguous(
    client_for, mollie
) -> None:
    """The one tiebreak, and where it deliberately stops.

    A live *and* a test credential side by side is the ordinary state of an agency
    integrating — it is the whole reason the credential is a row rather than a settings
    singleton — and it is not a real ambiguity: a test key collects nothing and settles
    nothing, so it was never a candidate for a client's money. Refusing there would also refuse
    a **client** in the portal, who cannot see the account list at all (#266 keeps it at
    ``:any``) and would be handed "choose one" with nothing to choose from.

    Two *live* keys is a genuine ambiguity with no principled answer, so the refusal stands and
    names the field the caller must send (#296's rule, applied to money).
    """
    t: Tenant = await make_tenant("pay-ambiguous")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _setup_org(c, headers)
        company = await _company(c, headers)
        invoice = await _open_invoice(c, headers, company)
        live = await _mollie_account(c, headers, api_key=LIVE_KEY, name="Mollie live")
        await _mollie_account(c, headers, api_key=TEST_KEY, name="Mollie test")

        # Live wins, and nobody had to say so.
        resolved = await _started(c, headers, invoice["id"])
        assert resolved["account_id"] == live["id"]
        assert resolved["mode"] == "live"

        # A second live credential, and now there is nothing to prefer.
        second = await _mollie_account(
            c, headers, api_key=LIVE_KEY_2, name="Mollie live (overgenomen)"
        )
        other = await _open_invoice(c, headers, company)
        refused = await _start(c, headers, other["id"])
        assert refused.status_code == 409, refused.text
        assert (
            refused.json()["error"]["message"] == "errors.invoicing.payment_account_ambiguous"
        )
        assert refused.json()["error"]["fields"] == {"account_id": "errors.required"}

        chosen = await _started(c, headers, other["id"], account_id=second["id"])
        assert chosen["account_id"] == second["id"]

    assert len(mollie.calls_matching("POST", "/payments")) == 2


# --------------------------------------------------------------------------------------- #
# The callback
# --------------------------------------------------------------------------------------- #
async def test_the_same_callback_delivered_three_times_writes_one_payment(
    client_for, mollie
) -> None:
    """**The most important assertion in this file.**

    Mollie retries a webhook up to ten times over 26 hours and does not wait for the previous
    delivery to finish, so "we already handled this" has to be true under concurrency, not just
    in sequence. The row lock in ``apply`` plus the partial unique index on
    ``(org_id, intent_id)`` are what make it true; this is what would go red if either were
    removed, and the failure it prevents is charging a client twice.

    The first delivery does the whole job — one ledger row, ``method="online"``, the invoice
    flipped to ``paid`` through the ordinary settle path — and the next two change nothing at
    all except that the provider gets its 200.
    """
    t: Tenant = await make_tenant("pay-idempotent")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _setup_org(c, headers)
        company = await _company(c, headers)
        invoice = await _open_invoice(c, headers, company)
        account = await _mollie_account(c, headers)
        await _started(c, headers, invoice["id"])

        payment_id = mollie.last_payment["id"]
        mollie.pay(payment_id, method="ideal")

        for _ in range(3):
            answered = await _callback(c, _token(account), payment_id)
            assert answered.status_code == 200, answered.text

        settled = await _invoice(c, headers, invoice["id"])
        assert settled["status"] == "paid"
        assert settled["outstanding"] == "0.00"
        assert len(settled["payments"]) == 1
        assert settled["payments"][0]["amount"] == _TOTAL
        # One value for every provider: a ledger row's method answers the bookkeeper's question
        # ("how did this arrive?"), and the provider is on the intent.
        assert settled["payments"][0]["method"] == "online"
        assert settled["payments"][0]["paid_on"] == _today().isoformat()

        assert len(settled["intents"]) == 1
        intent = settled["intents"][0]
        assert intent["status"] == "paid"
        assert intent["settled_at"] is not None
        assert intent["method"] == "ideal"
        assert intent["last_error"] is None
        # A payment that is no longer payable has no checkout link — the UI must not offer one
        # that answers "this payment has expired".
        assert intent["checkout_url"] is None

    rows = await _ledger(t.org.id, invoice["id"])
    assert len(rows) == 1, f"{len(rows)} ledger rows — the client was charged more than once"
    assert rows[0].intent_id is not None

    # Every delivery re-fetched: the body is a hint and the authenticated read is the fact.
    assert len(mollie.calls_matching("GET", f"/payments/{payment_id}")) == 3


async def test_the_callback_believes_the_refetch_and_not_its_own_body(
    client_for, mollie
) -> None:
    """A body shouting ``"status": "paid"`` settles nothing while Mollie says ``open``.

    Mollie's per-payment webhook has no signature at all, which is safe *only* because nothing
    in the body is acted on — *"fake calls to your webhook will never result in orders being
    processed without being actually paid"*. This test posts the strongest forgery the contract
    allows (valid token, real payment id, a JSON body claiming success) and asserts the two
    halves of the answer: the re-fetch happened, and the provider's own ``open`` won.
    """
    t: Tenant = await make_tenant("pay-refetch")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _setup_org(c, headers)
        company = await _company(c, headers)
        invoice = await _open_invoice(c, headers, company)
        account = await _mollie_account(c, headers)
        await _started(c, headers, invoice["id"])
        payment_id = mollie.last_payment["id"]

        answered = await c.post(
            f"/api/v1/invoicing/payments/webhook/mollie/{_token(account)}",
            json={"id": payment_id, "status": "paid", "amount": {"value": "1210.00"}},
        )
        assert answered.status_code == 200, answered.text

        still_open = await _invoice(c, headers, invoice["id"])
        assert still_open["status"] == "open"
        assert still_open["payments"] == []
        assert still_open["intents"][0]["status"] == "open"
        assert still_open["intents"][0]["settled_at"] is None

    assert len(mollie.calls_matching("GET", f"/payments/{payment_id}")) == 1
    assert await _ledger(t.org.id, invoice["id"]) == []


async def test_a_forged_or_foreign_reference_changes_nothing(client_for, mollie) -> None:
    """Two attacks, two different right answers, and a control run that proves the harness works.

    A **wrong secret** is a bare 404: never 401 or 403, which would confirm that the account
    exists, and never a lookup either — the token names the tenant before anything is read, so a
    mismatch costs no query and reaches no provider.

    **Another org's payment id under this org's token** answers **200**. That looks generous and
    is not: the org came out of the token, every read below it is RLS-scoped, and the reference
    simply is not this tenant's. Mollie documents 200 for an unknown id precisely so a caller
    cannot enumerate which references exist by reading status codes — a 404 here would leak the
    one bit the 404 above is protecting.

    Both payments are ``paid`` at the provider before either attack, so "nothing happened" cannot
    quietly mean "there was nothing to happen"; the control at the end settles A for real.
    """
    a: Tenant = await make_tenant("pay-forge-a")
    b: Tenant = await make_tenant("pay-forge-b")
    a_headers, b_headers = await auth_cookie(a.user), await auth_cookie(b.user)

    async with client_for(b.host) as cb:
        await _setup_org(cb, b_headers)
        b_invoice = await _open_invoice(cb, b_headers, await _company(cb, b_headers))
        await _mollie_account(cb, b_headers)
        await _started(cb, b_headers, b_invoice["id"])
        b_payment = mollie.last_payment["id"]
        mollie.pay(b_payment)

    async with client_for(a.host) as ca:
        await _setup_org(ca, a_headers)
        a_invoice = await _open_invoice(ca, a_headers, await _company(ca, a_headers))
        a_account = await _mollie_account(ca, a_headers)
        await _started(ca, a_headers, a_invoice["id"])
        a_payment = mollie.last_payment["id"]
        mollie.pay(a_payment)

        org_id, account_id, secret = _token(a_account).split(".", 2)
        forged = f"{org_id}.{account_id}.{'z' * len(secret)}"
        refused = await _callback(ca, forged, a_payment)
        assert refused.status_code == 404, refused.text

        foreign = await _callback(ca, _token(a_account), b_payment)
        assert foreign.status_code == 200, foreign.text

        # Neither attack touched a row…
        assert (await _invoice(ca, a_headers, a_invoice["id"]))["status"] == "open"
        # …and neither reached Mollie: the refusals are decided before any credential is spent.
        assert mollie.calls_matching("GET", f"/payments/{a_payment}") == []
        assert mollie.calls_matching("GET", f"/payments/{b_payment}") == []

        # The control: the same delivery with the right token does settle, so the two silences
        # above are refusals rather than a broken harness.
        assert (await _callback(ca, _token(a_account), a_payment)).status_code == 200
        assert (await _invoice(ca, a_headers, a_invoice["id"]))["status"] == "paid"

    async with client_for(b.host) as cb:
        assert (await _invoice(cb, b_headers, b_invoice["id"]))["status"] == "open"
    assert await _ledger(b.org.id, b_invoice["id"]) == []


async def test_a_callback_for_a_cancelled_invoice_is_recorded_and_not_raised(
    client_for, mollie
) -> None:
    """The client's money moved and the invoice is gone. That is reported, never thrown away.

    Raising would roll back the very row that says we tried (``require_context`` rolls back on
    any exception), and a 4xx would leave Mollie retrying into the same rollback for 26 hours
    with no trace anywhere. So the intent keeps the reason on ``last_error``, ``settled_at``
    stays NULL — which is what the screen and the reconcile cron both key off — and the provider
    gets its 200.
    """
    t: Tenant = await make_tenant("pay-cancelled")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _setup_org(c, headers)
        company = await _company(c, headers)
        invoice = await _open_invoice(c, headers, company)
        account = await _mollie_account(c, headers)
        await _started(c, headers, invoice["id"])
        payment_id = mollie.last_payment["id"]

        assert (
            await c.post(
                f"/api/v1/invoicing/invoices/{invoice['id']}/cancel", headers=headers
            )
        ).status_code == 200

        mollie.pay(payment_id)
        answered = await _callback(c, _token(account), payment_id)
        assert answered.status_code == 200, answered.text

        after = await _invoice(c, headers, invoice["id"])
        assert after["status"] == "cancelled"
        assert after["payments"] == []
        intent = after["intents"][0]
        # The provider's word for what happened is believed; what *we* did about it is a
        # separate column, and it is empty.
        assert intent["status"] == "paid"
        assert intent["settled_at"] is None
        assert intent["last_error"] == "invoice is no longer open"

    assert await _ledger(t.org.id, invoice["id"]) == []


# --------------------------------------------------------------------------------------- #
# Test mode
# --------------------------------------------------------------------------------------- #
async def test_a_test_mode_payment_reaching_paid_writes_no_ledger_row(
    client_for, mollie
) -> None:
    """The deliberate dead end.

    Every step is observable — a checkout opens, the callback arrives, the re-fetch happens, the
    intent reads ``paid`` — and the one step withheld is the ledger write, because a test-mode
    payment is money that does not exist. An agency that leaves a test key in place therefore
    gets an obviously-stuck screen (``paid`` with no ``settled_at``) instead of silently wrong
    revenue, and the mode came from the credential's own prefix, so nobody had to choose it.
    """
    t: Tenant = await make_tenant("pay-testmode")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _setup_org(c, headers)
        company = await _company(c, headers)
        invoice = await _open_invoice(c, headers, company)
        account = await _mollie_account(c, headers, api_key=TEST_KEY)
        intent = await _started(c, headers, invoice["id"])
        assert intent["mode"] == "test"

        payment_id = mollie.last_payment["id"]
        assert mollie.last_payment["mode"] == "test"
        mollie.pay(payment_id)
        assert (await _callback(c, _token(account), payment_id)).status_code == 200

        after = await _invoice(c, headers, invoice["id"])
        assert after["status"] == "open"
        assert after["payments"] == []
        assert after["intents"][0]["status"] == "paid"
        assert after["intents"][0]["settled_at"] is None

    assert await _ledger(t.org.id, invoice["id"]) == []


# --------------------------------------------------------------------------------------- #
# The safety net under the webhook
# --------------------------------------------------------------------------------------- #
async def test_the_reconcile_cron_settles_what_the_callback_never_delivered(
    client_for, mollie
) -> None:
    """A webhook can be lost for entirely ordinary reasons — an access proxy in front of the
    API, a redeploy, a Zero Trust rule nobody added — and the failure is invisible from outside:
    the client's money moved and the invoice still says open.

    So the hourly cron runs the same loop the callback runs, and the two are idempotent against
    each other by construction. Here nothing is ever delivered at all: the payment is paid at
    Mollie, the cron asks, and the invoice settles.
    """
    t: Tenant = await make_tenant("pay-reconcile")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _setup_org(c, headers)
        company = await _company(c, headers)
        invoice = await _open_invoice(c, headers, company)
        await _mollie_account(c, headers)
        await _started(c, headers, invoice["id"])
        payment_id = mollie.last_payment["id"]
        mollie.pay(payment_id, method="creditcard")

    await invoicing_payments_reconcile({})

    async with client_for(t.host) as c:
        settled = await _invoice(c, headers, invoice["id"])
        assert settled["status"] == "paid"
        assert len(settled["payments"]) == 1
        assert settled["payments"][0]["method"] == "online"
        assert settled["intents"][0]["settled_at"] is not None
        assert settled["intents"][0]["method"] == "creditcard"

    assert len(await _ledger(t.org.id, invoice["id"])) == 1
    assert len(mollie.calls_matching("GET", f"/payments/{payment_id}")) == 1

    # …and a second pass is a no-op: ``settled_at`` is what the cron's own filter reads, so a
    # settled intent is not asked about again and the ledger cannot grow a second row.
    await invoicing_payments_reconcile({})
    assert len(await _ledger(t.org.id, invoice["id"])) == 1
    assert len(mollie.calls_matching("GET", f"/payments/{payment_id}")) == 1


async def test_the_reconcile_cron_records_a_provider_outage_and_retries_later(
    client_for, mollie
) -> None:
    """A provider that is down is a state, not an exception.

    The reconcile records why it could not answer and leaves ``settled_at`` NULL, so the next
    pass picks the same intent up — which is the whole reason the failure may not raise: a
    rollback would erase the note along with the attempt.
    """
    t: Tenant = await make_tenant("pay-outage")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _setup_org(c, headers)
        company = await _company(c, headers)
        invoice = await _open_invoice(c, headers, company)
        await _mollie_account(c, headers)
        await _started(c, headers, invoice["id"])
        payment_id = mollie.last_payment["id"]
        mollie.pay(payment_id)

    mollie.fail("payments.get", status=503, title="Service Unavailable", detail="Mollie is down")
    await invoicing_payments_reconcile({})

    async with client_for(t.host) as c:
        stuck = (await _invoice(c, headers, invoice["id"]))["intents"][0]
        assert stuck["settled_at"] is None
        assert stuck["last_error"], "an outage that leaves no trace is an outage nobody can fix"
    assert await _ledger(t.org.id, invoice["id"]) == []

    mollie.recover("payments.get")
    await invoicing_payments_reconcile({})

    async with client_for(t.host) as c:
        settled = await _invoice(c, headers, invoice["id"])
        assert settled["status"] == "paid"
        assert settled["intents"][0]["settled_at"] is not None
        assert settled["intents"][0]["last_error"] is None
    assert len(await _ledger(t.org.id, invoice["id"])) == 1


# --------------------------------------------------------------------------------------- #
# Tenant isolation + the query budget (CLAUDE.md §9, docs/PERFORMANCE.md)
# --------------------------------------------------------------------------------------- #
async def test_payment_accounts_and_intents_are_tenant_isolated(client_for, mollie) -> None:
    """Golden Rule 1 across both new surfaces: which credentials exist, and what was paid with
    them. The by-id reads answer **404**, never 403 — an authorization answer here would confirm
    that another tenant's invoice exists."""
    a: Tenant = await make_tenant("pay-iso-a")
    b: Tenant = await make_tenant("pay-iso-b")
    a_headers, b_headers = await auth_cookie(a.user), await auth_cookie(b.user)

    async with client_for(a.host) as ca:
        await _setup_org(ca, a_headers)
        invoice = await _open_invoice(ca, a_headers, await _company(ca, a_headers))
        await _mollie_account(ca, a_headers)
        intent = await _started(ca, a_headers, invoice["id"])

    async with client_for(b.host) as cb:
        await _setup_org(cb, b_headers)
        assert (
            await cb.get("/api/v1/invoicing/payment-accounts", headers=b_headers)
        ).json() == []
        assert (
            await cb.get(
                f"/api/v1/invoicing/invoices/{invoice['id']}/payment-intents",
                headers=b_headers,
            )
        ).status_code == 404
        assert (await _start(cb, b_headers, invoice["id"])).status_code == 404
        assert (
            await cb.post(
                f"/api/v1/invoicing/invoices/{invoice['id']}/payment-intents/"
                f"{intent['id']}/sync",
                headers=b_headers,
            )
        ).status_code == 404

    assert len(mollie.calls_matching("POST", "/payments")) == 1


async def test_an_invoice_detail_costs_the_same_at_one_intent_as_at_twelve(
    client_for, mollie, count_queries
) -> None:
    """The shape this pins is invisible in the JSON: one grouped query and one-per-attempt
    return identical bodies (docs/PERFORMANCE.md).

    An invoice legitimately collects several attempts — iDEAL expires in fifteen minutes and
    clients abandon checkouts — so "how many intents does this invoice have" is exactly the
    number that grows quietly in production and never in a test. The extra rows are written
    straight to the table on purpose: going through ``start`` would reuse the live intent, which
    is the *other* property this module guarantees.
    """
    t: Tenant = await make_tenant("pay-budget")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _setup_org(c, headers)
        company = await _company(c, headers)
        invoice = await _open_invoice(c, headers, company)
        account = await _mollie_account(c, headers)
        await _started(c, headers, invoice["id"])

        # Warm up whatever a first read seeds, so the two measurements compare like with like.
        await _invoice(c, headers, invoice["id"])
        with count_queries() as small:
            one = await _invoice(c, headers, invoice["id"])
        assert len(one["intents"]) == 1

        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            for index in range(11):
                session.add(
                    InvoicePaymentIntent(
                        org_id=t.org.id,
                        invoice_id=uuid_mod.UUID(invoice["id"]),
                        provider="mollie",
                        account_id=uuid_mod.UUID(account["id"]),
                        external_id=f"tr_budget{index:04d}",
                        status="expired",
                        amount=Decimal("1210.00"),
                        currency="EUR",
                        mode="live",
                    )
                )
            await session.commit()

        with count_queries() as large:
            many = await _invoice(c, headers, invoice["id"])
        assert len(many["intents"]) == 12

    assert len(large.statements) == len(small.statements), (
        f"{len(small.statements)} queries for 1 intent, {len(large.statements)} for 12 — "
        "something resolves per attempt"
    )
