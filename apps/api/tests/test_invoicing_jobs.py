"""The invoicing seams that run without a request (issue #207): the ``subscription.due``
consumer, the daily reminders/expiry cron, and the UBL export."""

from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.email.senders import Sender
from app.core.events import SystemContext
from app.core.models import Org
from app.db import async_session_maker, set_current_org
from app.modules.invoicing.events import on_subscription_due
from app.modules.invoicing.jobs import invoicing_daily
from tests.conftest import Tenant, auth_cookie, make_tenant

AMS = ZoneInfo("Europe/Amsterdam")
CBC = "{urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2}"
CAC = "{urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2}"


def _today():
    return datetime.now(AMS).date()


async def _setup_org(client, headers) -> None:
    resp = await client.put(
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
    assert resp.status_code == 200, resp.text
    # Seeds the NL rates (21% default among them) — the lazy-seed read.
    assert (await client.get("/api/v1/invoicing/tax-rates", headers=headers)).status_code == 200


async def _company(client, headers) -> str:
    resp = await client.post(
        "/api/v1/companies",
        json={"name": "Klant BV", "invoice_email": "boekhouding@klant.nl"},
        headers=headers,
    )
    return resp.json()["id"]


async def test_subscription_due_drafts_one_invoice_idempotently(client_for) -> None:
    tenant: Tenant = await make_tenant("inv-subdue")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        company_id = await _company(client, headers)

    subscription_id = uuid.uuid4()
    payload = {
        "subscription_id": subscription_id,
        "company_id": company_id,
        "name": "Hosting Plus",
        "amount": "250.00",
        "currency": "EUR",
        "period_start": (_today() - timedelta(days=31)).isoformat(),
        "period_end": _today().isoformat(),
        "lines": [
            {"description": "Hosting Plus", "quantity": "1", "unit_amount": "250.00"}
        ],
    }
    # The handler runs on the cron's SystemContext — same shape the subscriptions job emits.
    async with async_session_maker() as session:
        org = await session.get(Org, tenant.org.id)
        await set_current_org(session, org.id)
        ctx = SystemContext(org=org, session=session)
        await on_subscription_due(ctx, dict(payload))
        await on_subscription_due(ctx, dict(payload))  # a double emit must not double-bill
        await session.commit()

    async with client_for(tenant.host) as client:
        page = (await client.get("/api/v1/invoicing/invoices", headers=headers)).json()
        assert page["total"] == 1
        invoice = page["items"][0]
        assert invoice["status"] == "draft"
        assert invoice["subscription_id"] == str(subscription_id)
        assert invoice["period_end"] == _today().isoformat()
        # 250 + the seeded default 21% — the org's own tax, not the event's business.
        assert invoice["total"] == "302.50"
        assert invoice["reference"] == "Hosting Plus"


async def test_reminder_cron_walks_the_schedule_and_stops(client_for, monkeypatch) -> None:
    tenant: Tenant = await make_tenant("inv-remind")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        await client.put(
            "/api/v1/invoicing/settings",
            json={"reminders_enabled": True, "reminder_days": [7, 14]},
            headers=headers,
        )
        company_id = await _company(client, headers)

        async def make_invoice(paused: bool) -> str:
            invoice = (
                await client.post(
                    "/api/v1/invoicing/invoices",
                    json={
                        "company_id": company_id,
                        "lines": [
                            {"description": "W", "quantity": "1", "unit_price": "100"}
                        ],
                    },
                    headers=headers,
                )
            ).json()
            await client.post(
                f"/api/v1/invoicing/invoices/{invoice['id']}/issue",
                json={"due_date": (_today() - timedelta(days=15)).isoformat()},
                headers=headers,
            )
            if paused:
                await client.patch(
                    f"/api/v1/invoicing/invoices/{invoice['id']}",
                    json={"reminders_paused": True},
                    headers=headers,
                )
            return invoice["id"]

        overdue_id = await make_invoice(paused=False)
        paused_id = await make_invoice(paused=True)

    sent: list = []

    async def fake_send(provider, config, sender, message):  # noqa: ANN001
        sent.append(message)
        return True, None

    async def fake_transport(session, org_id):  # noqa: ANN001
        return ("smtp", {}, Sender(from_email="mail@agency.nl", from_name="Agency"))

    monkeypatch.setattr("app.modules.invoicing.jobs.send_email", fake_send)
    monkeypatch.setattr("app.modules.invoicing.jobs.load_transport", fake_transport)

    # 15 days past due, schedule [7, 14]: run 1 sends step 1, run 2 sends step 2,
    # run 3 has nothing left — the schedule bounds how often a client can be mailed.
    await invoicing_daily({})
    assert len(sent) == 1
    assert sent[0].to == "boekhouding@klant.nl"
    await invoicing_daily({})
    assert len(sent) == 2
    await invoicing_daily({})
    assert len(sent) == 2

    async with client_for(tenant.host) as client:
        invoice = (
            await client.get(
                f"/api/v1/invoicing/invoices/{overdue_id}", headers=headers
            )
        ).json()
        assert invoice["reminder_count"] == 2
        assert invoice["last_reminder_at"] is not None
        muted = (
            await client.get(f"/api/v1/invoicing/invoices/{paused_id}", headers=headers)
        ).json()
        assert muted["reminder_count"] == 0


async def test_cron_expires_open_quotes(client_for) -> None:
    tenant: Tenant = await make_tenant("inv-expire")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        company_id = await _company(client, headers)
        quote = (
            await client.post(
                "/api/v1/invoicing/quotes",
                json={
                    "company_id": company_id,
                    "lines": [{"description": "W", "quantity": "1", "unit_price": "10"}],
                },
                headers=headers,
            )
        ).json()
        await client.post(
            f"/api/v1/invoicing/quotes/{quote['id']}/issue",
            json={"due_date": (_today() - timedelta(days=1)).isoformat()},
            headers=headers,
        )

    await invoicing_daily({})

    async with client_for(tenant.host) as client:
        expired = (
            await client.get(f"/api/v1/invoicing/quotes/{quote['id']}", headers=headers)
        ).json()
        assert expired["status"] == "expired"
        # A deal accepted after the deadline is still the owner's call.
        late = await client.post(
            f"/api/v1/invoicing/quotes/{quote['id']}/accept", json={}, headers=headers
        )
        assert late.status_code == 200
        assert late.json()["status"] == "accepted"


async def test_ubl_export_reconciles_and_codes_reverse_charge(client_for) -> None:
    tenant: Tenant = await make_tenant("inv-ubl")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        company_id = await _company(client, headers)
        rates = (await client.get("/api/v1/invoicing/tax-rates", headers=headers)).json()
        reverse = next(r for r in rates if r["category"] == "reverse_charge")

        invoice = (
            await client.post(
                "/api/v1/invoicing/invoices",
                json={
                    "company_id": company_id,
                    "lines": [
                        {"description": "Ontwikkeling", "quantity": "10", "unit_price": "85"},
                        {
                            "description": "EU dienst",
                            "quantity": "1",
                            "unit_price": "500",
                            "tax_rate_id": reverse["id"],
                        },
                    ],
                },
                headers=headers,
            )
        ).json()

        # Drafts have no number and must not export.
        draft = await client.get(
            f"/api/v1/invoicing/invoices/{invoice['id']}/ubl", headers=headers
        )
        assert draft.status_code == 409

        issued = (
            await client.post(
                f"/api/v1/invoicing/invoices/{invoice['id']}/issue",
                json={},
                headers=headers,
            )
        ).json()
        resp = await client.get(
            f"/api/v1/invoicing/invoices/{invoice['id']}/ubl", headers=headers
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/xml")

        root = ET.fromstring(resp.content)
        assert root.findtext(f"{CBC}ID") == issued["number"]
        assert root.findtext(f"{CBC}DocumentCurrencyCode") == "EUR"
        supplier = root.find(f"{CAC}AccountingSupplierParty/{CAC}Party")
        assert supplier.findtext(f"{CAC}PartyName/{CBC}Name") == "Agency BV"
        customer = root.find(f"{CAC}AccountingCustomerParty/{CAC}Party")
        assert customer.findtext(f"{CAC}PartyName/{CBC}Name") == "Klant BV"

        # Tax: 21% over 850 = 178.50; the reverse-charge group charges 0, coded AE with a
        # mandatory exemption reason.
        tax_total = root.find(f"{CAC}TaxTotal")
        assert tax_total.findtext(f"{CBC}TaxAmount") == "178.50"
        subtotals = tax_total.findall(f"{CAC}TaxSubtotal")
        assert len(subtotals) == 2
        by_code = {
            s.findtext(f"{CAC}TaxCategory/{CBC}ID"): s for s in subtotals
        }
        assert by_code["S"].findtext(f"{CBC}TaxableAmount") == "850.00"
        assert by_code["AE"].findtext(f"{CBC}TaxAmount") == "0.00"
        assert by_code["AE"].findtext(f"{CAC}TaxCategory/{CBC}TaxExemptionReason")

        # The money reconciles: line extensions sum to the tax-exclusive amount.
        monetary = root.find(f"{CAC}LegalMonetaryTotal")
        assert monetary.findtext(f"{CBC}LineExtensionAmount") == "1350.00"
        assert monetary.findtext(f"{CBC}TaxExclusiveAmount") == "1350.00"
        assert monetary.findtext(f"{CBC}TaxInclusiveAmount") == "1528.50"
        assert monetary.findtext(f"{CBC}PayableAmount") == "1528.50"
        line_nets = [
            line.findtext(f"{CBC}LineExtensionAmount")
            for line in root.findall(f"{CAC}InvoiceLine")
        ]
        assert line_nets == ["850.00", "500.00"]
