"""Withdrawing an issued document: cancel with a credit note, or delete it outright.

Two asks on one invoice. A definitive invoice the client has already received cannot be
"cancelled" in any sense the client's books or the ledger would notice — a cancel is a status
here and a document nowhere else — so the correction is a **credit note issued in the same
request** (`POST /credit` with `issue=true`): the invoice ends written off, the work it billed
is back on offer, and there is no draft left half-done between the two calls. And an issued
invoice may now be **deleted** behind `?force=true`, which is deliberately a second sentence
the caller has to say: the number leaves the sequence for good, so the refusals are the ones
`cancel` already makes plus the two a cancel leaves in place and a delete cannot — a booking
in the ledger, and an online checkout the client could still complete.
"""

from __future__ import annotations

import uuid

from app.db import async_session_maker, set_current_org
from app.modules.invoicing.models import ExternalRef, InvoicePaymentIntent
from tests.conftest import Tenant, auth_cookie, make_tenant
from tests.test_assignees import _add_member
from tests.test_invoicing_api import _company, _setup_org
from tests.test_invoicing_crediting import _get, _issued_invoice
from tests.test_invoicing_provenance import _billable_entries, _unbilled_ids


async def _trail(client, headers, invoice_id: str) -> list[str]:
    resp = await client.get(
        "/api/v1/activity",
        params={"entity_type": "invoice", "entity_id": invoice_id},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _hours_invoice(client, headers, company_id: str):
    """An issued invoice billing two approved entries — the work `cancel` hands back."""
    _, entry_ids = await _billable_entries(client, headers, company_id, (60, 30))
    invoice = (
        await client.post(
            "/api/v1/invoicing/invoices",
            json={
                "company_id": company_id,
                "lines": [
                    {
                        "description": "Uren",
                        "line_kind": "hours",
                        "quantity": "1.5",
                        "unit": "uur",
                        "unit_price": "100",
                        "time_entry_ids": entry_ids,
                    }
                ],
            },
            headers=headers,
        )
    ).json()
    resp = await client.post(
        f"/api/v1/invoicing/invoices/{invoice['id']}/issue", json={}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json(), entry_ids


async def test_crediting_with_issue_writes_the_invoice_off_in_one_request(client_for):
    """The "cancel a sent invoice" path: one call, and both documents come to rest."""
    tenant: Tenant = await make_tenant("withdraw-credit")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        company_id = await _company(client, headers)
        invoice, entry_ids = await _hours_invoice(client, headers, company_id)
        assert await _unbilled_ids(client, headers, company_id) == set()

        resp = await client.post(
            f"/api/v1/invoicing/invoices/{invoice['id']}/credit",
            json={"issue": True},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        credit = resp.json()
        # Definitive from the first moment it exists: numbered, applied, at rest.
        assert credit["kind"] == "credit_note"
        assert credit["status"] == "paid"
        assert credit["number"]
        assert credit["credit_for_id"] == invoice["id"]
        assert credit["applied_total"] == invoice["total"]
        assert credit["outstanding"] == "0.00"

        source = await _get(client, headers, invoice["id"])
        assert source["fully_credited"] is True
        assert source["outstanding"] == "0.00"
        # Not `cancelled`: it was a real document, and the credit note is what says so.
        assert source["status"] == "open"
        assert [n["id"] for n in source["credit_notes"]] == [credit["id"]]
        # And the hours are billable again — the point of correcting.
        assert await _unbilled_ids(client, headers, company_id) == set(entry_ids)

        # A plain cancel is refused now: the credit note vouches for the write-off.
        blocked = await client.post(
            f"/api/v1/invoicing/invoices/{invoice['id']}/cancel", headers=headers
        )
        assert blocked.status_code == 409
        assert blocked.json()["error"]["message"] == "errors.invoicing.has_credit_notes"


async def test_crediting_without_the_flag_still_leaves_a_draft(client_for):
    """The partial-credit path is untouched: no body, or `issue=false`, is a draft."""
    tenant: Tenant = await make_tenant("withdraw-credit-draft")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        company_id = await _company(client, headers)
        invoice = await _issued_invoice(client, headers, company_id)
        for body in (None, {"issue": False}):
            resp = await client.post(
                f"/api/v1/invoicing/invoices/{invoice['id']}/credit",
                json=body,
                headers=headers,
            )
            assert resp.status_code == 201, resp.text
            assert resp.json()["status"] == "draft"
            assert resp.json()["number"] is None
            # A draft allocates nothing, so the second draft is not "already credited".
            assert (await _get(client, headers, invoice["id"]))["credited_total"] == "0.00"


async def test_an_issued_invoice_deletes_only_when_forced(client_for):
    tenant: Tenant = await make_tenant("withdraw-delete")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        company_id = await _company(client, headers)
        invoice, entry_ids = await _hours_invoice(client, headers, company_id)

        # The default answer is the old one: a numbered document does not delete.
        refused = await client.delete(
            f"/api/v1/invoicing/invoices/{invoice['id']}", headers=headers
        )
        assert refused.status_code == 409
        assert refused.json()["error"]["message"] == "errors.invoicing.not_draft"

        deleted = await client.delete(
            f"/api/v1/invoicing/invoices/{invoice['id']}",
            params={"force": "true"},
            headers=headers,
        )
        assert deleted.status_code == 204, deleted.text
        gone = await client.get(f"/api/v1/invoicing/invoices/{invoice['id']}", headers=headers)
        assert gone.status_code == 404
        # The work it billed is on offer again, exactly as after a cancel.
        assert await _unbilled_ids(client, headers, company_id) == set(entry_ids)
        # The trail outlives the record and is the one place the number survives.
        trail = await _trail(client, headers, invoice["id"])
        deleted_line = next(item for item in trail if item["action"] == "deleted")
        assert deleted_line["payload"]["number"] == invoice["number"]
        assert deleted_line["payload"]["status"] == "open"


async def test_a_cancelled_invoice_deletes_when_forced_too(client_for):
    """A voided number is withdrawable: it bills nothing, so nothing is stranded."""
    tenant: Tenant = await make_tenant("withdraw-delete-cancelled")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        company_id = await _company(client, headers)
        invoice = await _issued_invoice(client, headers, company_id)
        cancelled = await client.post(
            f"/api/v1/invoicing/invoices/{invoice['id']}/cancel", headers=headers
        )
        assert cancelled.status_code == 200, cancelled.text
        # Cancelling twice is not a thing.
        again = await client.post(
            f"/api/v1/invoicing/invoices/{invoice['id']}/cancel", headers=headers
        )
        assert again.status_code == 409
        deleted = await client.delete(
            f"/api/v1/invoicing/invoices/{invoice['id']}",
            params={"force": "true"},
            headers=headers,
        )
        assert deleted.status_code == 204, deleted.text


async def test_a_forced_delete_refuses_what_the_record_vouches_for(client_for):
    """Registered money, a credit note's allocation, a ledger booking, an open checkout."""
    tenant: Tenant = await make_tenant("withdraw-delete-refuse")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        company_id = await _company(client, headers)

        async def forced_delete(invoice_id: str):
            return await client.delete(
                f"/api/v1/invoicing/invoices/{invoice_id}",
                params={"force": "true"},
                headers=headers,
            )

        # 1. A payment on it.
        paid = await _issued_invoice(client, headers, company_id)
        resp = await client.post(
            f"/api/v1/invoicing/invoices/{paid['id']}/payments",
            json={"paid_on": "2026-01-10", "amount": "100.00", "method": "bank"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        refused = await forced_delete(paid["id"])
        assert refused.status_code == 409
        assert refused.json()["error"]["message"] == "errors.invoicing.has_payments"

        # 2. A credit note written against it — even one that absorbed nothing, because the
        #    source was paid in full first, is a document that points at this number.
        settled = await _issued_invoice(client, headers, company_id)
        await client.post(
            f"/api/v1/invoicing/invoices/{settled['id']}/payments",
            json={"paid_on": "2026-01-10", "amount": "363.00", "method": "bank"},
            headers=headers,
        )
        resp = await client.post(
            f"/api/v1/invoicing/invoices/{settled['id']}/credit",
            json={"issue": True},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        assert (await _get(client, headers, settled["id"]))["credited_total"] == "0.00"
        # Withdrawing the payment first exposes the credit-note guard rather than the money one.
        payment_id = (await _get(client, headers, settled["id"]))["payments"][0]["id"]
        await client.delete(
            f"/api/v1/invoicing/invoices/{settled['id']}/payments/{payment_id}",
            headers=headers,
        )
        refused = await forced_delete(settled["id"])
        assert refused.status_code == 409
        assert refused.json()["error"]["message"] == "errors.invoicing.has_credit_notes"

        # 3. Booked in the ledger.
        booked = await _issued_invoice(client, headers, company_id)
        async with async_session_maker() as session:
            await set_current_org(session, tenant.org.id)
            session.add(
                ExternalRef(
                    org_id=tenant.org.id,
                    provider="snelstart",
                    local_type="invoice",
                    local_id=uuid.UUID(booked["id"]),
                    external_id="VK-2026-1",
                )
            )
            await session.commit()
        refused = await forced_delete(booked["id"])
        assert refused.status_code == 409
        assert refused.json()["error"]["message"] == "errors.invoicing.in_ledger"
        # A cancel still works there: the number stays, the ledger's twin stays explainable.
        cancelled = await client.post(
            f"/api/v1/invoicing/invoices/{booked['id']}/cancel", headers=headers
        )
        assert cancelled.status_code == 200, cancelled.text

        # 4. A checkout the client could still complete.
        checkout = await _issued_invoice(client, headers, company_id)
        async with async_session_maker() as session:
            await set_current_org(session, tenant.org.id)
            session.add(
                InvoicePaymentIntent(
                    org_id=tenant.org.id,
                    invoice_id=uuid.UUID(checkout["id"]),
                    provider="mollie",
                    account_id=uuid.uuid4(),
                    external_id="tr_open",
                    status="open",
                    amount=checkout["total"],
                    currency="EUR",
                )
            )
            await session.commit()
        refused = await forced_delete(checkout["id"])
        assert refused.status_code == 409
        assert refused.json()["error"]["message"] == "errors.invoicing.open_checkout"


async def test_deleting_an_issued_credit_note_hands_its_allocation_back(client_for):
    """A forced delete of a credit note is a withdrawal: the invoice it corrected is back on
    the books — under the same refusal `cancel` makes when the note released work."""
    tenant: Tenant = await make_tenant("withdraw-delete-credit")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        company_id = await _company(client, headers)

        # Plain product lines: the credit note releases nothing, so it may go.
        invoice = await _issued_invoice(client, headers, company_id)
        credit = (
            await client.post(
                f"/api/v1/invoicing/invoices/{invoice['id']}/credit",
                json={"issue": True},
                headers=headers,
            )
        ).json()
        assert (await _get(client, headers, invoice["id"]))["outstanding"] == "0.00"
        deleted = await client.delete(
            f"/api/v1/invoicing/invoices/{credit['id']}",
            params={"force": "true"},
            headers=headers,
        )
        assert deleted.status_code == 204, deleted.text
        source = await _get(client, headers, invoice["id"])
        assert source["credited_total"] == "0.00"
        assert source["outstanding"] == "363.00"
        assert source["credit_notes"] == []

        # Hours on it: the credit note handed the work back, so it can no longer be withdrawn.
        hours, _ = await _hours_invoice(client, headers, company_id)
        credit = (
            await client.post(
                f"/api/v1/invoicing/invoices/{hours['id']}/credit",
                json={"issue": True},
                headers=headers,
            )
        ).json()
        refused = await client.delete(
            f"/api/v1/invoicing/invoices/{credit['id']}",
            params={"force": "true"},
            headers=headers,
        )
        assert refused.status_code == 409
        assert refused.json()["error"]["message"] == "errors.invoicing.credit_released_work"


async def test_forced_delete_needs_the_delete_permission(client_for):
    """`force` widens what may go, never who may make it go."""
    tenant: Tenant = await make_tenant("withdraw-delete-perm")
    headers = await auth_cookie(tenant.user)
    member = await _add_member(tenant.org.id, "member@withdraw.example.com")
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        company_id = await _company(client, headers)
        invoice = await _issued_invoice(client, headers, company_id)
        member_headers = await auth_cookie(member)
        refused = await client.delete(
            f"/api/v1/invoicing/invoices/{invoice['id']}",
            params={"force": "true"},
            headers=member_headers,
        )
        assert refused.status_code == 403
