"""CSV import/export (issue #77): round-trip, RBAC gates, upsert, FK resolution, custom
fields, tenant isolation, and the synchronous row cap."""

from __future__ import annotations

import csv
import io

from openpyxl import Workbook
from sqlalchemy import text

from app.db import async_session_maker, set_current_org
from tests.conftest import auth_cookie, make_tenant

COMPANY_HEADER = [
    "name", "client_number", "website", "phone", "invoice_email", "status",
    # Billing identity (issue #11, shipped with invoicing #207) — including the name a
    # document is addressed to, which belongs to this block and not beside "name".
    "legal_name",
    "vat_number", "coc_number", "address_line1", "house_number", "address_line2",
    "postal_code", "city", "country",
    "notes",
    # Contributed by the contacts module (issue #77): the client's contact person, carried in
    # the company's own row. Present because this caller holds contacts' write permissions —
    # the header is deliberately caller-dependent (see test_contributed_columns_are_gated).
    "contact_first_name", "contact_last_name", "contact_email", "contact_phone",
    "contact_job_title",
]


def _csv_bytes(header: list[str], rows: list[list[str]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _xlsx_bytes(header: list[str], rows: list[list[str]]) -> bytes:
    """The same table as :func:`_csv_bytes`, as a workbook.

    Rows are appended as given, so a **short** row stays short: a spreadsheet stops writing
    cells at the last one it has, and the column of a row that ended early never arrives.
    """
    workbook = Workbook()
    workbook.active.append(header)
    for row in rows:
        workbook.active.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _file(content: bytes) -> dict:
    return {"file": ("import.csv", content, "text/csv")}


def _rows(content: bytes) -> tuple[list[str], list[dict[str, str]]]:
    """Parse a CSV response body into (header, rows-as-dicts)."""
    parsed = list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))
    header = parsed[0]
    return header, [dict(zip(header, row, strict=True)) for row in parsed[1:]]


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #
async def test_export_round_trips_with_custom_fields(client_for) -> None:
    t = await make_tenant("impex-exp")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        r = await c.post(
            "/api/v1/custom-fields/definitions",
            json={
                "entity_type": "company",
                "key": "vat",
                "label_i18n": {"nl": "BTW", "en": "VAT"},
                "data_type": "text",
            },
            headers=headers,
        )
        assert r.status_code == 201
        for body in (
            {"name": "Acme", "website": "https://acme.test", "custom": {"vat": "NL01"}},
            {"name": "Beta", "status": "lead", "custom": {"vat": "NL02"}},
        ):
            assert (
                await c.post("/api/v1/companies", json=body, headers=headers)
            ).status_code == 201

        r = await c.get("/api/v1/impex/company/export", headers=headers)
        assert r.status_code == 200
        assert r.content.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM, or Excel mangles accents
        assert r.headers["content-type"].startswith("text/csv")
        assert 'filename="company-export.csv"' in r.headers["content-disposition"]

        header, rows = _rows(r.content)
        # Stable keys, custom-field columns appended by definition key (round-trippable).
        assert header == COMPANY_HEADER + ["vat"]
        by_name = {row["name"]: row for row in rows}
        assert by_name["Acme"]["vat"] == "NL01"
        assert by_name["Acme"]["website"] == "https://acme.test"
        assert by_name["Beta"]["status"] == "lead"

        # Round-trip: importing the export back matches every row — nothing new, no errors.
        r2 = await c.post(
            "/api/v1/impex/company/import",
            params={"dry_run": "false"},
            files={"file": ("export.csv", r.content, "text/csv")},
            headers=headers,
        )
        assert r2.status_code == 200
        report = r2.json()
        assert report["creates"] == 0
        assert report["updates"] == 2
        assert report["errors"] == []
        assert report["applied"] is True
        listing = (await c.get("/api/v1/companies", headers=headers)).json()
        assert listing["total"] == 2
        assert {i["custom"]["vat"] for i in listing["items"]} == {"NL01", "NL02"}


async def test_export_applies_the_list_filters(client_for) -> None:
    t = await make_tenant("impex-filt")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        for name, status in (("Acme", "active"), ("Beta", "lead"), ("Gamma", "lead")):
            await c.post(
                "/api/v1/companies", json={"name": name, "status": status}, headers=headers
            )

        _, rows = _rows(
            (
                await c.get(
                    "/api/v1/impex/company/export",
                    params={"status": "lead"},
                    headers=headers,
                )
            ).content
        )
        assert {row["name"] for row in rows} == {"Beta", "Gamma"}

        _, rows = _rows(
            (
                await c.get(
                    "/api/v1/impex/company/export", params={"q": "acm"}, headers=headers
                )
            ).content
        )
        assert {row["name"] for row in rows} == {"Acme"}


