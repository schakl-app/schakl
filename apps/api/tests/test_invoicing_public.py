"""The public invoice link: what the token opens, and everything it must not (#304).

Written around the four ways a capability token in a URL goes wrong, because the endpoints
themselves are three lines each and none of the risk lives there.

* **It must open exactly one document.** A token names its own invoice and nothing adjacent —
  not the same company's other invoices, not another company's, not a draft, and not by id.
* **It must be a reader, not a session.** The context these routes build is a client-portal
  session scoped to one company holding two ``:own`` permissions, so the module's own ``:any``
  surfaces (the seller's bank details, the price list, the template library, the unbilled
  backlog with every employee's rate on it) are refused by the same gate that refuses a client.
  Asserted here as well as in ``test_invoicing_portal.py`` because "a client cannot" and "an
  anonymous holder of a link cannot" are two different claims about two different contexts.
* **It must not cross tenants.** The token is looked up *within the host's org*, never across
  them, so org A's link presented on org B's hostname is a 404 rather than a document.
* **It must be revocable, and revocation must be retroactive.** Unticking the setting kills a
  link that is already on paper — checked *before* the token is compared, so it withdraws
  every link at once rather than only the ones minted afterwards.

The fifth property is the one the feature was asked for: a payer returning from a checkout
sees their own payment without pressing reload. That is
``test_a_returning_payer_sees_the_settled_status_without_reloading``, and it is written the
way the money actually moves — the fake is paid, no callback is delivered at all (the webhook
being late or lost is the whole point), and the *refresh* is what finds it.
"""

from __future__ import annotations

import uuid as uuid_mod

import pytest
from sqlalchemy import select

from app.db import async_session_maker, set_current_org
from app.integrations.mollie import client as mollie_client
from app.modules.invoicing.models import Invoice
from tests.conftest import Tenant, auth_cookie, make_tenant
from tests.mollie_fake import FakeMollie
from tests.test_invoicing_payments import (
    LIVE_KEY,
    _company,
    _mollie_account,
    _open_invoice,
    _setup_org,
)


@pytest.fixture
def mollie() -> FakeMollie:
    fake = FakeMollie()
    mollie_client.set_transport(fake.transport())
    yield fake
    mollie_client.set_transport(None)


async def _token_of(org_id: uuid_mod.UUID, invoice_id: str) -> str | None:
    """The invoice's public token, read straight from the row.

    Deliberately not exposed on ``InvoiceRead``: the token is a credential, and a staff read of
    a document is not a reason to hand one out. The screen builds the link from it, but the
    tests read the column — which is also what makes "no token was minted" assertable at all.
    """
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        row = await session.scalar(
            select(Invoice).where(Invoice.id == uuid_mod.UUID(invoice_id))
        )
        return row.public_token if row is not None else None


async def _public(c, token: str, suffix: str = ""):
    return await c.get(f"/api/v1/invoicing/public/invoices/{token}{suffix}")


# --------------------------------------------------------------------------------------- #
# What the link opens
# --------------------------------------------------------------------------------------- #
async def test_the_link_opens_its_own_invoice_and_nothing_else(client_for) -> None:
    tenant: Tenant = await make_tenant("pub-open")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        company = await _company(client, headers)
        mine = await _open_invoice(client, headers, company)
        # A second invoice for the *same* company: the company horizon alone would let a
        # reader see this one too, so it is the case that proves the token is the fence.
        theirs = await _open_invoice(client, headers, company)
        draft = (
            await client.post(
                "/api/v1/invoicing/invoices",
                json={
                    "company_id": company,
                    "lines": [{"description": "X", "quantity": "1", "unit_price": "10"}],
                },
                headers=headers,
            )
        ).json()

        token = await _token_of(tenant.org.id, mine["id"])
        assert token, "issuing an invoice mints its public token"
        assert len(token) >= 40, "a guessable token is the one failure this feature cannot have"

        res = await _public(client, token)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["number"] == mine["number"]
        assert body["outstanding"] == mine["total"]
        # The narrow shape: no ids at all, so nothing here is a name to try somewhere else.
        assert not [key for key in body if key.endswith("_id") or key == "id"]
        assert "lines" not in body and "custom" not in body and "intents" not in body

        # The other invoice has its own token, and it is not this one.
        other = await _token_of(tenant.org.id, theirs["id"])
        assert other and other != token
        assert (await _public(client, other)).json()["number"] == theirs["number"]

        # A draft has no public address at all — #266's rule, one surface further out.
        assert await _token_of(tenant.org.id, draft["id"]) is None

        # And nothing else answers to a token-shaped string.
        assert (await _public(client, "x" * 43)).status_code == 404
        assert (await _public(client, str(uuid_mod.uuid4()))).status_code == 404
        assert (await _public(client, mine["id"])).status_code == 404, (
            "the invoice's own id must never be an address on this surface"
        )


