"""The client label and the name a document is addressed to (``companies.legal_name``).

``companies.name`` is what the product *prints* — lists, pickers, panels, reports,
notifications. ``companies.legal_name`` is what a document is *addressed to*, and it is
``NULL`` for the great majority of clients, meaning "the label is also the legal name".

Every assertion here is about one of two things: that the resolution is ``legal_name or name``
wherever a document is produced, and that a client with no legal name behaves exactly as they
did before the column existed — which is what makes this safe to ship into an instance that is
already invoicing.
"""

from __future__ import annotations

import csv
import io

from app.core.naming import document_name, document_name_of
from tests.conftest import Tenant, auth_cookie, make_tenant


# --------------------------------------------------------------------------- #
# The resolution itself
# --------------------------------------------------------------------------- #
def test_absent_legal_name_resolves_to_the_label() -> None:
    assert document_name("Bakkerij Jansen", None) == "Bakkerij Jansen"
    # Blank is absent. A form posts "" for an empty box, and an import or an older row may
    # still carry whitespace; a bill-to headed by a space is not a state worth having.
    assert document_name("Bakkerij Jansen", "") == "Bakkerij Jansen"
    assert document_name("Bakkerij Jansen", "   ") == "Bakkerij Jansen"


def test_a_legal_name_wins_and_is_trimmed() -> None:
    assert document_name("Bakkerij Jansen", " J. Jansen Holding B.V. ") == "J. Jansen Holding B.V."


def test_resolution_reads_mappings_and_objects_alike() -> None:
    """Invoicing hands this raw SQL rows; SnelStart hands it a ``Company``."""

    class _Row:
        name = "Bakkerij Jansen"
        legal_name = "J. Jansen Holding B.V."

    assert document_name_of(_Row()) == "J. Jansen Holding B.V."
    assert document_name_of({"name": "Bakkerij Jansen", "legal_name": None}) == "Bakkerij Jansen"
    # A mapping from a query that predates the column at all.
    assert document_name_of({"name": "Bakkerij Jansen"}) == "Bakkerij Jansen"


# --------------------------------------------------------------------------- #
# The record
# --------------------------------------------------------------------------- #
async def test_legal_name_round_trips_and_blank_clears_it(client_for) -> None:
    tenant: Tenant = await make_tenant("legal-crud")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        created = await client.post(
            "/api/v1/companies",
            json={"name": "Bakkerij Jansen", "legal_name": "J. Jansen Holding B.V."},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        company = created.json()
        assert company["name"] == "Bakkerij Jansen"
        assert company["legal_name"] == "J. Jansen Holding B.V."

        # A client created without one keeps ``None`` — never a copy of the label, which would
        # make "does this client invoice under another name?" unanswerable.
        plain = await client.post(
            "/api/v1/companies", json={"name": "Kapsalon Els"}, headers=headers
        )
        assert plain.status_code == 201
        assert plain.json()["legal_name"] is None

        # An emptied box clears it — unlike ``client_number``, having no separate legal name is
        # a real state rather than an absence of information.
        cleared = await client.patch(
            f"/api/v1/companies/{company['id']}", json={"legal_name": ""}, headers=headers
        )
        assert cleared.status_code == 200
        assert cleared.json()["legal_name"] is None


async def test_both_names_are_searchable(client_for) -> None:
    """A register you can only search by the half you already know is half a register."""
    tenant: Tenant = await make_tenant("legal-search")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        created = await client.post(
            "/api/v1/companies",
            json={"name": "Bakkerij Jansen", "legal_name": "J. Jansen Holding B.V."},
            headers=headers,
        )
        assert created.status_code == 201
        company_id = created.json()["id"]
        await client.post("/api/v1/companies", json={"name": "Kapsalon Els"}, headers=headers)

        for query in ("Bakkerij", "Holding", "jansen holding"):
            found = await client.get(f"/api/v1/companies?q={query}", headers=headers)
            assert found.status_code == 200
            ids = [row["id"] for row in found.json()["items"]]
            assert ids == [company_id], f"searching {query!r} found {ids}"

        # And the search still narrows: the other client is not swept in.
        els = await client.get("/api/v1/companies?q=Kapsalon", headers=headers)
        assert [row["name"] for row in els.json()["items"]] == ["Kapsalon Els"]


async def test_a_legal_name_change_is_on_the_activity_trail(client_for) -> None:
    tenant: Tenant = await make_tenant("legal-trail")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        created = await client.post(
            "/api/v1/companies", json={"name": "Bakkerij Jansen"}, headers=headers
        )
        company_id = created.json()["id"]
        await client.patch(
            f"/api/v1/companies/{company_id}",
            json={"legal_name": "J. Jansen Holding B.V."},
            headers=headers,
        )
        trail = await client.get(
            f"/api/v1/activity?entity_type=company&entity_id={company_id}", headers=headers
        )
        assert trail.status_code == 200, trail.text
        changes = [
            entry["payload"].get("changes", {})
            for entry in trail.json()
            if entry["action"] == "updated"
        ]
        assert any("legal_name" in change for change in changes), changes


# --------------------------------------------------------------------------- #
# Documents
# --------------------------------------------------------------------------- #
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
                "vat_number": "NL123456789B01",
                "coc_number": "12345678",
                "iban": "NL02ABNA0123456789",
            }
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text