# --------------------------------------------------------------------------- #
# Permission gates (§15) — the deny-by-default sweep also covers the zero-permission case
# --------------------------------------------------------------------------- #
async def test_member_holds_neither_bulk_gate_by_default(client_for) -> None:
    """Bulk is not an employee's capability by default (owner call, catalog.py).

    The member role still holds ``companies.company.read``, so this is the second gate doing
    its whole job: reading a client is not the same act as downloading every client, and an
    agency decides who may do the latter deliberately. Granting ``impex.export`` back to the
    role is a click in Instellingen → Rollen — and it covers every entity at once, which is
    exactly why the pair is one capability rather than one per screen.
    """
    t = await make_tenant("impex-member", role="member")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        assert (
            await c.get("/api/v1/companies", headers=headers)
        ).status_code == 200  # the entity read the member does hold
        for response in (
            await c.get("/api/v1/impex/company/export", headers=headers),
            await c.post(
                "/api/v1/impex/company/import",
                files=_file(_csv_bytes(["name"], [["Nope"]])),
                headers=headers,
            ),
        ):
            assert response.status_code == 403
            assert response.json()["error"]["message"] == "errors.forbidden"


async def test_granting_impex_export_reaches_every_entity(client_for) -> None:
    """One capability across the whole surface: granting it once opens each entity the role
    can already read, and opens nothing it cannot."""
    t = await make_tenant("impex-grant", role="member")
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        await session.execute(
            text(
                "INSERT INTO role_permissions (id, org_id, role_id, permission) "
                "SELECT gen_random_uuid(), :org, id, 'impex.export' FROM roles "
                "WHERE org_id = :org AND key = 'member'"
            ),
            {"org": t.org.id},
        )
        await session.commit()
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        # Readable by the member role: companies, contacts, domains, hosting, websites.
        for entity in ("company", "contact", "domain", "hosting", "website"):
            r = await c.get(f"/api/v1/impex/{entity}/export", headers=headers)
            assert r.status_code == 200, entity
        # Not readable: subscriptions are admin-only money (subscriptions/permissions.py), so
        # the bulk grant alone changes nothing there.
        assert (
            await c.get("/api/v1/impex/subscription/export", headers=headers)
        ).status_code == 403


async def test_export_requires_the_read_permission(client_for) -> None:
    t = await make_tenant("impex-noperm", role="member")
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        await session.execute(text("DELETE FROM membership_roles"))
        await session.commit()
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        assert (
            await c.get("/api/v1/impex/company/export", headers=headers)
        ).status_code == 403


# --------------------------------------------------------------------------- #
# Import — dry run, commit, upsert
# --------------------------------------------------------------------------- #
async def test_dry_run_reports_row_numbers_and_writes_nothing(client_for) -> None:
    t = await make_tenant("impex-dry")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.post("/api/v1/companies", json={"name": "Existing"}, headers=headers)
        content = _csv_bytes(
            ["name", "website"],
            [
                ["Existing", "https://updated.test"],  # data row 1: update
                ["Newco", ""],                          # data row 2: create
                ["", "https://nameless.test"],          # data row 3: required name missing
            ],
        )
        r = await c.post(  # dry_run defaults to true
            "/api/v1/impex/company/import", files=_file(content), headers=headers
        )
        assert r.status_code == 200
        report = r.json()
        assert report["dry_run"] is True
        assert report["applied"] is False
        assert (report["rows"], report["creates"], report["updates"]) == (3, 1, 1)
        assert report["errors"] == [
            {"row": 3, "field": "name", "message_key": "errors.required"}
        ]

        # A dry run writes NOTHING — not even the valid rows.
        listing = (await c.get("/api/v1/companies", headers=headers)).json()
        assert listing["total"] == 1
        assert listing["items"][0]["website"] is None


async def test_commit_upserts_on_the_natural_key(client_for) -> None:
    t = await make_tenant("impex-commit")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.post(
            "/api/v1/companies",
            json={"name": "Existing", "notes": "keep me"},
            headers=headers,
        )
        content = _csv_bytes(
            ["name", "website", "status"],
            [["Existing", "https://updated.test", "onboarding"], ["Newco", "", "lead"]],
        )
        r = await c.post(
            "/api/v1/impex/company/import",
            params={"dry_run": "false"},
            files=_file(content),
            headers=headers,
        )
        report = r.json()
        assert report["applied"] is True
        assert (report["creates"], report["updates"]) == (1, 1)

        listing = (await c.get("/api/v1/companies", headers=headers)).json()
        by_name = {i["name"]: i for i in listing["items"]}
        assert listing["total"] == 2
        assert by_name["Existing"]["website"] == "https://updated.test"
        assert by_name["Existing"]["status"] == "onboarding"
        # A column absent from the file is never touched.
        assert by_name["Existing"]["notes"] == "keep me"
        assert by_name["Newco"]["status"] == "lead"