async def test_the_document_and_the_pdf_are_the_same_artefact_staff_get(client_for) -> None:
    tenant: Tenant = await make_tenant("pub-doc")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        invoice = await _open_invoice(client, headers, await _company(client, headers))
        token = await _token_of(tenant.org.id, invoice["id"])

        public_html = await _public(client, token, "/preview")
        staff_html = await client.get(
            f"/api/v1/invoicing/invoices/{invoice['id']}/preview", headers=headers
        )
        assert public_html.status_code == 200
        assert public_html.text == staff_html.text, (
            "one renderer, so the page a client scans into cannot disagree with the paper"
        )
        # The headers are the security half of this route and are worth pinning: the token is
        # in the path, and the very next thing a payer does is leave for a provider.
        assert public_html.headers["referrer-policy"] == "no-referrer"
        assert "noindex" in public_html.headers["x-robots-tag"]

        pdf = await _public(client, token, "/pdf")
        assert pdf.status_code == 200
        assert pdf.content[:4] == b"%PDF"
        assert pdf.headers["referrer-policy"] == "no-referrer"


# --------------------------------------------------------------------------------------- #
# What it is not
# --------------------------------------------------------------------------------------- #
async def test_the_reader_reaches_no_other_surface_and_no_other_tenant(client_for) -> None:
    """The containment, stated as the two questions an attacker actually asks."""
    tenant: Tenant = await make_tenant("pub-fence")
    other: Tenant = await make_tenant("pub-fence-2")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        invoice = await _open_invoice(client, headers, await _company(client, headers))
        token = await _token_of(tenant.org.id, invoice["id"])
        assert (await _public(client, token)).status_code == 200

        # 1. Holding a link is not holding a session. Every signed-in surface still refuses,
        #    including the ones a *client* is refused for being `:any` (#266).
        for path in (
            "/api/v1/invoicing/invoices",
            "/api/v1/invoicing/settings",
            "/api/v1/invoicing/products",
            "/api/v1/invoicing/templates",
            "/api/v1/invoicing/unbilled",
            "/api/v1/companies",
            f"/api/v1/invoicing/invoices/{invoice['id']}",
        ):
            assert (await client.get(path)).status_code in (401, 403), path

    # 2. A token is looked up within the request's own tenant, never across them — so the same
    #    string on another org's hostname is simply not a document here.
    async with client_for(other.host) as client:
        assert (await _public(client, token)).status_code == 404


async def test_switching_the_setting_off_withdraws_links_already_printed(client_for) -> None:
    tenant: Tenant = await make_tenant("pub-switch")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        invoice = await _open_invoice(client, headers, await _company(client, headers))
        token = await _token_of(tenant.org.id, invoice["id"])
        assert (await _public(client, token)).status_code == 200

        off = await client.put(
            "/api/v1/invoicing/settings", json={"public_invoice_links": False}, headers=headers
        )
        assert off.status_code == 200, off.text
        assert off.json()["public_invoice_links"] is False

        # Retroactive, which is the only useful meaning of an off switch for a credential the
        # agency cannot collect back off a client's desk.
        assert (await _public(client, token)).status_code == 404
        assert (await _public(client, token, "/pdf")).status_code == 404

        # And a document issued while it is off is never given an address at all.
        later = await _open_invoice(client, headers, await _company(client, headers, "B BV"))
        assert await _token_of(tenant.org.id, later["id"]) is None

        # Back on: the old link works again (the token was never destroyed, only refused) and
        # the new invoice gets one the first time something needs to draw its QR.
        on = await client.put(
            "/api/v1/invoicing/settings", json={"public_invoice_links": True}, headers=headers
        )
        assert on.status_code == 200
        assert (await _public(client, token)).status_code == 200


# --------------------------------------------------------------------------------------- #
# Paying, and coming back
# --------------------------------------------------------------------------------------- #
async def test_a_public_payer_starts_a_checkout_for_what_is_owed_and_names_nothing(
    client_for, mollie: FakeMollie
) -> None:
    tenant: Tenant = await make_tenant("pub-pay")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        await _mollie_account(client, headers, api_key=LIVE_KEY)
        company = await _company(client, headers)
        invoice = await _open_invoice(client, headers, company)
        token = await _token_of(tenant.org.id, invoice["id"])

        # €210 already registered, so "outstanding" and "total" are different numbers and the
        # assertion below distinguishes them (the #267 rule, restated on the public surface).
        paid = await client.post(
            f"/api/v1/invoicing/invoices/{invoice['id']}/payments",
            json={"paid_on": "2026-01-05", "amount": "210.00", "method": "bank"},
            headers=headers,
        )
        assert paid.status_code in (200, 201), paid.text

        read = (await _public(client, token)).json()
        assert read["outstanding"] == "1000.00"
        assert read["payable"] is True

        res = await client.post(f"/api/v1/invoicing/public/invoices/{token}/payment-intents")
        assert res.status_code == 200, res.text
        assert res.json()["checkout_url"].startswith("https://")
        assert list(res.json()) == ["checkout_url"], "the payer gets a destination, not a row"

        payment = mollie.payments[next(iter(mollie.payments))]
        assert payment["amount"]["value"] == "1000.00", "outstanding, never the total"
        assert "/invoice/" in payment["redirectUrl"], (
            "a public payer comes back to the public page, not to a sign-in screen"
        )
        assert payment["redirectUrl"].endswith("?return=1")


