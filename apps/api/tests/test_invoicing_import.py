"""Bringing the back catalogue in (docs/INVOICING.md): invoices issued elsewhere arrive as a
spreadsheet through the impex engine, with their totals as stated, their payment *state*
recorded as ordinary payments, and — separately — the PDF the client actually received.

What each test pins, and why it is its own test:

* the sheet's totals are stored **verbatim** and every breakdown reads them back (a mixed-rate
  document at an effective 15% must never be a cent off in the tax row);
* the preview names the **row and the column** for every rule the write enforces (#289);
* an export **round-trips** unchanged, and a re-import may only raise the payment state;
* an import emits **nothing** and pauses reminders;
* the original document is served untouched by every reader, fingerprinted on the invoice,
  refused on a native invoice, and readable by a client exactly when the invoice is.
"""

from __future__ import annotations

import csv
import hashlib
import io
import zipfile

import pytest
from sqlalchemy import select

from app.config import settings
from app.core.auth.models import User
from app.core.events import _handlers
from app.db import async_session_maker
from tests.conftest import Tenant, auth_cookie, make_tenant

# A minimal, valid PDF: what a scanned invoice looks like to a magic-number check.
PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


@pytest.fixture(autouse=True)
def _tmp_storage(monkeypatch, tmp_path) -> None:
    """Originals land on the local backend; point it at a throwaway directory."""
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))


def _csv(header: list[str], rows: list[list[str]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _rows(content: bytes) -> tuple[list[str], list[dict[str, str]]]:
    parsed = list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))
    return parsed[0], [dict(zip(parsed[0], row, strict=True)) for row in parsed[1:]]