async def test_client_number_outranks_name_as_the_match_key(client_for) -> None:
    """A renamed client re-imports onto itself, because the number is the stabler key."""
    t = await make_tenant("impex-nk-order")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        created = (
            await c.post(
                "/api/v1/companies",
                json={"name": "Oude Naam", "client_number": "K001"},
                headers=headers,
            )
        ).json()

        # Same number, new name: an update, not a second company.
        content = _csv_bytes(
            ["client_number", "name", "city"], [["K001", "Nieuwe Naam", "Breda"]]
        )
        report = (
            await c.post(
                "/api/v1/impex/company/import",
                params={"dry_run": "false"},
                files=_file(content),
                headers=headers,
            )
        ).json()
        assert (report["creates"], report["updates"]) == (0, 1)

        listing = (await c.get("/api/v1/companies", headers=headers)).json()
        assert listing["total"] == 1
        assert listing["items"][0]["id"] == created["id"]
        assert listing["items"][0]["name"] == "Nieuwe Naam"
        assert listing["items"][0]["city"] == "Breda"

        # A file with no number column still falls back to matching on the name.
        report = (
            await c.post(
                "/api/v1/impex/company/import",
                params={"dry_run": "false"},
                files=_file(_csv_bytes(["name", "city"], [["Nieuwe Naam", "Tilburg"]])),
                headers=headers,
            )
        ).json()
        assert (report["creates"], report["updates"]) == (0, 1)


async def test_two_rows_reaching_one_company_by_different_keys_is_a_duplicate(
    client_for,
) -> None:
    """Per-key dedup cannot see this: different buckets, same company.

    Without the resolved-target check the later row silently overwrites what the earlier one
    just imported, and the report cheerfully calls it two updates.
    """
    t = await make_tenant("impex-nk-cross")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.post(
            "/api/v1/companies",
            json={"name": "Acme", "client_number": "K001"},
            headers=headers,
        )
        content = _csv_bytes(
            ["client_number", "name", "city"],
            [["K001", "Acme", "Breda"], ["", "Acme", "Tilburg"]],
        )
        report = (
            await c.post(
                "/api/v1/impex/company/import",
                params={"dry_run": "false"},
                files=_file(content),
                headers=headers,
            )
        ).json()
        assert report["applied"] is False
        assert [(e["row"], e["message_key"]) for e in report["errors"]] == [
            (2, "impex.errors.duplicate_in_file")
        ]
        listing = (await c.get("/api/v1/companies", headers=headers)).json()
        assert listing["items"][0]["city"] is None  # nothing was written


async def test_national_phone_numbers_import_using_the_org_country(client_for) -> None:
    """No real client list writes +31; rejecting the file over that would be the bug."""
    t = await make_tenant("impex-phone")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        content = _csv_bytes(
            ["name", "phone", "country"],
            [
                ["Dutch BV", "0612345678", ""],          # national → org default (NL)
                ["Belgian BV", "0475 12 34 56", "BE"],   # national → the row's own country
                ["Intl BV", "+3120 624 1111", ""],       # already international, untouched
            ],
        )
        r = await c.post(
            "/api/v1/impex/company/import",
            params={"dry_run": "false"},
            files=_file(content),
            headers=headers,
        )
        report = r.json()
        assert report["errors"] == []
        assert report["applied"] is True

        listing = (await c.get("/api/v1/companies", headers=headers)).json()
        phones = {i["name"]: i["phone"] for i in listing["items"]}
        assert phones["Dutch BV"] == "+31612345678"
        # The row's own country wins over the org's — a Belgian client stays Belgian.
        assert phones["Belgian BV"] == "+32475123456"
        assert phones["Intl BV"] == "+31206241111"