async def test_a_returning_payer_sees_the_settled_status_without_reloading(
    client_for, mollie: FakeMollie
) -> None:
    """The bug this feature was asked to check: the redirect beats the webhook.

    Mollie's callback is asynchronous and makes no ordering promise against the browser
    redirect, so the page a payer lands on had already read the invoice before anything told
    us. It said *open* to the person who had just paid it, and the only control that could fix
    that was ``sync`` — ``:any``, staff-only. Here **no callback is delivered at all**, which
    is the honest model of "late or lost", and the landing's own refresh is what finds it.
    """
    tenant: Tenant = await make_tenant("pub-return")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        await _mollie_account(client, headers, api_key=LIVE_KEY)
        invoice = await _open_invoice(client, headers, await _company(client, headers))
        token = await _token_of(tenant.org.id, invoice["id"])

        started = await client.post(
            f"/api/v1/invoicing/public/invoices/{token}/payment-intents"
        )
        assert started.status_code == 200, started.text

        # The money moves at Mollie. Nothing tells us.
        payment_id = next(iter(mollie.payments))
        mollie.pay(payment_id)
        assert (await _public(client, token)).json()["status"] == "open"

        refreshed = await client.post(f"/api/v1/invoicing/public/invoices/{token}/refresh")
        assert refreshed.status_code == 200, refreshed.text
        body = refreshed.json()
        assert body["changed"] is True
        assert body["status"] == "paid"
        assert body["settled"] is True
        assert body["invoice_status"] == "paid"

        # …and the document itself agrees, which is what the page redraws from.
        assert (await _public(client, token)).json()["status"] == "paid"


async def test_the_public_refresh_is_throttled_so_it_cannot_amplify(
    client_for, mollie: FakeMollie
) -> None:
    """A POST anyone can reach must not become an outbound-call amplifier.

    The bound is per attempt and time-based, so the assertion is "the second press asked
    nobody" rather than a count of HTTP calls — which is also what the page's own polling
    relies on being free.
    """
    tenant: Tenant = await make_tenant("pub-throttle")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        await _mollie_account(client, headers, api_key=LIVE_KEY)
        invoice = await _open_invoice(client, headers, await _company(client, headers))
        token = await _token_of(tenant.org.id, invoice["id"])
        await client.post(f"/api/v1/invoicing/public/invoices/{token}/payment-intents")

        first = await client.post(f"/api/v1/invoicing/public/invoices/{token}/refresh")
        assert first.json()["changed"] is True
        for _ in range(5):
            again = await client.post(f"/api/v1/invoicing/public/invoices/{token}/refresh")
            assert again.status_code == 200
            assert again.json()["changed"] is False, "within the window, nobody is asked again"


async def test_a_settled_invoice_refuses_a_second_checkout_from_the_public_page(
    client_for, mollie: FakeMollie
) -> None:
    """``is_collectable``'s three conditions, reached from the surface that has no session.

    A paid invoice must not offer a pay button, and must not open a checkout if somebody posts
    to it anyway — the screen and the endpoint have to agree, or the first client to leave a
    tab open pays twice.
    """
    tenant: Tenant = await make_tenant("pub-settled")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        await _mollie_account(client, headers, api_key=LIVE_KEY)
        invoice = await _open_invoice(client, headers, await _company(client, headers))
        token = await _token_of(tenant.org.id, invoice["id"])

        settled = await client.post(
            f"/api/v1/invoicing/invoices/{invoice['id']}/payments",
            json={"paid_on": "2026-01-05", "amount": invoice["total"], "method": "bank"},
            headers=headers,
        )
        assert settled.status_code in (200, 201), settled.text

        read = (await _public(client, token)).json()
        assert read["status"] == "paid"
        assert read["outstanding"] == "0.00"
        assert read["payable"] is False, "no pay button on a settled invoice"

        res = await client.post(f"/api/v1/invoicing/public/invoices/{token}/payment-intents")
        assert res.status_code == 409, res.text
        assert res.json()["error"]["message"] == "errors.invoicing.payment_not_payable"
