"""Crediting reaches the balance, not just the paperwork (#207 follow-up).

A credit note used to be a document and nothing else. It corrected what the client was
*shown* and left every number alone, so the invoice it corrected stayed open, stayed in
arrears and kept being dunned — and the credit note itself, whose total is negative, could
never satisfy ``paid_total >= total`` and so stayed open for good.

What these tests pin is the allocation: an issued credit note writes its source down by what
that source still had room for, and whatever it could not absorb is a refund the client is
owed. That one rule is the whole difference between the two cases an agency actually has:

* crediting an **open** invoice — nobody paid anything, the two documents cancel out;
* crediting a **paid** one — the money is already in, so it has to go back.
"""

from __future__ import annotations

from datetime import timedelta

from tests.conftest import Tenant, auth_cookie, make_tenant
from tests.test_invoicing_api import _company, _setup_org, _today


async def _issued_invoice(client, headers, company_id, unit_price="300", **issue):
    """An issued invoice of 1 × unit_price + 21% — the shape every case below starts from."""
    invoice = (
        await client.post(
            "/api/v1/invoicing/invoices",
            json={
                "company_id": company_id,
                "lines": [
                    {"description": "W", "quantity": "1", "unit_price": unit_price}
                ],
            },
            headers=headers,
        )
    ).json()
    resp = await client.post(
        f"/api/v1/invoicing/invoices/{invoice['id']}/issue", json=issue, headers=headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _credit(client, headers, invoice_id) -> dict:
    resp = await client.post(
        f"/api/v1/invoicing/invoices/{invoice_id}/credit", headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _get(client, headers, invoice_id) -> dict:
    resp = await client.get(f"/api/v1/invoicing/invoices/{invoice_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_crediting_an_open_invoice_cancels_both_documents(client_for) -> None:
    """The everyday correction: an invoice nobody paid, credited in full.

    Neither side owes anything afterwards, and — the actual bug — the invoice stops being
    something the system chases.
    """
    tenant: Tenant = await make_tenant("credit-open")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        company_id = await _company(client, headers)
        invoice = await _issued_invoice(client, headers, company_id)
        assert invoice["outstanding"] == "363.00"

        credit = await _credit(client, headers, invoice["id"])
        # A draft allocates nothing: it is not a document yet.
        assert credit["status"] == "draft"
        assert (await _get(client, headers, invoice["id"]))["credited_total"] == "0.00"

        issued = (
            await client.post(
                f"/api/v1/invoicing/invoices/{credit['id']}/issue",
                json={},
                headers=headers,
            )
        ).json()
        # The invoice had room for the whole thing, so the credit note is fully absorbed
        # and comes to rest without a cent moving.
        assert issued["applied_total"] == "363.00"
        assert issued["outstanding"] == "0.00"
        assert issued["status"] == "paid"
        assert issued["paid_total"] == "0.00"  # settled, not refunded

        source = await _get(client, headers, invoice["id"])
        assert source["credited_total"] == "363.00"
        assert source["outstanding"] == "0.00"
        assert source["fully_credited"] is True
        assert source["credited"] is True
        # Still `open` on purpose: nobody paid it, and `paid` would book it as revenue.
        assert source["status"] == "open"
        assert source["paid_total"] == "0.00"


async def test_crediting_a_paid_invoice_leaves_a_refund_to_register(client_for) -> None:
    """The other half of the ask: the client already paid, so crediting owes them money back.

    The source has no room left, so the credit note absorbs nothing and stays open for the
    exact amount of the refund — which is the thing that had no home before.
    """
    tenant: Tenant = await make_tenant("credit-paid")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        company_id = await _company(client, headers)
        invoice = await _issued_invoice(client, headers, company_id)
        paid = (
            await client.post(
                f"/api/v1/invoicing/invoices/{invoice['id']}/payments",
                json={"paid_on": _today().isoformat(), "amount": "363"},
                headers=headers,
            )
        ).json()
        assert paid["status"] == "paid"

        credit = await _credit(client, headers, invoice["id"])
        issued = (
            await client.post(
                f"/api/v1/invoicing/invoices/{credit['id']}/issue",
                json={},
                headers=headers,
            )
        ).json()
        assert issued["applied_total"] == "0.00"  # nothing to absorb: it was paid
        assert issued["outstanding"] == "-363.00"  # we owe the client
        assert issued["status"] == "open"

        # The invoice keeps its history: it *was* paid, and crediting does not undo that.
        source = await _get(client, headers, invoice["id"])
        assert source["status"] == "paid"
        assert source["paid_total"] == "363.00"
        assert source["credited_total"] == "0.00"

        # Paying the client back is a negative payment on the credit note, and *that* is
        # what brings it to rest — the transition that was unreachable before.
        refunded = (
            await client.post(
                f"/api/v1/invoicing/invoices/{issued['id']}/payments",
                json={"paid_on": _today().isoformat(), "amount": "-363"},
                headers=headers,
            )
        ).json()
        assert refunded["status"] == "paid"
        assert refunded["outstanding"] == "0.00"
        assert refunded["paid_total"] == "-363.00"


async def test_a_partial_credit_leaves_the_rest_owing(client_for) -> None:
    """Editing the draft down before issuing credits only part of the invoice."""
    tenant: Tenant = await make_tenant("credit-partial")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        company_id = await _company(client, headers)
        invoice = await _issued_invoice(client, headers, company_id)

        credit = await _credit(client, headers, invoice["id"])
        # A credit note is a draft, so its money is still editable (§ issued money is
        # immutable applies from `open`).
        patched = await client.patch(
            f"/api/v1/invoicing/invoices/{credit['id']}",
            json={
                "lines": [{"description": "W", "quantity": "1", "unit_price": "-100"}]
            },
            headers=headers,
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["total"] == "-121.00"

        issued = (
            await client.post(
                f"/api/v1/invoicing/invoices/{credit['id']}/issue",
                json={},
                headers=headers,
            )
        ).json()
        assert issued["applied_total"] == "121.00"
        assert issued["status"] == "paid"

        source = await _get(client, headers, invoice["id"])
        assert source["credited_total"] == "121.00"
        assert source["outstanding"] == "242.00"  # 363 − 121, still owed
        assert source["fully_credited"] is False
        assert source["credited"] is True


async def test_a_credited_invoice_is_neither_overdue_nor_in_arrears(client_for) -> None:
    """The dashboard and the due-date badge both read the netted figure.

    An invoice written off in full is not late — it is settled — and the arrears tiles are
    about money clients owe, which a credit note is not.
    """
    tenant: Tenant = await make_tenant("credit-arrears")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        company_id = await _company(client, headers)
        long_ago = (_today() - timedelta(days=30)).isoformat()
        invoice = await _issued_invoice(
            client, headers, company_id, due_date=long_ago
        )
        assert invoice["overdue"] is True

        before = (await client.get("/api/v1/invoicing/summary", headers=headers)).json()
        assert before["open_count"] == 1
        assert float(before["open_total"]) == 363.0
        assert float(before["overdue_total"]) == 363.0

        credit = await _credit(client, headers, invoice["id"])
        await client.post(
            f"/api/v1/invoicing/invoices/{credit['id']}/issue",
            json={"due_date": long_ago},
            headers=headers,
        )

        source = await _get(client, headers, invoice["id"])
        assert source["overdue"] is False

        after = (await client.get("/api/v1/invoicing/summary", headers=headers)).json()
        # Neither document is a receivable now: not the written-off invoice, and not the
        # credit note, which was itself being counted as an open document before.
        assert after["open_count"] == 0
        assert float(after["open_total"]) == 0.0
        assert float(after["overdue_count"]) == 0
        assert float(after["overdue_total"]) == 0.0
        # And nothing was received, so nothing enters the year's takings. A credit note that
        # settled at `total` would have booked −363 against revenue the invoice it cancelled
        # never contributed — the tile would read a loss on a year where no money moved.
        assert float(after["paid_this_year"]) == 0.0


async def test_the_year_s_takings_are_money_that_moved(client_for) -> None:
    """`paid_this_year` counts receipts, so an applied credit note is worth nothing to it and
    a refunded one costs exactly what went back."""
    tenant: Tenant = await make_tenant("credit-takings")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        company_id = await _company(client, headers)

        # Paid and kept: the whole receipt counts.
        kept = await _issued_invoice(client, headers, company_id, unit_price="300")
        await client.post(
            f"/api/v1/invoicing/invoices/{kept['id']}/payments",
            json={"paid_on": _today().isoformat(), "amount": "363"},
            headers=headers,
        )
        # Never paid, then credited: the credit note settles by application, moving nothing.
        unpaid = await _issued_invoice(client, headers, company_id, unit_price="500")
        applied = await _credit(client, headers, unpaid["id"])
        await client.post(
            f"/api/v1/invoicing/invoices/{applied['id']}/issue", json={}, headers=headers
        )

        summary = (await client.get("/api/v1/invoicing/summary", headers=headers)).json()
        assert float(summary["paid_this_year"]) == 363.0

        # Paid, then credited and refunded: the money goes back out and the tile follows.
        refunded_src = await _issued_invoice(client, headers, company_id, unit_price="200")
        await client.post(
            f"/api/v1/invoicing/invoices/{refunded_src['id']}/payments",
            json={"paid_on": _today().isoformat(), "amount": "242"},
            headers=headers,
        )
        note = await _credit(client, headers, refunded_src["id"])
        note = (
            await client.post(
                f"/api/v1/invoicing/invoices/{note['id']}/issue", json={}, headers=headers
            )
        ).json()
        await client.post(
            f"/api/v1/invoicing/invoices/{note['id']}/payments",
            json={"paid_on": _today().isoformat(), "amount": "-242"},
            headers=headers,
        )

        final = (await client.get("/api/v1/invoicing/summary", headers=headers)).json()
        assert float(final["paid_this_year"]) == 363.0, "242 in, 242 back out, nets to zero"


async def test_both_halves_of_a_correction_link_to_each_other(client_for) -> None:
    """The FK points one way; the detail read resolves the other, or the UI cannot link."""
    tenant: Tenant = await make_tenant("credit-links")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        company_id = await _company(client, headers)
        invoice = await _issued_invoice(client, headers, company_id)
        credit = await _credit(client, headers, invoice["id"])
        issued = (
            await client.post(
                f"/api/v1/invoicing/invoices/{credit['id']}/issue",
                json={},
                headers=headers,
            )
        ).json()

        assert issued["credit_for_id"] == invoice["id"]
        assert issued["credit_for_number"] == invoice["number"]

        source = await _get(client, headers, invoice["id"])
        assert [n["id"] for n in source["credit_notes"]] == [issued["id"]]
        assert source["credit_notes"][0]["applied_total"] == "363.00"
        assert source["credit_notes"][0]["number"] == issued["number"]


async def test_withdrawing_a_credit_note_puts_the_invoice_back_on_the_books(
    client_for,
) -> None:
    """Cancelling a credit note that only ever offset (no money moved) reverses cleanly."""
    tenant: Tenant = await make_tenant("credit-withdraw")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        company_id = await _company(client, headers)
        invoice = await _issued_invoice(client, headers, company_id)
        credit = await _credit(client, headers, invoice["id"])
        issued = (
            await client.post(
                f"/api/v1/invoicing/invoices/{credit['id']}/issue",
                json={},
                headers=headers,
            )
        ).json()
        assert issued["status"] == "paid"  # fully applied, nothing refundable

        # An invoice a credit note wrote down cannot be cancelled out from under it.
        blocked = await client.post(
            f"/api/v1/invoicing/invoices/{invoice['id']}/cancel", headers=headers
        )
        assert blocked.status_code == 409
        assert blocked.json()["error"]["message"] == "errors.invoicing.has_credit_notes"

        # The credit note itself withdraws, even though it rests at `paid`: no cent moved.
        cancelled = await client.post(
            f"/api/v1/invoicing/invoices/{issued['id']}/cancel", headers=headers
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "cancelled"
        assert cancelled.json()["applied_total"] == "0.00"

        source = await _get(client, headers, invoice["id"])
        assert source["credited_total"] == "0.00"
        assert source["outstanding"] == "363.00"
        assert source["credited"] is False
        assert source["credit_notes"] == []  # a withdrawn note is not a link


async def test_a_credit_note_is_not_itself_credited(client_for) -> None:
    tenant: Tenant = await make_tenant("credit-twice")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        company_id = await _company(client, headers)
        invoice = await _issued_invoice(client, headers, company_id)
        credit = await _credit(client, headers, invoice["id"])
        await client.post(
            f"/api/v1/invoicing/invoices/{credit['id']}/issue", json={}, headers=headers
        )

        resp = await client.post(
            f"/api/v1/invoicing/invoices/{credit['id']}/credit", headers=headers
        )
        assert resp.status_code == 409
        assert resp.json()["error"]["message"] == "errors.invoicing.already_credit_note"

        # And an invoice already written off in full refuses a second credit note, which
        # would otherwise refund money that was never received.
        again = await client.post(
            f"/api/v1/invoicing/invoices/{invoice['id']}/credit", headers=headers
        )
        assert again.status_code == 409
        assert again.json()["error"]["message"] == "errors.invoicing.already_credited"


async def test_a_paid_invoice_cannot_be_credited_twice(client_for) -> None:
    """The trap `credited_total` alone walks into.

    A paid invoice has no room, so an issued credit note against it absorbs nothing and
    `credited_total` stays at zero — which reads as "never credited" and would let the same
    invoice be credited again and again, each note owing the client another full refund. The
    documents already issued are the honest measure, not what they managed to absorb.
    """
    tenant: Tenant = await make_tenant("credit-paid-twice")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        company_id = await _company(client, headers)
        invoice = await _issued_invoice(client, headers, company_id)
        await client.post(
            f"/api/v1/invoicing/invoices/{invoice['id']}/payments",
            json={"paid_on": _today().isoformat(), "amount": "363"},
            headers=headers,
        )
        first = await _credit(client, headers, invoice["id"])
        issued = (
            await client.post(
                f"/api/v1/invoicing/invoices/{first['id']}/issue",
                json={},
                headers=headers,
            )
        ).json()
        assert issued["applied_total"] == "0.00"  # nothing to absorb…
        source = await _get(client, headers, invoice["id"])
        assert source["credited_total"] == "0.00"  # …so the counter says nothing

        second = await client.post(
            f"/api/v1/invoicing/invoices/{invoice['id']}/credit", headers=headers
        )
        assert second.status_code == 409
        assert second.json()["error"]["message"] == "errors.invoicing.already_credited"

        # A *partial* credit still leaves room for the rest.
        other = await _issued_invoice(client, headers, company_id, unit_price="1000")
        part = await _credit(client, headers, other["id"])
        await client.patch(
            f"/api/v1/invoicing/invoices/{part['id']}",
            json={"lines": [{"description": "W", "quantity": "1", "unit_price": "-400"}]},
            headers=headers,
        )
        await client.post(
            f"/api/v1/invoicing/invoices/{part['id']}/issue", json={}, headers=headers
        )
        more = await client.post(
            f"/api/v1/invoicing/invoices/{other['id']}/credit", headers=headers
        )
        assert more.status_code == 201, "partly credited is not fully credited"