async def test_one_bad_phone_is_a_row_error_and_the_blanks_are_not(client_for) -> None:
    """The reported file's shape (issue #289): valid international numbers, blank cells, and
    a single number one digit short.

    Phone validation used to live only in the service, which runs *after* the report is built
    — so the preview said the file was clean and the commit came back as a request-level 422
    naming no row. The user then went looking at the 19 empty cells and the 85 good numbers.
    """
    t = await make_tenant("impex-phone-bad")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        rows = [
            ["Acme", "+31 6 1234 5678"],
            ["Blank BV", ""],           # a true blank
            ["Spaces BV", "   "],       # whitespace only
            ["Short BV", "+3161234567"],  # a digit short — the only real problem
            ["Beta", "+31 20 624 1111"],
        ]
        content = _csv_bytes(["name", "phone"], rows)
        preview = (
            await c.post(
                "/api/v1/impex/company/import", files=_file(content), headers=headers
            )
        ).json()
        # The dry run names the row and the column, and blames nothing else.
        assert preview["errors"] == [
            {"row": 4, "field": "phone", "message_key": "errors.invalid_phone"}
        ]
        assert preview["error_count"] == 1

        # And a commit of the same file is refused as a whole, writing nothing.
        commit = (
            await c.post(
                "/api/v1/impex/company/import",
                params={"dry_run": "false"},
                files=_file(content),
                headers=headers,
            )
        ).json()
        assert commit["applied"] is False
        assert (await c.get("/api/v1/companies", headers=headers)).json()["total"] == 0

        # Corrected, the very same file imports — blanks and all.
        rows[3][1] = "+31612345670"
        fixed = (
            await c.post(
                "/api/v1/impex/company/import",
                params={"dry_run": "false"},
                files=_file(_csv_bytes(["name", "phone"], rows)),
                headers=headers,
            )
        ).json()
        assert (fixed["errors"], fixed["applied"]) == ([], True)
        listing = (await c.get("/api/v1/companies", headers=headers)).json()
        phones = {i["name"]: i["phone"] for i in listing["items"]}
        assert phones == {
            "Acme": "+31612345678",
            "Blank BV": None,       # an empty optional cell clears, never rejects
            "Spaces BV": None,      # and whitespace is an empty cell
            "Short BV": "+31612345670",
            "Beta": "+31206241111",
        }


async def test_an_xlsx_reports_a_phone_exactly_as_the_same_csv_does(client_for) -> None:
    """The reported file was a workbook, and Excel is where the ambiguity lives: a blank cell,
    a row that ends early and a formatted-but-empty cell all have to mean the same thing they
    mean in a CSV, and a bad number has to name the same data row (issue #289)."""
    t = await make_tenant("impex-phone-xlsx")
    headers = await auth_cookie(t.user)
    header = ["name", "phone"]
    rows = [
        ["Acme", "0612345678"],
        ["Trailing BV"],            # the row simply ends — no phone cell at all
        ["Blank BV", ""],
        ["Short BV", "+3161234567"],  # a digit short, on data row 4 either way
    ]

    async with client_for(t.host) as c:
        for source, content in (
            ("import.csv", _csv_bytes(header, rows)),
            ("import.xlsx", _xlsx_bytes(header, rows)),
        ):
            report = (
                await c.post(
                    "/api/v1/impex/company/import",
                    files={"file": (source, content, "application/octet-stream")},
                    headers=headers,
                )
            ).json()
            assert report["errors"] == [
                {"row": 4, "field": "phone", "message_key": "errors.invalid_phone"}
            ], source

        rows[3][1] = "+31612345670"
        for source, content in (
            ("import.csv", _csv_bytes(header, rows)),
            ("import.xlsx", _xlsx_bytes(header, rows)),
        ):
            report = (
                await c.post(
                    "/api/v1/impex/company/import",
                    params={"dry_run": "false"},
                    files={"file": (source, content, "application/octet-stream")},
                    headers=headers,
                )
            ).json()
            assert (report["errors"], report["applied"]) == ([], True), source
            listing = (await c.get("/api/v1/companies", headers=headers)).json()
            phones = {i["name"]: i["phone"] for i in listing["items"]}
            assert phones == {
                "Acme": "+31612345678",
                "Trailing BV": None,
                "Blank BV": None,
                "Short BV": "+31612345670",
            }, source


async def test_contact_phone_columns_report_their_own_row_and_field(client_for) -> None:
    """Both places a contact's number reaches the importer: its own entity, and the column
    contacts contributes to the company import (§17)."""
    t = await make_tenant("impex-phone-contact")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        report = (
            await c.post(
                "/api/v1/impex/contact/import",
                files=_file(
                    _csv_bytes(
                        ["first_name", "email", "phone"],
                        [
                            ["Ann", "ann@x.nl", "0612345678"],
                            ["Bob", "bob@x.nl", ""],
                            ["Cee", "cee@x.nl", "0612"],
                        ],
                    )
                ),
                headers=headers,
            )
        ).json()
        assert report["errors"] == [
            {"row": 3, "field": "phone", "message_key": "errors.invalid_phone"}
        ]

        # The contributed column blames itself, not the company's own phone column.
        report = (
            await c.post(
                "/api/v1/impex/company/import",
                files=_file(
                    _csv_bytes(
                        ["name", "phone", "contact_first_name", "contact_phone"],
                        [["Acme", "0612345678", "Ann", "06123"]],
                    )
                ),
                headers=headers,
            )
        ).json()
        assert report["errors"] == [
            {"row": 1, "field": "contact_phone", "message_key": "errors.invalid_phone"}
        ]

        # Valid, it lands on the contact as E.164 — the preview and the write agree.
        assert (
            await c.post(
                "/api/v1/impex/company/import",
                params={"dry_run": "false"},
                files=_file(
                    _csv_bytes(
                        ["name", "phone", "contact_first_name", "contact_phone"],
                        [["Acme", "0612345678", "Ann", "0612345679"]],
                    )
                ),
                headers=headers,
            )
        ).json()["applied"] is True
        contacts = (await c.get("/api/v1/contacts", headers=headers)).json()
        assert [i["phone"] for i in contacts["items"]] == ["+31612345679"]