async def _invoice_for(client, headers, company_id: str) -> dict:
    created = await client.post(
        "/api/v1/invoicing/invoices",
        json={
            "company_id": company_id,
            "lines": [{"description": "Werk", "quantity": "1", "unit_price": "100"}],
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    return created.json()


async def test_an_invoice_is_addressed_to_the_legal_name(client_for) -> None:
    """The one place being wrong is a legal problem rather than an awkward one."""
    tenant: Tenant = await make_tenant("legal-invoice")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        company = await client.post(
            "/api/v1/companies",
            json={
                "name": "Bakkerij Jansen",
                "legal_name": "J. Jansen Holding B.V.",
                "invoice_email": "boekhouding@jansen.nl",
            },
            headers=headers,
        )
        invoice = await _invoice_for(client, headers, company.json()["id"])
        assert invoice["customer"]["name"] == "J. Jansen Holding B.V."
        # The label travels beside it: the snapshot is a record, and the covering e-mail greets
        # a human. Named ``trade_name`` because the renderer's own ``label`` is the block
        # heading.
        assert invoice["customer"]["trade_name"] == "Bakkerij Jansen"

        # Issuing freezes it, and a later rename never rewrites what was sent.
        issued = await client.post(
            f"/api/v1/invoicing/invoices/{invoice['id']}/issue", json={}, headers=headers
        )
        assert issued.status_code == 200, issued.text
        assert issued.json()["customer"]["name"] == "J. Jansen Holding B.V."
        await client.patch(
            f"/api/v1/companies/{company.json()['id']}",
            json={"legal_name": "Jansen Beheer B.V."},
            headers=headers,
        )
        again = await client.get(
            f"/api/v1/invoicing/invoices/{invoice['id']}", headers=headers
        )
        assert again.json()["customer"]["name"] == "J. Jansen Holding B.V."


async def test_a_client_with_no_legal_name_invoices_exactly_as_before(client_for) -> None:
    """The whole safety argument for shipping this into a live instance, in one test."""
    tenant: Tenant = await make_tenant("legal-plain")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        company = await client.post(
            "/api/v1/companies", json={"name": "Kapsalon Els"}, headers=headers
        )
        invoice = await _invoice_for(client, headers, company.json()["id"])
        assert invoice["customer"]["name"] == "Kapsalon Els"
        assert invoice["customer"]["trade_name"] == "Kapsalon Els"


async def test_ubl_separates_the_trading_name_from_the_registered_one(client_for) -> None:
    """EN 16931 draws the same distinction, in two different elements (BT-45 / BT-47).

    Through the real download, not the serializer: what makes the two elements right is that the
    *snapshot* carries both names, and a unit test over a hand-built dict would prove only that
    the XML writer copies whatever it is handed.
    """
    tenant: Tenant = await make_tenant("legal-ubl")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        company = await client.post(
            "/api/v1/companies",
            json={
                "name": "Bakkerij Jansen",
                "legal_name": "J. Jansen Holding B.V.",
                "coc_number": "12345678",
                "country": "NL",
            },
            headers=headers,
        )
        invoice = await _invoice_for(client, headers, company.json()["id"])
        issued = await client.post(
            f"/api/v1/invoicing/invoices/{invoice['id']}/issue", json={}, headers=headers
        )
        assert issued.status_code == 200, issued.text

        ubl = await client.get(
            f"/api/v1/invoicing/invoices/{invoice['id']}/ubl", headers=headers
        )
        assert ubl.status_code == 200, ubl.text
        xml = ubl.content.decode()
        # BT-45, the trading name a human recognises…
        assert "<cbc:Name>Bakkerij Jansen</cbc:Name>" in xml
        # …and BT-47, the registered entity the accountant books against.
        assert "<cbc:RegistrationName>J. Jansen Holding B.V.</cbc:RegistrationName>" in xml
        # The seller has one name and still fills both elements rather than emptying one.
        assert "<cbc:RegistrationName>Agency BV</cbc:RegistrationName>" in xml


async def test_ubl_for_a_client_with_one_name_fills_both_elements(client_for) -> None:
    tenant: Tenant = await make_tenant("legal-ubl-plain")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        company = await client.post(
            "/api/v1/companies", json={"name": "Kapsalon Els"}, headers=headers
        )
        invoice = await _invoice_for(client, headers, company.json()["id"])
        await client.post(
            f"/api/v1/invoicing/invoices/{invoice['id']}/issue", json={}, headers=headers
        )
        xml = (
            await client.get(
                f"/api/v1/invoicing/invoices/{invoice['id']}/ubl", headers=headers
            )
        ).content.decode()
        assert "<cbc:Name>Kapsalon Els</cbc:Name>" in xml
        assert "<cbc:RegistrationName>Kapsalon Els</cbc:RegistrationName>" in xml


# --------------------------------------------------------------------------- #
# Spreadsheets (CLAUDE.md §17)
# --------------------------------------------------------------------------- #
def _csv_bytes(header: list[str], rows: list[list[str]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _file(content: bytes) -> dict:
    return {"file": ("clients.csv", content, "text/csv")}


async def test_the_legal_name_is_a_column_and_survives_a_round_trip(client_for) -> None:
    tenant: Tenant = await make_tenant("legal-impex")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        content = _csv_bytes(
            ["name", "legal_name"],
            [["Bakkerij Jansen", "J. Jansen Holding B.V."], ["Kapsalon Els", ""]],
        )
        report = (
            await client.post(
                "/api/v1/impex/company/import",
                params={"dry_run": "false"},
                files=_file(content),
                headers=headers,
            )
        ).json()
        assert report["applied"] is True, report
        assert report["creates"] == 2

        rows = (await client.get("/api/v1/companies?sort=name", headers=headers)).json()["items"]
        by_name = {row["name"]: row for row in rows}
        assert by_name["Bakkerij Jansen"]["legal_name"] == "J. Jansen Holding B.V."
        # An empty cell is not "unfilled and therefore the label" — it stays NULL, which is what
        # the label-is-also-the-legal-name state actually is.
        assert by_name["Kapsalon Els"]["legal_name"] is None

        export = await client.get("/api/v1/impex/company/export", headers=headers)
        assert export.status_code == 200, export.text
        exported = export.content.decode("utf-8-sig")
        assert "legal_name" in exported.splitlines()[0].split(",")
        assert "J. Jansen Holding B.V." in exported


async def test_a_reference_resolves_by_either_of_a_clients_names(client_for) -> None:
    """A spreadsheet from the bookkeeper carries the name this product does not print."""
    tenant: Tenant = await make_tenant("legal-impex-fk")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        company = (
            await client.post(
                "/api/v1/companies",
                json={"name": "Bakkerij Jansen", "legal_name": "J. Jansen Holding B.V."},
                headers=headers,
            )
        ).json()

        content = _csv_bytes(
            ["first_name", "email", "company"],
            [
                ["Ann", "ann@jansen.nl", "Bakkerij Jansen"],          # by label
                ["Bob", "bob@jansen.nl", "J. Jansen Holding B.V."],   # by legal name
            ],
        )
        report = (
            await client.post(
                "/api/v1/impex/contact/import",
                params={"dry_run": "false"},
                files=_file(content),
                headers=headers,
            )
        ).json()
        assert report["errors"] == [], report
        assert report["applied"] is True
        linked = (
            await client.get(
                "/api/v1/contacts", params={"company_id": company["id"]}, headers=headers
            )
        ).json()
        assert sorted(row["first_name"] for row in linked["items"]) == ["Ann", "Bob"]


async def test_a_name_two_clients_answer_to_is_still_ambiguous(client_for) -> None:
    """Widening the lookup must not make a *wrong* match possible, only fewer missed ones."""
    tenant: Tenant = await make_tenant("legal-impex-amb")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await client.post("/api/v1/companies", json={"name": "Jansen B.V."}, headers=headers)
        await client.post(
            "/api/v1/companies",
            json={"name": "Bakkerij Jansen", "legal_name": "Jansen B.V."},
            headers=headers,
        )
        content = _csv_bytes(
            ["first_name", "email", "company"], [["Ann", "ann@x.nl", "Jansen B.V."]]
        )
        report = (
            await client.post(
                "/api/v1/impex/contact/import",
                params={"dry_run": "false"},
                files=_file(content),
                headers=headers,
            )
        ).json()
        assert report["applied"] is False
        assert report["errors"] == [
            {"row": 1, "field": "company", "message_key": "impex.errors.ambiguous_match"}
        ]
