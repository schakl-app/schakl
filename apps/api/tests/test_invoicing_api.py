"""HTTP-level tests for the invoicing module (issue #207).

Covers the document lifecycle (draft → issue → pay / cancel / credit), the quote suite and
its conversion, numbering, the post-issue money lock, the time bridge, and — like every
module — tenant isolation across each new table.
"""

from __future__ import annotations

import uuid as uuid_mod
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from tests.conftest import Tenant, auth_cookie, make_tenant

AMS = ZoneInfo("Europe/Amsterdam")


def _today():
    return datetime.now(AMS).date()


async def _setup_org(client, headers) -> None:
    """Seller details + seeded tax rates: what a real org does once in Instellingen."""
    resp = await client.put(
        "/api/v1/invoicing/settings",
        json={
            "company_details": {
                "name": "Agency BV",
                "address_line1": "Kerkstraat 1",
                "postal_code": "1234 AB",
                "city": "Amsterdam",
                "country": "NL",
                "vat_number": "NL123456789B01",
                "coc_number": "12345678",
                "iban": "NL02ABNA0123456789",
                "email": "administratie@agency.nl",
            }
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    rates = (await client.get("/api/v1/invoicing/tax-rates", headers=headers)).json()
    assert any(r["rate"] == "21.00" or float(r["rate"]) == 21.0 for r in rates)


async def _company(client, headers, name: str = "Klant BV") -> str:
    resp = await client.post(
        "/api/v1/companies",
        json={"name": name, "invoice_email": "boekhouding@klant.nl"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_invoice_lifecycle_totals_numbering_and_lock(client_for) -> None:
    tenant: Tenant = await make_tenant("inv-life")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        company_id = await _company(client, headers)

        created = await client.post(
            "/api/v1/invoicing/invoices",
            json={
                "company_id": company_id,
                "lines": [
                    {"description": "Ontwikkeling", "quantity": "10", "unit_price": "85"},
                    {"description": "Hosting", "quantity": "1", "unit_price": "50"},
                ],
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        invoice = created.json()
        # The org default (21% hoog, seeded) applied; totals are the server's, per group.
        assert invoice["status"] == "draft"
        assert invoice["number"] is None
        assert invoice["subtotal"] == "900.00"
        assert invoice["tax_total"] == "189.00"
        assert invoice["total"] == "1089.00"
        assert invoice["customer"]["name"] == "Klant BV"
        assert invoice["customer"]["email"] == "boekhouding@klant.nl"
        assert invoice["lines"][0]["tax_rate_pct"] == "21.00"
        invoice_id = invoice["id"]

        # Draft edits recompute totals server-side; sent totals are ignored by design
        # (there is simply no field for them).
        updated = await client.patch(
            f"/api/v1/invoicing/invoices/{invoice_id}",
            json={"lines": [{"description": "Alles", "quantity": "1", "unit_price": "100"}]},
            headers=headers,
        )
        assert updated.status_code == 200
        assert updated.json()["total"] == "121.00"

        issued = await client.post(
            f"/api/v1/invoicing/invoices/{invoice_id}/issue", json={}, headers=headers
        )
        assert issued.status_code == 200, issued.text
        body = issued.json()
        year = _today().year
        assert body["number"] == f"{year}-0001"
        assert body["status"] == "open"
        assert body["issue_date"] == _today().isoformat()
        assert body["due_date"] == (_today() + timedelta(days=14)).isoformat()

        # Money is locked after issue; process fields stay editable.
        locked = await client.patch(
            f"/api/v1/invoicing/invoices/{invoice_id}",
            json={"lines": [{"description": "X", "quantity": "1", "unit_price": "1"}]},
            headers=headers,
        )
        assert locked.status_code == 409
        assert locked.json()["error"]["message"] == "errors.invoicing.locked"
        still_ok = await client.patch(
            f"/api/v1/invoicing/invoices/{invoice_id}",
            json={"reference": "PO-777", "reminders_paused": True},
            headers=headers,
        )
        assert still_ok.status_code == 200

        # The sequence marches on, race-safe under the settings row lock.
        second = await client.post(
            "/api/v1/invoicing/invoices",
            json={
                "company_id": company_id,
                "lines": [{"description": "Werk", "quantity": "1", "unit_price": "10"}],
            },
            headers=headers,
        )
        second_issued = await client.post(
            f"/api/v1/invoicing/invoices/{second.json()['id']}/issue",
            json={},
            headers=headers,
        )
        assert second_issued.json()["number"] == f"{year}-0002"

        # Issued documents don't delete — they cancel (the draft does delete).
        no_delete = await client.delete(
            f"/api/v1/invoicing/invoices/{invoice_id}", headers=headers
        )
        assert no_delete.status_code == 409
        cancelled = await client.post(
            f"/api/v1/invoicing/invoices/{second.json()['id']}/cancel",
            headers=headers,
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"


async def test_payments_flip_status_both_ways(client_for) -> None:
    tenant: Tenant = await make_tenant("inv-pay")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        company_id = await _company(client, headers)
        invoice = (
            await client.post(
                "/api/v1/invoicing/invoices",
                json={
                    "company_id": company_id,
                    "lines": [{"description": "W", "quantity": "1", "unit_price": "1000"}],
                },
                headers=headers,
            )
        ).json()
        await client.post(
            f"/api/v1/invoicing/invoices/{invoice['id']}/issue", json={}, headers=headers
        )

        partial = await client.post(
            f"/api/v1/invoicing/invoices/{invoice['id']}/payments",
            json={"paid_on": _today().isoformat(), "amount": "500"},
            headers=headers,
        )
        assert partial.status_code == 200
        assert partial.json()["status"] == "open"
        assert partial.json()["outstanding"] == "710.00"  # 1210 total − 500

        rest = await client.post(
            f"/api/v1/invoicing/invoices/{invoice['id']}/payments",
            json={"paid_on": _today().isoformat(), "amount": "710"},
            headers=headers,
        )
        assert rest.json()["status"] == "paid"
        assert rest.json()["paid_at"] is not None

        # An open invoice with payments refuses to cancel; deleting a payment reopens it.
        payment_id = rest.json()["payments"][0]["id"]
        reopened = await client.delete(
            f"/api/v1/invoicing/invoices/{invoice['id']}/payments/{payment_id}",
            headers=headers,
        )
        assert reopened.json()["status"] == "open"
        blocked = await client.post(
            f"/api/v1/invoicing/invoices/{invoice['id']}/cancel", headers=headers
        )
        assert blocked.status_code == 409
        assert blocked.json()["error"]["message"] == "errors.invoicing.has_payments"


async def test_credit_note_negates_the_source(client_for) -> None:
    tenant: Tenant = await make_tenant("inv-credit")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        company_id = await _company(client, headers)
        invoice = (
            await client.post(
                "/api/v1/invoicing/invoices",
                json={
                    "company_id": company_id,
                    "lines": [{"description": "W", "quantity": "2", "unit_price": "150"}],
                },
                headers=headers,
            )
        ).json()
        await client.post(
            f"/api/v1/invoicing/invoices/{invoice['id']}/issue", json={}, headers=headers
        )
        credit = await client.post(
            f"/api/v1/invoicing/invoices/{invoice['id']}/credit", headers=headers
        )
        assert credit.status_code == 201
        body = credit.json()
        assert body["kind"] == "credit_note"
        assert body["status"] == "draft"
        assert body["credit_for_id"] == invoice["id"]
        assert body["total"] == "-363.00"
        assert body["reference"] == f"{_today().year}-0001"


async def test_quote_suite_and_conversion(client_for) -> None:
    tenant: Tenant = await make_tenant("inv-quote")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        company_id = await _company(client, headers)

        quote = (
            await client.post(
                "/api/v1/invoicing/quotes",
                json={
                    "company_id": company_id,
                    "lines": [
                        {"description": "Website", "quantity": "1", "unit_price": "4500"}
                    ],
                },
                headers=headers,
            )
        ).json()
        assert quote["status"] == "draft"
        assert quote["total"] == "5445.00"

        issued = await client.post(
            f"/api/v1/invoicing/quotes/{quote['id']}/issue", json={}, headers=headers
        )
        assert issued.status_code == 200
        assert issued.json()["number"] == f"Q{_today().year}-0001"
        assert issued.json()["valid_until"] == (_today() + timedelta(days=30)).isoformat()

        # Money locked while open; accept with the customer's words on record.
        locked = await client.patch(
            f"/api/v1/invoicing/quotes/{quote['id']}",
            json={"lines": [{"description": "X", "quantity": "1", "unit_price": "1"}]},
            headers=headers,
        )
        assert locked.status_code == 409
        accepted = await client.post(
            f"/api/v1/invoicing/quotes/{quote['id']}/accept",
            json={"note": "Akkoord per mail 12-07"},
            headers=headers,
        )
        assert accepted.json()["status"] == "accepted"
        assert accepted.json()["decision_note"] == "Akkoord per mail 12-07"

        converted = await client.post(
            f"/api/v1/invoicing/quotes/{quote['id']}/convert", headers=headers
        )
        assert converted.status_code == 201
        invoice = converted.json()
        assert invoice["status"] == "draft"
        assert invoice["total"] == "5445.00"
        assert invoice["quote_id"] == quote["id"]
        after = (
            await client.get(f"/api/v1/invoicing/quotes/{quote['id']}", headers=headers)
        ).json()
        assert after["status"] == "invoiced"
        assert after["invoice_id"] == invoice["id"]

        # A second convert would double-bill the deal — refused.
        again = await client.post(
            f"/api/v1/invoicing/quotes/{quote['id']}/convert", headers=headers
        )
        assert again.status_code == 409

        # Deleting the draft invoice puts the accepted deal back on the table.
        await client.delete(f"/api/v1/invoicing/invoices/{invoice['id']}", headers=headers)
        reverted = (
            await client.get(f"/api/v1/invoicing/quotes/{quote['id']}", headers=headers)
        ).json()
        assert reverted["status"] == "accepted"
        assert reverted["invoice_id"] is None


async def test_invoice_from_unbilled_time_and_release(client_for) -> None:
    tenant: Tenant = await make_tenant("inv-time")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        company_id = await _company(client, headers)
        # Rates live on the employee, never the project (#226): the owner bills at €90/h.
        await client.put(
            f"/api/v1/leave/rate/{tenant.user.id}",
            json={"hourly_rate": "90.00"},
            headers=headers,
        )
        project = (
            await client.post(
                "/api/v1/projects",
                json={"name": "Retainer", "company_id": company_id},
                headers=headers,
            )
        ).json()

        start = datetime.now(UTC) - timedelta(days=2)
        entry_ids = []
        for minutes in (90, 30):
            entry = await client.post(
                "/api/v1/time/entries",
                json={
                    "company_id": company_id,
                    "project_id": project["id"],
                    "description": "Werkzaamheden",
                    "started_at": start.isoformat(),
                    "minutes": minutes,
                    "billable": True,
                },
                headers=headers,
            )
            assert entry.status_code == 201, entry.text
            entry_ids.append(entry.json()["id"])
        approved = await client.post(
            "/api/v1/time/entries/approve",
            json={"entry_ids": entry_ids, "approved": True},
            headers=headers,
        )
        assert approved.status_code == 200

        unbilled = (
            await client.get(
                f"/api/v1/invoicing/unbilled?company_id={company_id}", headers=headers
            )
        ).json()
        assert unbilled["total_minutes"] == 120
        assert len(unbilled["entries"]) == 2

        built = await client.post(
            "/api/v1/invoicing/invoices/from-time",
            json={"company_id": company_id, "group_by": "project"},
            headers=headers,
        )
        assert built.status_code == 201, built.text
        invoice = built.json()
        assert invoice["status"] == "draft"
        assert len(invoice["lines"]) == 1
        # 120 minutes at the logger's €90: 2.00 h × 90 = 180 net.
        assert invoice["lines"][0]["quantity"] == "2.00"
        assert invoice["lines"][0]["unit_price"] == "90.00"
        assert invoice["subtotal"] == "180.00"

        # The entries are stamped: nothing unbilled remains, twice cannot double-bill.
        empty = (
            await client.get(
                f"/api/v1/invoicing/unbilled?company_id={company_id}", headers=headers
            )
        ).json()
        assert empty["total_minutes"] == 0
        refused = await client.post(
            "/api/v1/invoicing/invoices/from-time",
            json={"company_id": company_id, "group_by": "project"},
            headers=headers,
        )
        assert refused.status_code == 400
        assert refused.json()["error"]["message"] == "errors.invoicing.no_unbilled"

        # Deleting the draft un-bills exactly those entries.
        await client.delete(f"/api/v1/invoicing/invoices/{invoice['id']}", headers=headers)
        released = (
            await client.get(
                f"/api/v1/invoicing/unbilled?company_id={company_id}", headers=headers
            )
        ).json()
        assert released["total_minutes"] == 120


async def test_invoice_from_time_prices_each_logger_at_their_own_rate(client_for) -> None:
    """#226: two people on one project bill at two rates — the grouped build splits the
    project into one line per rate instead of pricing everyone at the first entry's."""
    from tests.test_task_subresources import add_member

    tenant: Tenant = await make_tenant("inv-time-rates")
    headers = await auth_cookie(tenant.user)
    member = await add_member(tenant)
    member_headers = await auth_cookie(member)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        company_id = await _company(client, headers)
        # Owner bills at a personal €90/h; the member has none and falls back to the
        # leave org default of €60/h (#113) — never the invoicing default of €10.
        await client.put(
            f"/api/v1/leave/rate/{tenant.user.id}",
            json={"hourly_rate": "90.00"},
            headers=headers,
        )
        await client.put(
            "/api/v1/leave/settings",
            json={"default_hourly_rate": "60.00"},
            headers=headers,
        )
        await client.put(
            "/api/v1/invoicing/settings",
            json={"default_hourly_rate": "10.00"},
            headers=headers,
        )
        project = (
            await client.post(
                "/api/v1/projects",
                json={"name": "Retainer", "company_id": company_id},
                headers=headers,
            )
        ).json()

        start = datetime.now(UTC) - timedelta(days=1)
        entry_ids = []
        for who, minutes in ((headers, 60), (member_headers, 90)):
            entry = await client.post(
                "/api/v1/time/entries",
                json={
                    "company_id": company_id,
                    "project_id": project["id"],
                    "started_at": start.isoformat(),
                    "minutes": minutes,
                    "billable": True,
                },
                headers=who,
            )
            assert entry.status_code == 201, entry.text
            entry_ids.append(entry.json()["id"])
        await client.post(
            "/api/v1/time/entries/approve",
            json={"entry_ids": entry_ids, "approved": True},
            headers=headers,
        )

        # The preview resolves each entry to its logger's rate.
        unbilled = (
            await client.get(
                f"/api/v1/invoicing/unbilled?company_id={company_id}", headers=headers
            )
        ).json()
        assert sorted(e["rate"] for e in unbilled["entries"]) == ["60.00", "90.00"]

        built = await client.post(
            "/api/v1/invoicing/invoices/from-time",
            json={"company_id": company_id, "group_by": "project"},
            headers=headers,
        )
        assert built.status_code == 201, built.text
        lines = built.json()["lines"]
        assert len(lines) == 2
        priced = sorted((line["unit_price"], line["quantity"]) for line in lines)
        # 90 min at the member's €60 and 60 min at the owner's €90.
        assert priced == [("60.00", "1.50"), ("90.00", "1.00")]
        assert built.json()["subtotal"] == "180.00"


async def test_invoice_create_links_time_entries_by_id(client_for) -> None:
    """The new-invoice form auto-adds unbilled entries as lines carrying ``time_entry_id``:
    creating the invoice bills exactly those entries, and an invalid/foreign/already-billed
    id is silently skipped (never a 500 on the unique constraint)."""
    tenant: Tenant = await make_tenant("inv-line-time")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        company_id = await _company(client, headers)
        # No personal rate here: the leave org default (#113) is the effective rate (#226).
        await client.put(
            "/api/v1/leave/settings",
            json={"default_hourly_rate": "80.00"},
            headers=headers,
        )
        project = (
            await client.post(
                "/api/v1/projects",
                json={"name": "Retainer", "company_id": company_id},
                headers=headers,
            )
        ).json()

        start = datetime.now(UTC) - timedelta(days=1)
        entry_ids = []
        for minutes in (60, 30):
            entry = await client.post(
                "/api/v1/time/entries",
                json={
                    "company_id": company_id,
                    "project_id": project["id"],
                    "description": "Ontwerp",
                    "started_at": start.isoformat(),
                    "minutes": minutes,
                    "billable": True,
                },
                headers=headers,
            )
            assert entry.status_code == 201, entry.text
            entry_ids.append(entry.json()["id"])
        await client.post(
            "/api/v1/time/entries/approve",
            json={"entry_ids": entry_ids, "approved": True},
            headers=headers,
        )

        # The unbilled preview carries a resolved per-entry rate (leave org default €80).
        unbilled = (
            await client.get(
                f"/api/v1/invoicing/unbilled?company_id={company_id}", headers=headers
            )
        ).json()
        assert len(unbilled["entries"]) == 2
        assert all(e["rate"] == "80.00" for e in unbilled["entries"])

        # Create with two valid time-entry lines plus a foreign id and a plain line: all
        # lines land, but only the two real entries get billed; the bogus id is skipped.
        created = await client.post(
            "/api/v1/invoicing/invoices",
            json={
                "company_id": company_id,
                "lines": [
                    {"description": "Ontwerp 1", "quantity": "1", "unit_price": "80",
                     "time_entry_id": entry_ids[0]},
                    {"description": "Ontwerp 2", "quantity": "0.5", "unit_price": "80",
                     "time_entry_id": entry_ids[1]},
                    {"description": "Los", "quantity": "1", "unit_price": "10",
                     "time_entry_id": str(uuid_mod.uuid4())},
                    {"description": "Nog los", "quantity": "1", "unit_price": "5"},
                ],
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        invoice = created.json()
        assert len(invoice["lines"]) == 4

        # Both real entries are stamped; nothing unbilled remains, and a re-bill is a no-op.
        after = (
            await client.get(
                f"/api/v1/invoicing/unbilled?company_id={company_id}", headers=headers
            )
        ).json()
        assert after["total_minutes"] == 0

        # An already-billed id on a second invoice is silently skipped (no 500), and the
        # entry stays bound to the first invoice.
        again = await client.post(
            "/api/v1/invoicing/invoices",
            json={
                "company_id": company_id,
                "lines": [
                    {"description": "Dubbel", "quantity": "1", "unit_price": "80",
                     "time_entry_id": entry_ids[0]},
                ],
            },
            headers=headers,
        )
        assert again.status_code == 201, again.text

        # Deleting the first draft releases exactly its two entries.
        await client.delete(f"/api/v1/invoicing/invoices/{invoice['id']}", headers=headers)
        released = (
            await client.get(
                f"/api/v1/invoicing/unbilled?company_id={company_id}", headers=headers
            )
        ).json()
        assert released["total_minutes"] == 90

        # The second (skip-only) invoice bound nothing, so deleting it releases nothing.
        await client.delete(f"/api/v1/invoicing/invoices/{again.json()['id']}", headers=headers)


async def test_summary_counts(client_for) -> None:
    tenant: Tenant = await make_tenant("inv-sum")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        company_id = await _company(client, headers)
        invoice = (
            await client.post(
                "/api/v1/invoicing/invoices",
                json={
                    "company_id": company_id,
                    "lines": [{"description": "W", "quantity": "1", "unit_price": "100"}],
                },
                headers=headers,
            )
        ).json()
        await client.post(
            f"/api/v1/invoicing/invoices/{invoice['id']}/issue",
            json={"due_date": (_today() - timedelta(days=5)).isoformat()},
            headers=headers,
        )
        summary = (await client.get("/api/v1/invoicing/summary", headers=headers)).json()
        assert summary["open_count"] == 1
        assert summary["overdue_count"] == 1
        assert summary["open_total"] == 121.0
        overdue_list = (
            await client.get("/api/v1/invoicing/invoices?overdue=true", headers=headers)
        ).json()
        assert overdue_list["total"] == 1
        assert overdue_list["items"][0]["overdue"] is True


async def test_tenant_isolation_across_invoicing_tables(client_for) -> None:
    a: Tenant = await make_tenant("inv-iso-a")
    b: Tenant = await make_tenant("inv-iso-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)

    async with client_for(a.host) as ca:
        await _setup_org(ca, a_headers)
        company_id = await _company(ca, a_headers)
        rate_id = (await ca.get("/api/v1/invoicing/tax-rates", headers=a_headers)).json()[0][
            "id"
        ]
        invoice_id = (
            await ca.post(
                "/api/v1/invoicing/invoices",
                json={
                    "company_id": company_id,
                    "lines": [{"description": "W", "quantity": "1", "unit_price": "10"}],
                },
                headers=a_headers,
            )
        ).json()["id"]
        quote_id = (
            await ca.post(
                "/api/v1/invoicing/quotes",
                json={
                    "company_id": company_id,
                    "lines": [{"description": "W", "quantity": "1", "unit_price": "10"}],
                },
                headers=a_headers,
            )
        ).json()["id"]

    async with client_for(b.host) as cb:
        # B sees none of A's rows — 404, never 403 (existence must not leak).
        for path in (
            f"/api/v1/invoicing/invoices/{invoice_id}",
            f"/api/v1/invoicing/quotes/{quote_id}",
        ):
            assert (await cb.get(path, headers=b_headers)).status_code == 404
        assert (
            await cb.get("/api/v1/invoicing/invoices", headers=b_headers)
        ).json()["total"] == 0
        assert (
            await cb.get("/api/v1/invoicing/quotes", headers=b_headers)
        ).json()["total"] == 0

        # B cannot price a line with A's tax rate: the scoped resolve refuses it.
        b_company = await _company(cb, b_headers, name="B klant")
        probe = await cb.post(
            "/api/v1/invoicing/invoices",
            json={
                "company_id": b_company,
                "lines": [
                    {
                        "description": "W",
                        "quantity": "1",
                        "unit_price": "10",
                        "tax_rate_id": rate_id,
                    }
                ],
            },
            headers=b_headers,
        )
        assert probe.status_code == 400

        # And B cannot invoice A's company at all.
        cross = await cb.post(
            "/api/v1/invoicing/invoices",
            json={"company_id": company_id, "lines": []},
            headers=b_headers,
        )
        assert cross.status_code == 404

        # Settings are per org: B's fresh row is untouched by A's seller block.
        b_settings = (
            await cb.get("/api/v1/invoicing/settings", headers=b_headers)
        ).json()
        assert b_settings["company_details"].get("vat_number") is None


async def test_products_catalog(client_for) -> None:
    """Owner request: default products — named line presets with price and tax rate,
    manageable under settings, readable by anyone who can read invoices, tenant-scoped."""
    t: Tenant = await make_tenant("inv-products")
    headers = await auth_cookie(t.user)
    other: Tenant = await make_tenant("inv-products-b")
    other_headers = await auth_cookie(other.user)
    async with client_for(t.host) as c:
        rates = (await c.get("/api/v1/invoicing/tax-rates", headers=headers)).json()
        created = await c.post(
            "/api/v1/invoicing/products",
            json={
                "name": "Onderhoud website",
                "description": "Maandelijks onderhoud",
                "unit": "maand",
                "unit_price": "95.00",
                "tax_rate_id": rates[0]["id"],
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        product = created.json()
        assert product["tax_rate_id"] == rates[0]["id"]

        listed = (await c.get("/api/v1/invoicing/products", headers=headers)).json()
        assert [p["name"] for p in listed] == ["Onderhoud website"]

        # A foreign tax rate is refused.
        bogus = await c.post(
            "/api/v1/invoicing/products",
            json={"name": "X", "unit_price": "1.00", "tax_rate_id": str(uuid_mod.uuid4())},
            headers=headers,
        )
        assert bogus.status_code == 422

        # Deactivate: gone from the picker, back with include_inactive.
        assert (
            await c.patch(
                f"/api/v1/invoicing/products/{product['id']}",
                json={"active": False},
                headers=headers,
            )
        ).status_code == 200
        assert (await c.get("/api/v1/invoicing/products", headers=headers)).json() == []
        assert (
            len(
                (
                    await c.get(
                        "/api/v1/invoicing/products?include_inactive=true", headers=headers
                    )
                ).json()
            )
            == 1
        )

    async with client_for(other.host) as cb:
        assert (
            await cb.get("/api/v1/invoicing/products", headers=other_headers)
        ).json() == []
        assert (
            await cb.patch(
                f"/api/v1/invoicing/products/{product['id']}",
                json={"name": "Kaping"},
                headers=other_headers,
            )
        ).status_code == 404


async def test_invoice_pdf_download(client_for) -> None:
    """Owner feedback: the API renders the invoice document itself — the same PDF the send
    path attaches — instead of leaving 'PDF' to the browser's print dialog."""
    t: Tenant = await make_tenant("inv-pdf")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "PDF BV"}, headers=headers)
        ).json()
        invoice = (
            await c.post(
                "/api/v1/invoicing/invoices",
                json={
                    "company_id": company["id"],
                    "lines": [
                        {"description": "Websiteonderhoud", "quantity": "2", "unit_price": "95"}
                    ],
                },
                headers=headers,
            )
        ).json()
        res = await c.get(f"/api/v1/invoicing/invoices/{invoice['id']}/pdf", headers=headers)
        assert res.status_code == 200, res.text
        assert res.headers["content-type"] == "application/pdf"
        assert res.content.startswith(b"%PDF")
        assert "attachment" in res.headers["content-disposition"]