async def test_a_legacy_freeform_phone_still_round_trips(client_for) -> None:
    """Rows predating validation (issue #256) hold freeform strings, and their own service
    revalidates a phone only when it *changes*. An export→import round-trip must inherit that
    exactly: the preview is a pre-check of the write, never a stricter gate of its own."""
    t = await make_tenant("impex-phone-legacy")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Oud BV"}, headers=headers)
        ).json()
        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            await session.execute(
                text("UPDATE companies SET phone = :phone WHERE id = :id"),
                {"phone": "010-1234567 (privé)", "id": company["id"]},
            )
            await session.commit()

        export = (await c.get("/api/v1/impex/company/export", headers=headers)).content
        report = (
            await c.post(
                "/api/v1/impex/company/import",
                params={"dry_run": "false"},
                files={"file": ("export.csv", export, "text/csv")},
                headers=headers,
            )
        ).json()
        assert (report["errors"], report["updates"]) == ([], 1)
        listing = (await c.get("/api/v1/companies", headers=headers)).json()
        assert listing["items"][0]["phone"] == "010-1234567 (privé)"

        # Editing it, though, does have to be a valid number.
        report = (
            await c.post(
                "/api/v1/impex/company/import",
                files=_file(_csv_bytes(["name", "phone"], [["Oud BV", "010-12345"]])),
                headers=headers,
            )
        ).json()
        assert report["errors"] == [
            {"row": 1, "field": "phone", "message_key": "errors.invalid_phone"}
        ]


async def test_commit_with_errors_applies_nothing(client_for) -> None:
    """dry_run=false is all-or-nothing: one bad row keeps every good row out."""
    t = await make_tenant("impex-atomic")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        content = _csv_bytes(["name", "status"], [["Good", "active"], ["Bad", "bogus"]])
        r = await c.post(
            "/api/v1/impex/company/import",
            params={"dry_run": "false"},
            files=_file(content),
            headers=headers,
        )
        report = r.json()
        assert report["applied"] is False
        assert report["errors"] == [
            {"row": 2, "field": "status", "message_key": "impex.errors.invalid_option"}
        ]
        assert (await c.get("/api/v1/companies", headers=headers)).json()["total"] == 0


async def test_unknown_and_missing_columns_are_header_errors(client_for) -> None:
    t = await make_tenant("impex-header")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        r = await c.post(
            "/api/v1/impex/company/import",
            files=_file(_csv_bytes(["naam", "website"], [["Acme", ""]])),
            headers=headers,
        )
        report = r.json()
        assert report["creates"] == 0
        assert {(e["row"], e["field"], e["message_key"]) for e in report["errors"]} == {
            (0, "naam", "impex.errors.unknown_column"),
            (0, "name", "impex.errors.missing_column"),
        }


async def test_duplicate_and_ambiguous_natural_keys(client_for) -> None:
    t = await make_tenant("impex-dup")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        # Two existing companies may share a name; an import row naming it must not pick one.
        for _ in range(2):
            await c.post("/api/v1/companies", json={"name": "Twin"}, headers=headers)
        content = _csv_bytes(["name"], [["Solo"], ["Solo"], ["Twin"]])
        report = (
            await c.post(
                "/api/v1/impex/company/import", files=_file(content), headers=headers
            )
        ).json()
        assert report["creates"] == 1  # the first "Solo"
        assert {(e["row"], e["message_key"]) for e in report["errors"]} == {
            (2, "impex.errors.duplicate_in_file"),
            (3, "impex.errors.ambiguous_match"),
        }


# --------------------------------------------------------------------------- #
# FK resolution (contacts → company)
# --------------------------------------------------------------------------- #
async def test_contact_company_resolves_by_name_and_uuid(client_for) -> None:
    t = await make_tenant("impex-fk")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        acme = (
            await c.post("/api/v1/companies", json={"name": "Acme"}, headers=headers)
        ).json()
        beta = (
            await c.post("/api/v1/companies", json={"name": "Beta"}, headers=headers)
        ).json()

        content = _csv_bytes(
            ["first_name", "email", "company"],
            [
                ["Ann", "ann@x.nl", "Acme"],       # by exact name
                ["Bob", "bob@x.nl", beta["id"]],   # by UUID
            ],
        )
        report = (
            await c.post(
                "/api/v1/impex/contact/import",
                params={"dry_run": "false"},
                files=_file(content),
                headers=headers,
            )
        ).json()
        assert report["applied"] is True
        assert report["creates"] == 2

        for company_id, first_name in ((acme["id"], "Ann"), (beta["id"], "Bob")):
            linked = (
                await c.get(
                    "/api/v1/contacts",
                    params={"company_id": company_id},
                    headers=headers,
                )
            ).json()
            assert [i["first_name"] for i in linked["items"]] == [first_name]