async def _setup_org(client, headers) -> None:
    resp = await client.put(
        "/api/v1/invoicing/settings",
        json={
            "company_details": {
                "name": "Agency BV",
                "address_line1": "Kerkstraat 1",
                "postal_code": "1234 AB",
                "city": "Amsterdam",
                "country": "NL",
                "iban": "NL02ABNA0123456789",
            }
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text


async def _company(client, headers, name: str = "Klant BV", number: str | None = None) -> str:
    body = {"name": name, "invoice_email": "boekhouding@klant.nl"}
    if number:
        body["client_number"] = number
    resp = await client.post("/api/v1/companies", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _import(client, headers, content: bytes, *, dry_run: bool) -> dict:
    resp = await client.post(
        "/api/v1/impex/invoice/import",
        params={"dry_run": str(dry_run).lower()},
        files={"file": ("facturen.csv", content, "text/csv")},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _invoice_by_number(client, headers, number: str) -> dict:
    listed = (
        await client.get(
            "/api/v1/invoicing/invoices", params={"q": number}, headers=headers
        )
    ).json()["items"]
    match = next(i for i in listed if i["number"] == number)
    return (
        await client.get(f"/api/v1/invoicing/invoices/{match['id']}", headers=headers)
    ).json()


async def _native_invoice(client, headers, company_id: str, *, issue: bool = True) -> dict:
    created = await client.post(
        "/api/v1/invoicing/invoices",
        json={
            "company_id": company_id,
            "lines": [{"description": "Werk", "quantity": "1", "unit_price": "100"}],
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    invoice = created.json()
    if not issue:
        return invoice
    issued = await client.post(
        f"/api/v1/invoicing/invoices/{invoice['id']}/issue", json={}, headers=headers
    )
    assert issued.status_code == 200, issued.text
    return issued.json()


async def _portal_login(client, headers, slug: str, company_id: str) -> dict[str, str]:
    contact = (
        await client.post(
            "/api/v1/contacts",
            json={
                "first_name": "Piet",
                "last_name": "Klant",
                "email": f"piet-{slug}@example.com",
                "company_ids": [company_id],
            },
            headers=headers,
        )
    ).json()
    enabled = await client.post(
        f"/api/v1/portal/logins/contact/{contact['id']}", headers=headers
    )
    assert enabled.status_code in (200, 201), enabled.text
    async with async_session_maker() as session:
        user = await session.scalar(
            select(User).where(User.email == f"piet-{slug}@example.com")
        )
    assert user is not None
    return await auth_cookie(user)


HEADER = [
    "number", "kind", "company", "issue_date", "due_date", "subtotal", "tax_total", "total",
    "status", "paid_total", "paid_on", "import_source",
]


# --------------------------------------------------------------------------- #
# The import
# --------------------------------------------------------------------------- #
async def test_import_records_issued_invoices_with_their_state(client_for) -> None:
    t: Tenant = await make_tenant("inv-import")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _setup_org(c, headers)
        await _company(c, headers, "Klant BV", "K-100")
        content = _csv(
            HEADER,
            [
                # by client number, paid in full, the amount implied by the status
                ["2023-0001", "invoice", "K-100", "2023-03-01", "2023-03-31",
                 "1000.00", "210.00", "1210.00", "paid", "", "2023-03-20", "Moneybird"],
                # by name, partially paid
                ["2023-0002", "", "Klant BV", "2023-04-01", "", "500.00", "105.00",
                 "605.00", "", "200.00", "2023-04-15", "Moneybird"],
                # open, mixed rates: an effective 15% that must print as stored
                ["2023-0003", "invoice", "Klant BV", "2023-05-01", "2023-05-15", "1000.00",
                 "150.00", "1150.00", "open", "", "", ""],
                ["2023-0004", "invoice", "Klant BV", "2023-06-01", "", "", "", "100.00",
                 "cancelled", "", "", ""],
            ],
        )
        preview = await _import(c, headers, content, dry_run=True)
        assert preview["error_count"] == 0, preview
        assert (preview["creates"], preview["updates"]) == (4, 0)
        report = await _import(c, headers, content, dry_run=False)
        assert report["applied"] is True, report

        paid = await _invoice_by_number(c, headers, "2023-0001")
        assert paid["status"] == "paid"
        assert paid["origin"] == "imported"
        assert paid["import_source"] == "Moneybird"
        assert paid["subtotal"] == "1000.00" and paid["total"] == "1210.00"
        assert paid["paid_total"] == "1210.00"
        assert paid["outstanding"] == "0.00"
        assert [p["paid_on"] for p in paid["payments"]] == ["2023-03-20"]
        assert paid["reminders_paused"] is True
        assert paid["due_date"] == "2023-03-31"
        assert len(paid["lines"]) == 1
        assert paid["lines"][0]["description"] == "Factuur 2023-0001"
        assert paid["tax_groups"][0]["rate_pct"] == "21.00"
        # An imported paid invoice is paid on the day the sheet says, not today.
        assert paid["paid_at"].startswith("2023-03-20")

        partial = await _invoice_by_number(c, headers, "2023-0002")
        assert partial["status"] == "open"
        assert partial["paid_total"] == "200.00"
        assert partial["outstanding"] == "405.00"
        assert partial["due_date"] is not None  # defaulted from the org's due days

        mixed = await _invoice_by_number(c, headers, "2023-0003")
        assert mixed["tax_total"] == "150.00"
        [group] = mixed["tax_groups"]
        assert (group["rate_pct"], group["base"], group["tax"]) == ("15.00", "1000.00", "150.00")
        # …and the paper says the same: the render prints the stored breakdown.
        html = await c.get(f"/api/v1/invoicing/invoices/{mixed['id']}/preview", headers=headers)
        assert html.status_code == 200
        assert "150,00" in html.text and "1.150,00" in html.text
        ubl = await c.get(f"/api/v1/invoicing/invoices/{mixed['id']}/ubl", headers=headers)
        assert ubl.status_code == 200
        assert "<cbc:TaxAmount currencyID=\"EUR\">150.00</cbc:TaxAmount>" in ubl.text

        cancelled = await _invoice_by_number(c, headers, "2023-0004")
        assert cancelled["status"] == "cancelled"
        assert cancelled["subtotal"] == "100.00" and cancelled["tax_total"] == "0.00"

        trail = (
            await c.get(
                "/api/v1/activity",
                params={"entity_type": "invoice", "entity_id": paid["id"]},
                headers=headers,
            )
        ).json()
        actions = [row["action"] for row in (trail["items"] if isinstance(trail, dict) else trail)]
        assert "imported" in actions and "payment_registered" in actions


async def test_preview_names_the_row_and_the_column(client_for) -> None:
    t: Tenant = await make_tenant("inv-import-err")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _setup_org(c, headers)
        await _company(c, headers, "Klant BV")
        content = _csv(
            HEADER,
            [
                ["A-1", "invoice", "Klant BV", "2023-03-01", "", "100.00", "21.00", "125.00",
                 "", "", "", ""],                                       # totals disagree
                ["A-2", "invoice", "Klant BV", "2023-03-01", "", "100.00", "21.00", "121.00",
                 "paid", "", "", ""],                                   # paid, no date
                ["A-3", "invoice", "Klant BV", "2023-03-01", "", "100.00", "21.00", "121.00",
                 "paid", "40.00", "2023-03-10", ""],                    # status vs amount
                ["A-4", "invoice", "Klant BV", "2023-03-01", "", "100.00", "21.00", "121.00",
                 "cancelled", "50.00", "2023-03-10", ""],               # cancelled, paid
                ["A-5", "invoice", "Klant BV", "2023-03-01", "", "100.00", "21.00", "121.00",
                 "open", "", "", ""],                                   # fine
            ],
        )
        preview = await _import(c, headers, content, dry_run=True)
        errors = {(e["row"], e["field"]): e["message_key"] for e in preview["errors"]}
        assert errors == {
            (1, "total"): "invoicing.import.totals_mismatch",
            (2, "paid_on"): "invoicing.import.paid_on_required",
            (3, "status"): "invoicing.import.status_mismatch",
            (4, "paid_total"): "invoicing.import.cancelled_with_payment",
        }
        assert preview["creates"] == 1
        # A commit with errors applies nothing — A-5 included.
        committed = await _import(c, headers, content, dry_run=False)
        assert committed["applied"] is False
        listed = (await c.get("/api/v1/invoicing/invoices", headers=headers)).json()
        assert listed["total"] == 0


async def test_export_round_trips_and_a_reimport_only_raises_the_payment_state(
    client_for,
) -> None:
    t: Tenant = await make_tenant("inv-import-rt")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _setup_org(c, headers)
        company = await _company(c, headers, "Klant BV", "K-7")
        native = await _native_invoice(c, headers, company)
        content = _csv(
            HEADER,
            [["2022-0009", "invoice", "K-7", "2022-09-01", "2022-09-30", "300.00", "63.00",
              "363.00", "open", "", "", "Excel"]],
        )
        assert (await _import(c, headers, content, dry_run=False))["applied"] is True

        exported = await c.get("/api/v1/impex/invoice/export", headers=headers)
        assert exported.status_code == 200
        header, rows = _rows(exported.content)
        assert {"number", "company", "total", "paid_total", "origin", "outstanding"} <= set(header)
        by_number = {row["number"]: row for row in rows}
        assert by_number["2022-0009"]["origin"] == "imported"
        assert by_number["2022-0009"]["company"] == "K-7"
        assert by_number[native["number"]]["origin"] == "native"
        assert by_number[native["number"]]["total"] == native["total"]

        # The list's "te laat" pill reaches the file: a screen narrowed to what is overdue
        # exports exactly that, not the whole register with the pill quietly dropped.
        overdue = await c.get(
            "/api/v1/impex/invoice/export", params={"overdue": "true"}, headers=headers
        )
        assert overdue.status_code == 200
        assert {row["number"] for row in _rows(overdue.content)[1]} == {"2022-0009"}

        # The file goes straight back in: every row an update, nothing refused, nothing moved.
        again = await _import(c, headers, exported.content, dry_run=True)
        assert again["error_count"] == 0, again
        assert (again["creates"], again["updates"]) == (0, 2)
        assert (await _import(c, headers, exported.content, dry_run=False))["applied"] is True
        unchanged = (
            await c.get(f"/api/v1/invoicing/invoices/{native['id']}", headers=headers)
        ).json()
        assert unchanged["paid_total"] == "0.00" and unchanged["payments"] == []

        # Marking the native one paid from the bank statement: the difference is registered.
        statement = _csv(
            ["number", "company", "issue_date", "total", "paid_total", "paid_on"],
            [[native["number"], "K-7", native["issue_date"], native["total"],
              native["total"], "2026-01-15"]],
        )
        assert (await _import(c, headers, statement, dry_run=False))["applied"] is True
        settled = (
            await c.get(f"/api/v1/invoicing/invoices/{native['id']}", headers=headers)
        ).json()
        assert settled["status"] == "paid"
        assert [(p["amount"], p["paid_on"]) for p in settled["payments"]] == [
            (native["total"], "2026-01-15")
        ]

        # Lowering it, or moving the money, is refused per row.
        bad = _csv(
            ["number", "company", "issue_date", "total", "paid_total", "paid_on"],
            [
                [native["number"], "K-7", native["issue_date"], native["total"], "50.00",
                 "2026-01-15"],
                ["2022-0009", "K-7", "2022-09-01", "999.00", "", ""],
            ],
        )
        preview = await _import(c, headers, bad, dry_run=True)
        errors = {(e["row"], e["field"]): e["message_key"] for e in preview["errors"]}
        assert errors == {
            (1, "paid_total"): "invoicing.import.payments_reduced",
            (2, "total"): "invoicing.import.locked",
        }


async def test_credit_note_import_writes_its_source_down(client_for) -> None:
    t: Tenant = await make_tenant("inv-import-credit")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _setup_org(c, headers)
        await _company(c, headers, "Klant BV")
        invoices = _csv(
            HEADER,
            [["F-1", "invoice", "Klant BV", "2023-01-10", "", "1000.00", "210.00", "1210.00",
              "open", "", "", ""]],
        )
        assert (await _import(c, headers, invoices, dry_run=False))["applied"] is True
        # The note comes in a second file: a reference is resolved against what exists
        # before the preview, so a note and its source travel in that order.
        content = _csv(
            [*HEADER, "credit_for"],
            [
                # stated positive, as many packages export a credit note — stored negative
                ["C-1", "credit_note", "Klant BV", "2023-02-01", "", "400.00", "84.00",
                 "484.00", "", "", "", "", "F-1"],
            ],
        )
        preview = await _import(c, headers, content, dry_run=True)
        assert preview["error_count"] == 0, preview
        assert (await _import(c, headers, content, dry_run=False))["applied"] is True
        source = await _invoice_by_number(c, headers, "F-1")
        note = await _invoice_by_number(c, headers, "C-1")
        assert note["total"] == "-484.00"
        assert note["credit_for_number"] == "F-1"
        assert note["applied_total"] == "484.00"
        assert note["status"] == "paid"  # fully absorbed: nothing left to refund
        assert source["credited_total"] == "484.00"
        assert source["outstanding"] == "726.00"
        assert source["credited"] is True

        # A credit note may not name another credit note.
        wrong = _csv(
            [*HEADER, "credit_for"],
            [["C-2", "credit_note", "Klant BV", "2023-02-02", "", "10.00", "2.10", "12.10",
              "", "", "", "", "C-1"]],
        )
        preview = await _import(c, headers, wrong, dry_run=True)
        assert [(e["field"], e["message_key"]) for e in preview["errors"]] == [
            ("credit_for", "invoicing.import.credit_source")
        ]


async def test_an_import_emits_nothing(client_for) -> None:
    """Eight hundred rows must not become eight hundred notifications (docs/INVOICING.md)."""
    t: Tenant = await make_tenant("inv-import-quiet")
    headers = await auth_cookie(t.user)
    seen: list[str] = []

    async def spy(event: str):  # noqa: ANN202
        async def handler(ctx, payload):  # noqa: ANN001, ANN202
            seen.append(event)

        return handler

    handlers = {event: await spy(event) for event in ("invoice.issued", "invoice.paid")}
    for event, handler in handlers.items():
        _handlers.setdefault(event, []).append(handler)
    try:
        async with client_for(t.host) as c:
            await _setup_org(c, headers)
            await _company(c, headers, "Klant BV")
            content = _csv(
                HEADER,
                [["Q-1", "invoice", "Klant BV", "2023-01-10", "", "100.00", "21.00", "121.00",
                  "paid", "", "2023-01-20", ""]],
            )
            assert (await _import(c, headers, content, dry_run=False))["applied"] is True
            assert seen == []
    finally:
        for event, handler in handlers.items():
            _handlers[event].remove(handler)


async def test_numbers_are_per_tenant(client_for) -> None:
    a: Tenant = await make_tenant("inv-import-a")
    b: Tenant = await make_tenant("inv-import-b")
    content_for = {}
    for tenant in (a, b):
        headers = await auth_cookie(tenant.user)
        async with client_for(tenant.host) as c:
            await _setup_org(c, headers)
            await _company(c, headers, "Klant BV")
            content = _csv(
                HEADER,
                [["SAME-1", "invoice", "Klant BV", "2023-01-10", "", "100.00", "21.00",
                  "121.00", "open", "", "", ""]],
            )
            assert (await _import(c, headers, content, dry_run=False))["applied"] is True
            content_for[tenant.host] = headers
    for tenant in (a, b):
        async with client_for(tenant.host) as c:
            listed = (
                await c.get("/api/v1/invoicing/invoices", headers=content_for[tenant.host])
            ).json()
            assert [i["number"] for i in listed["items"]] == ["SAME-1"]


# --------------------------------------------------------------------------- #
# The original document
# --------------------------------------------------------------------------- #
async def test_original_is_served_untouched_and_fingerprinted(client_for) -> None:
    t: Tenant = await make_tenant("inv-original")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _setup_org(c, headers)
        company = await _company(c, headers, "Klant BV")
        content = _csv(
            HEADER,
            [["O-1", "invoice", "Klant BV", "2023-01-10", "", "100.00", "21.00", "121.00",
              "open", "", "", ""]],
        )
        assert (await _import(c, headers, content, dry_run=False))["applied"] is True
        imported = await _invoice_by_number(c, headers, "O-1")
        assert imported["original"] is None

        # Not a PDF by its bytes — whatever the declared type says.
        refused = await c.post(
            f"/api/v1/invoicing/invoices/{imported['id']}/original",
            files={"file": ("scan.pdf", b"\x89PNG not a pdf", "application/pdf")},
            headers=headers,
        )
        assert refused.status_code == 422, refused.text

        attached = await c.post(
            f"/api/v1/invoicing/invoices/{imported['id']}/original",
            files={"file": ("O-1.pdf", PDF, "application/pdf")},
            headers=headers,
        )
        assert attached.status_code == 200, attached.text
        original = attached.json()["original"]
        assert original["filename"] == "O-1.pdf"
        assert original["sha256"] == hashlib.sha256(PDF).hexdigest()
        assert original["size_bytes"] == len(PDF)

        # Every reader hands the bytes over as they are: the download…
        pdf = await c.get(f"/api/v1/invoicing/invoices/{imported['id']}/pdf", headers=headers)
        assert pdf.status_code == 200
        assert pdf.content == PDF
        assert pdf.headers["content-disposition"].endswith('"O-1.pdf"')
        # …and the public link (minted at import, like at issue).
        detail = (
            await c.get(f"/api/v1/invoicing/invoices/{imported['id']}", headers=headers)
        ).json()
        token = detail["public_url"].rsplit("/", 1)[-1]
        public = await c.get(f"/api/v1/invoicing/public/invoices/{token}")
        assert public.status_code == 200 and public.json()["has_original"] is True
        public_pdf = await c.get(f"/api/v1/invoicing/public/invoices/{token}/pdf")
        assert public_pdf.content == PDF

        # Replacing records both halves and leaves one file row behind.
        other = PDF.replace(b"1.4", b"1.7")
        replaced = await c.post(
            f"/api/v1/invoicing/invoices/{imported['id']}/original",
            files={"file": ("O-1-v2.pdf", other, "application/pdf")},
            headers=headers,
        )
        assert replaced.json()["original"]["sha256"] == hashlib.sha256(other).hexdigest()
        files = (
            await c.get(
                "/api/v1/files",
                params={"entity_type": "invoice", "entity_id": imported["id"]},
                headers=headers,
            )
        ).json()
        assert [f["filename"] for f in files] == ["O-1-v2.pdf"]
        trail = (
            await c.get(
                "/api/v1/activity",
                params={"entity_type": "invoice", "entity_id": imported["id"]},
                headers=headers,
            )
        ).json()
        actions = [row["action"] for row in (trail["items"] if isinstance(trail, dict) else trail)]
        assert actions.count("original_attached") == 2

        # An explicit null detaches (§18), and the render is back.
        detached = await c.patch(
            f"/api/v1/invoicing/invoices/{imported['id']}",
            json={"original_file_id": None},
            headers=headers,
        )
        assert detached.status_code == 200, detached.text
        assert detached.json()["original"] is None
        # (The render itself is WeasyPrint's, exercised by test_invoicing_render; the HTML
        # preview is the same artefact and needs no native library.)
        rendered = await c.get(
            f"/api/v1/invoicing/invoices/{imported['id']}/preview", headers=headers
        )
        assert rendered.status_code == 200 and "O-1" in rendered.text

        # A native invoice's document is its render: no original, ever.
        native = await _native_invoice(c, headers, company)
        refused = await c.post(
            f"/api/v1/invoicing/invoices/{native['id']}/original",
            files={"file": ("x.pdf", PDF, "application/pdf")},
            headers=headers,
        )
        assert refused.status_code == 409
        assert refused.json()["error"]["message"] == "errors.invoicing.not_imported"


async def test_the_json_twin_adopts_a_file_uploaded_against_the_invoice(client_for) -> None:
    t: Tenant = await make_tenant("inv-original-json")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _setup_org(c, headers)
        await _company(c, headers, "Klant BV")
        content = _csv(
            HEADER,
            [["J-1", "invoice", "Klant BV", "2023-01-10", "", "100.00", "21.00", "121.00",
              "open", "", "", ""],
             ["J-2", "invoice", "Klant BV", "2023-01-11", "", "100.00", "21.00", "121.00",
              "open", "", "", ""]],
        )
        assert (await _import(c, headers, content, dry_run=False))["applied"] is True
        one = await _invoice_by_number(c, headers, "J-1")
        two = await _invoice_by_number(c, headers, "J-2")
        uploaded = await c.post(
            "/api/v1/files",
            params={"entity_type": "invoice", "entity_id": one["id"]},
            files={"file": ("J-1.pdf", PDF, "application/pdf")},
            headers=headers,
        )
        assert uploaded.status_code == 201, uploaded.text
        file_id = uploaded.json()["id"]
        # Another invoice cannot adopt it: a file is its own record's.
        wrong = await c.patch(
            f"/api/v1/invoicing/invoices/{two['id']}",
            json={"original_file_id": file_id},
            headers=headers,
        )
        assert wrong.status_code == 400, wrong.text
        adopted = await c.patch(
            f"/api/v1/invoicing/invoices/{one['id']}",
            json={"original_file_id": file_id},
            headers=headers,
        )
        assert adopted.status_code == 200, adopted.text
        assert adopted.json()["original"]["sha256"] == hashlib.sha256(PDF).hexdigest()


async def test_a_zip_of_originals_matches_by_number(client_for) -> None:
    t: Tenant = await make_tenant("inv-original-zip")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _setup_org(c, headers)
        await _company(c, headers, "Klant BV")
        rows = [
            [number, "invoice", "Klant BV", "2023-01-10", "", "100.00", "21.00", "121.00",
             "open", "", "", ""]
            for number in ("2023-001", "2023-002", "2023-0021", "2023-003")
        ]
        assert (await _import(c, headers, _csv(HEADER, rows), dry_run=False))["applied"] is True
        # 2023-003 already holds one: the batch leaves it alone.
        three = await _invoice_by_number(c, headers, "2023-003")
        await c.post(
            f"/api/v1/invoicing/invoices/{three['id']}/original",
            files={"file": ("2023-003.pdf", PDF, "application/pdf")},
            headers=headers,
        )
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("scans/2023_001.pdf", PDF)              # exact, separators differ
            archive.writestr("Factuur 2023-002 Klant BV.pdf", PDF)   # contained
            archive.writestr("2023-00.pdf", PDF)                     # matches nothing
            archive.writestr("2023-002-en-2023-0021.pdf", PDF)       # two numbers
            archive.writestr("2023-003.pdf", PDF)                    # already attached
            archive.writestr("notes.txt", b"hello")                  # not a pdf
        result = await c.post(
            "/api/v1/invoicing/invoices/originals",
            files={"file": ("originelen.zip", buffer.getvalue(), "application/zip")},
            headers=headers,
        )
        assert result.status_code == 200, result.text
        report = result.json()
        assert sorted(m["number"] for m in report["matched"]) == ["2023-001", "2023-002"]
        assert report["unmatched"] == ["2023-00.pdf"]
        assert report["ambiguous"] == ["2023-002-en-2023-0021.pdf"]
        assert [m["number"] for m in report["already_attached"]] == ["2023-003"]
        assert report["not_pdf"] == ["notes.txt"]
        one = await _invoice_by_number(c, headers, "2023-001")
        assert one["original"]["filename"] == "2023_001.pdf"


async def test_a_client_reads_the_original_exactly_when_the_invoice(client_for) -> None:
    t: Tenant = await make_tenant("inv-original-portal")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _setup_org(c, headers)
        mine = await _company(c, headers, "Mijn BV")
        await _company(c, headers, "Ander BV")
        content = _csv(
            HEADER,
            [["P-1", "invoice", "Mijn BV", "2023-01-10", "", "100.00", "21.00", "121.00",
              "open", "", "", ""],
             ["P-2", "invoice", "Ander BV", "2023-01-10", "", "100.00", "21.00", "121.00",
              "open", "", "", ""]],
        )
        assert (await _import(c, headers, content, dry_run=False))["applied"] is True
        ours = await _invoice_by_number(c, headers, "P-1")
        theirs = await _invoice_by_number(c, headers, "P-2")
        file_ids = {}
        for invoice in (ours, theirs):
            attached = await c.post(
                f"/api/v1/invoicing/invoices/{invoice['id']}/original",
                files={"file": (f"{invoice['number']}.pdf", PDF, "application/pdf")},
                headers=headers,
            )
            file_ids[invoice["number"]] = attached.json()["original"]["file_id"]

        portal = await _portal_login(c, headers, "inv-original-portal", mine)
        pdf = await c.get(f"/api/v1/invoicing/invoices/{ours['id']}/pdf", headers=portal)
        assert pdf.status_code == 200 and pdf.content == PDF
        # The file route answers the same way as the invoice route, by id.
        direct = await c.get(f"/api/v1/files/{file_ids['P-1']}", headers=portal)
        assert direct.status_code == 200
        assert (
            await c.get(f"/api/v1/invoicing/invoices/{theirs['id']}/pdf", headers=portal)
        ).status_code == 404
        assert (await c.get(f"/api/v1/files/{file_ids['P-2']}", headers=portal)).status_code == 404
        # …and a client never attaches one.
        refused = await c.post(
            f"/api/v1/invoicing/invoices/{ours['id']}/original",
            files={"file": ("x.pdf", PDF, "application/pdf")},
            headers=portal,
        )
        assert refused.status_code == 403


async def test_export_is_a_bounded_number_of_queries(client_for, count_queries) -> None:
    t: Tenant = await make_tenant("inv-import-perf")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _setup_org(c, headers)
        await _company(c, headers, "Klant BV")
        rows = [
            [f"N-{i:03d}", "invoice", "Klant BV", "2023-01-10", "", "100.00", "21.00",
             "121.00", "open", "", "", ""]
            for i in range(30)
        ]
        assert (await _import(c, headers, _csv(HEADER, rows), dry_run=False))["applied"] is True
        with count_queries() as counter:
            exported = await c.get("/api/v1/impex/invoice/export", headers=headers)
        assert exported.status_code == 200
        assert len(_rows(exported.content)[1]) == 30
        invoice_reads = [
            statement
            for statement in counter.matching("invoices")
            if "invoice_payments" not in statement and "invoice_lines" not in statement
        ]
        assert len(invoice_reads) <= 3, "\n\n".join(invoice_reads)
