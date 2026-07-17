"""CSV import/export (issue #77): round-trip, RBAC gates, upsert, FK resolution, custom
fields, tenant isolation, and the synchronous row cap."""

from __future__ import annotations

import csv
import io

from sqlalchemy import text

from app.db import async_session_maker, set_current_org
from tests.conftest import auth_cookie, make_tenant

COMPANY_HEADER = [
    "name", "website", "invoice_email", "status",
    # Billing identity (issue #11, shipped with invoicing #207).
    "vat_number", "coc_number", "address_line1", "address_line2",
    "postal_code", "city", "country",
    "notes",
]


def _csv_bytes(header: list[str], rows: list[list[str]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


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
async def test_member_can_export_but_not_import(client_for) -> None:
    # The system member role holds companies.company.read but not .write.
    t = await make_tenant("impex-member", role="member")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        assert (
            await c.get("/api/v1/impex/company/export", headers=headers)
        ).status_code == 200
        r = await c.post(
            "/api/v1/impex/company/import",
            files=_file(_csv_bytes(["name"], [["Nope"]])),
            headers=headers,
        )
        assert r.status_code == 403
        assert r.json()["error"]["message"] == "errors.forbidden"


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
    assert all(e["importable"] for e in entities.values())
    assert entities["time_entry"]["read_permission"] == "time.entry.read"


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
                ["Bellen", "low", "", ""],
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
            files=_file(_csv_bytes(header, [["X", "normal", "ghost@niet.nl", ""]])),
            headers=headers,
        )
        assert bad.json()["errors"][0]["message_key"] == "impex.errors.unresolved_reference"


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