async def test_unresolved_company_is_a_row_error_never_an_orphan(client_for) -> None:
    t = await make_tenant("impex-fk-miss")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        content = _csv_bytes(
            ["first_name", "email", "company"], [["Cee", "cee@x.nl", "Ghost BV"]]
        )
        report = (
            await c.post(
                "/api/v1/impex/contact/import",
                params={"dry_run": "false"},
                files=_file(content),
                headers=headers,
            )
        ).json()
        assert report["applied"] is False
        assert report["errors"] == [
            {
                "row": 1,
                "field": "company",
                "message_key": "impex.errors.unresolved_reference",
            }
        ]
        assert (await c.get("/api/v1/contacts", headers=headers)).json()["total"] == 0


# --------------------------------------------------------------------------- #
# Custom fields (§13)
# --------------------------------------------------------------------------- #
async def test_required_custom_field_is_enforced_per_row(client_for) -> None:
    t = await make_tenant("impex-custom")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.post(
            "/api/v1/custom-fields/definitions",
            json={
                "entity_type": "company",
                "key": "vat",
                "label_i18n": {"nl": "BTW", "en": "VAT"},
                "data_type": "text",
                "required": True,
            },
            headers=headers,
        )

        # A file that omits the required custom column fails at the header.
        report = (
            await c.post(
                "/api/v1/impex/company/import",
                files=_file(_csv_bytes(["name"], [["Acme"]])),
                headers=headers,
            )
        ).json()
        assert report["errors"] == [
            {"row": 0, "field": "vat", "message_key": "impex.errors.missing_column"}
        ]

        # With the column present, an empty cell on a create is the usual required error.
        report = (
            await c.post(
                "/api/v1/impex/company/import",
                files=_file(_csv_bytes(["name", "vat"], [["Acme", ""], ["Beta", "NL02"]])),
                headers=headers,
            )
        ).json()
        assert report["creates"] == 1
        assert report["errors"] == [
            {"row": 1, "field": "vat", "message_key": "errors.required"}
        ]

        # And a valid commit stores the custom value through the §13 validator.
        report = (
            await c.post(
                "/api/v1/impex/company/import",
                params={"dry_run": "false"},
                files=_file(_csv_bytes(["name", "vat"], [["Beta", "NL02"]])),
                headers=headers,
            )
        ).json()
        assert report["applied"] is True
        listing = (await c.get("/api/v1/companies", headers=headers)).json()
        assert listing["items"][0]["custom"] == {"vat": "NL02"}


# --------------------------------------------------------------------------- #
# Tenant isolation (Golden Rule 1)
# --------------------------------------------------------------------------- #
async def test_import_and_export_never_cross_tenants(client_for) -> None:
    a = await make_tenant("impex-org-a")
    b = await make_tenant("impex-org-b")
    headers_a = await auth_cookie(a.user)
    headers_b = await auth_cookie(b.user)

    async with client_for(b.host) as cb:
        await cb.post(
            "/api/v1/companies",
            json={"name": "Shared", "website": "https://b.test"},
            headers=headers_b,
        )
        await cb.post("/api/v1/companies", json={"name": "B-only"}, headers=headers_b)

    async with client_for(a.host) as ca:
        await ca.post(
            "/api/v1/companies",
            json={"name": "Shared", "website": "https://a.test"},
            headers=headers_a,
        )
        # Importing "Shared" into A updates A's row — never B's, whatever the name says.
        report = (
            await ca.post(
                "/api/v1/impex/company/import",
                params={"dry_run": "false"},
                files=_file(
                    _csv_bytes(["name", "website"], [["Shared", "https://a2.test"]])
                ),
                headers=headers_a,
            )
        ).json()
        assert (report["creates"], report["updates"]) == (0, 1)

        # A contact import cannot resolve a company that only exists in B.
        report = (
            await ca.post(
                "/api/v1/impex/contact/import",
                files=_file(
                    _csv_bytes(["first_name", "company"], [["Eve", "B-only"]])
                ),
                headers=headers_a,
            )
        ).json()
        assert report["errors"][0]["message_key"] == "impex.errors.unresolved_reference"

        # And the export carries A's rows only.
        _, rows = _rows(
            (await ca.get("/api/v1/impex/company/export", headers=headers_a)).content
        )
        assert [row["name"] for row in rows] == ["Shared"]
        assert rows[0]["website"] == "https://a2.test"

    async with client_for(b.host) as cb:
        listing = (await cb.get("/api/v1/companies", headers=headers_b)).json()
        assert {i["name"]: i["website"] for i in listing["items"]} == {
            "Shared": "https://b.test",
            "B-only": None,
        }


# --------------------------------------------------------------------------- #
# The synchronous cap
# --------------------------------------------------------------------------- #
async def test_more_than_2000_rows_is_a_413(client_for) -> None:
    t = await make_tenant("impex-cap")
    headers = await auth_cookie(t.user)
    content = _csv_bytes(["name"], [[f"Bulk {i}"] for i in range(2001)])
    async with client_for(t.host) as c:
        r = await c.post(
            "/api/v1/impex/company/import",
            params={"dry_run": "false"},
            files=_file(content),
            headers=headers,
        )
        assert r.status_code == 413
        assert r.json()["error"]["message"] == "impex.errors.too_many_rows"
        assert (await c.get("/api/v1/companies", headers=headers)).json()["total"] == 0


# --------------------------------------------------------------------------- #
# Settings hub round: entities catalog + the four new descriptors
# --------------------------------------------------------------------------- #
#: Entities that travel out by spreadsheet and never back in (``importable=False``).
EXPORT_ONLY = {"uptime_monitor"}


async def test_entities_catalog_lists_all_descriptors(client_for) -> None:
    t = await make_tenant("impex-cat")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        entities = {
            e["entity_type"]: e
            for e in (await c.get("/api/v1/impex/entities", headers=headers)).json()
        }
    assert set(entities) >= {
        "company", "contact", "project", "task", "time_entry", "subscription",
    }
    # Export-only is a supported state, not a defect (CLAUDE.md §17): a monitor is created
    # against the checking service and a row in a spreadsheet cannot make one exist, exactly
    # as an approval-bearing record must be requested rather than bulk-written. So the set is
    # named rather than waved through — a descriptor that becomes export-only by accident
    # still trips this, and a new deliberate one is one line and a reason.
    assert {k for k, e in entities.items() if not e["importable"]} == EXPORT_ONLY
    assert all(e["importable"] for k, e in entities.items() if k not in EXPORT_ONLY)
    assert entities["time_entry"]["read_permission"] == "time.entry.read"
    # The upsert keys the wizard offers as "match existing rows on", in priority order.
    assert entities["company"]["natural_keys"] == ["client_number", "name"]
    assert entities["contact"]["natural_keys"] == ["email"]
    # Create-only entities advertise no key at all, rather than a misleading one.
    assert entities["task"]["natural_keys"] == []
    assert entities["time_entry"]["natural_keys"] == []


async def test_project_import_upserts_and_resolves_company(client_for) -> None:
    t = await make_tenant("impex-proj")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.post("/api/v1/companies", json={"name": "Klant BV"}, headers=headers)
        header = ["name", "company", "status", "budget_hours", "start_date", "billable_default"]
        created = await c.post(
            "/api/v1/impex/project/import?dry_run=false",
            files=_file(_csv_bytes(header, [
                ["Website", "Klant BV", "active", "40,5", "2026-01-01", "true"],
            ])),
            headers=headers,
        )
        assert created.status_code == 200, created.text
        assert created.json()["creates"] == 1 and created.json()["applied"] is True

        # Same name again → an update, not a duplicate; a bad date is a row error.
        updated = await c.post(
            "/api/v1/impex/project/import?dry_run=false",
            files=_file(_csv_bytes(header, [
                ["Website", "Klant BV", "on_hold", "60", "2026-02-01", "false"],
            ])),
            headers=headers,
        )
        assert updated.json()["updates"] == 1

        bad = await c.post(
            "/api/v1/impex/project/import",
            files=_file(_csv_bytes(header, [
                ["X", "Klant BV", "active", "1", "01-02-2026", "true"],
            ])),
            headers=headers,
        )
        assert bad.json()["errors"][0]["message_key"] == "impex.errors.invalid_date"

        exported = await c.get("/api/v1/impex/project/export", headers=headers)
        _, rows = _rows(exported.content)
        assert rows[0]["name"] == "Website" and rows[0]["company"] == "Klant BV"
        assert rows[0]["status"] == "on_hold" and float(rows[0]["budget_hours"]) == 60.0


async def test_task_import_is_create_only(client_for) -> None:
    t = await make_tenant("impex-task")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        header = ["title", "priority", "assignee", "due_date"]
        first = await c.post(
            "/api/v1/impex/task/import?dry_run=false",
            files=_file(_csv_bytes(header, [
                ["Bellen", "high", t.user.email, "2026-08-01"],
                ["Bellen", "low", "", "2026-08-02"],
            ])),
            headers=headers,
        )
        assert first.status_code == 200, first.text
        # Two rows with the same title both create — tasks have no natural key.
        assert first.json()["creates"] == 2 and first.json()["updates"] == 0

        tasks = (await c.get("/api/v1/tasks?limit=50&offset=0", headers=headers)).json()
        titles = [item["title"] for item in tasks["items"]]
        assert titles.count("Bellen") == 2

        # An unknown assignee is a row error, never a silent orphan.
        bad = await c.post(
            "/api/v1/impex/task/import",
            files=_file(_csv_bytes(header, [["X", "normal", "ghost@niet.nl", "2026-08-01"]])),
            headers=headers,
        )
        assert bad.json()["errors"][0]["message_key"] == "impex.errors.unresolved_reference"

        # A deadline is required (#392), and an import says so **per row** rather than as a
        # request-level 422 the report cannot point at (CLAUDE.md §17, #289).
        undated = await c.post(
            "/api/v1/impex/task/import",
            files=_file(_csv_bytes(header, [["Zonder datum", "normal", "", ""]])),
            headers=headers,
        )
        assert undated.status_code == 200, undated.text
        assert ("due_date", "errors.required") in [
            (e["field"], e["message_key"]) for e in undated.json()["errors"]
        ]

        # …and a file that never mentions the column at all is refused before any row is read.
        missing = await c.post(
            "/api/v1/impex/task/import",
            files=_file(_csv_bytes(["title", "priority"], [["Zonder kolom", "normal"]])),
            headers=headers,
        )
        assert missing.json()["errors"], missing.text


async def test_time_entry_round_trip_with_readonly_columns(client_for) -> None:
    t = await make_tenant("impex-time")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.post("/api/v1/companies", json={"name": "Uren BV"}, headers=headers)
        header = [
            "date", "start", "end", "minutes", "break_minutes",
            "company", "description", "billable",
        ]
        created = await c.post(
            "/api/v1/impex/time_entry/import?dry_run=false",
            files=_file(_csv_bytes(header, [
                ["2026-07-06", "09:00", "17:00", "", "30", "Uren BV", "Bouw", "true"],
                # No end time: the 90-minute duration drives the derived end (the form's rule).
                ["2026-07-07", "13:15", "", "90", "", "", "Los werk", "false"],
            ])),
            headers=headers,
        )
        assert created.status_code == 200, created.text
        assert created.json()["creates"] == 2

        exported = await c.get("/api/v1/impex/time_entry/export", headers=headers)
        header_out, rows = _rows(exported.content)
        # Readonly derived columns ride along on export…
        assert {"user", "approved", "invoiced", "minutes"} <= set(header_out)
        by_desc = {r["description"]: r for r in rows}
        assert by_desc["Bouw"]["minutes"] == "450"  # 8h − 30m break
        assert by_desc["Bouw"]["user"] == t.user.email
        assert by_desc["Bouw"]["approved"] == "false"

        # …and a re-import of the export is accepted (readonly cells ignored, rows created).
        again = await c.post(
            "/api/v1/impex/time_entry/import",
            files=_file(exported.content),
            headers=headers,
        )
        assert again.status_code == 200, again.text
        assert again.json()["error_count"] == 0
        assert again.json()["creates"] == 2

        bad = await c.post(
            "/api/v1/impex/time_entry/import",
            files=_file(_csv_bytes(header, [["2026-07-08", "9u30", "", "", "", "", "", ""]])),
            headers=headers,
        )
        assert bad.json()["errors"][0]["message_key"] == "impex.errors.invalid_time"


async def test_subscription_import_creates_and_updates(client_for) -> None:
    t = await make_tenant("impex-sub")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.post("/api/v1/companies", json={"name": "Retainer BV"}, headers=headers)
        header = ["name", "company", "status", "interval", "start_date", "amount", "included_hours"]
        created = await c.post(
            "/api/v1/impex/subscription/import?dry_run=false",
            files=_file(_csv_bytes(header, [
                ["SLA Goud", "Retainer BV", "active", "monthly", "2026-01-01", "500", "10"],
            ])),
            headers=headers,
        )
        assert created.status_code == 200, created.text
        assert created.json()["creates"] == 1

        updated = await c.post(
            "/api/v1/impex/subscription/import?dry_run=false",
            files=_file(_csv_bytes(header, [
                ["SLA Goud", "Retainer BV", "active", "monthly", "2026-01-01", "550", "12"],
            ])),
            headers=headers,
        )
        assert updated.json()["updates"] == 1

        subs = (await c.get("/api/v1/subscriptions", headers=headers)).json()["items"]
        assert subs[0]["amount"] == "550.00" or float(subs[0]["amount"]) == 550.0
