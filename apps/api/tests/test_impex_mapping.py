"""Column mapping, the column catalog and contributed columns (issue #77).

The engine's other tests exercise a file whose header *is* the mapping. These cover the
opposite contract: an arbitrary spreadsheet whose columns are named whatever the previous
system called them, mapped explicitly by position.
"""

from __future__ import annotations

import csv
import io
import json

from sqlalchemy import text

from app.db import async_session_maker, set_current_org
from tests.conftest import auth_cookie, make_tenant


def _csv(rows: list[list[str]]) -> bytes:
    buffer = io.StringIO()
    csv.writer(buffer).writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _file(content: bytes, name: str = "klanten.csv") -> dict:
    return {"file": (name, content, "text/csv")}


#: A file as an agency actually has it: Dutch headers, columns this system has no idea about,
#: and the client's contact person in the same row.
CLIENT_LIST = _csv(
    [
        ["Klantnummer", "Bedrijfsnaam", "Plaats", "Omzet 2025", "Contactpersoon", "E-mail"],
        ["K-001", "Acme BV", "Utrecht", "120000", "Sanne", "sanne@acme.nl"],
        ["K-002", "Beta Systems", "Amsterdam", "80000", "Joris", "joris@beta.nl"],
    ]
)


async def _drop_permissions(org_id, keys: list[str]) -> None:
    """Take named permissions off every role in the org — a caller who simply lacks them."""
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        await session.execute(
            text("DELETE FROM role_permissions WHERE permission = ANY(:keys)"),
            {"keys": keys},
        )
        await session.commit()


# --------------------------------------------------------------------------- #
# The column catalog
# --------------------------------------------------------------------------- #
async def test_columns_lists_builtin_contributed_and_custom(client_for) -> None:
    t = await make_tenant("impex-cols")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        assert (
            await c.post(
                "/api/v1/custom-fields/definitions",
                json={
                    "entity_type": "company",
                    "key": "segment",
                    "label_i18n": {"nl": "Segment", "en": "Segment"},
                    "data_type": "text",
                },
                headers=headers,
            )
        ).status_code == 201

        body = (await c.get("/api/v1/impex/company/columns", headers=headers)).json()
        by_key = {column["key"]: column for column in body["columns"]}

        assert body["natural_keys"] == ["client_number", "name"]
        assert by_key["name"]["source"] == "builtin"
        assert by_key["name"]["required"] is True
        assert by_key["name"]["natural_key"] is True
        assert "bedrijfsnaam" in by_key["name"]["aliases"]

        # Contributed by another module, and it says which one — the UI groups on it.
        assert by_key["contact_email"]["source"] == "extension"
        assert by_key["contact_email"]["module"] == "contacts"
        # A contributed column may never be required (asserted at mount time too).
        assert by_key["contact_email"]["required"] is False

        # Tenant data: the raw per-locale labels, for the client to resolve. The API does not
        # pick a locale for someone else's content.
        assert by_key["segment"]["source"] == "custom"
        assert by_key["segment"]["label_i18n"] == {"nl": "Segment", "en": "Segment"}

        assert "lead" in by_key["status"]["options"]


async def test_columns_never_leak_another_tenants_custom_fields(client_for) -> None:
    a = await make_tenant("impex-cols-a")
    b = await make_tenant("impex-cols-b")
    async with client_for(b.host) as c:
        assert (
            await c.post(
                "/api/v1/custom-fields/definitions",
                json={
                    "entity_type": "company",
                    "key": "b_only",
                    "label_i18n": {"nl": "B", "en": "B"},
                    "data_type": "text",
                },
                headers=await auth_cookie(b.user),
            )
        ).status_code == 201

    async with client_for(a.host) as c:
        body = (
            await c.get("/api/v1/impex/company/columns", headers=await auth_cookie(a.user))
        ).json()
        assert "b_only" not in {column["key"] for column in body["columns"]}


async def test_contributed_columns_are_gated_on_the_contributors_permission(client_for) -> None:
    """A caller who cannot write contacts never *sees* the contact columns — rather than
    discovering it as a 403 halfway through a commit that then rolls the whole file back."""
    t = await make_tenant("impex-cols-gate", role="admin")
    await _drop_permissions(t.org.id, ["contacts.link.write"])
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        body = (await c.get("/api/v1/impex/company/columns", headers=headers)).json()
        assert not [col for col in body["columns"] if col["source"] == "extension"]

        # The export header follows the same rule, by the same resolution.
        export = (await c.get("/api/v1/impex/company/export", headers=headers)).content
        assert "contact_email" not in export.decode("utf-8-sig").splitlines()[0]


# --------------------------------------------------------------------------- #
# Inspect
# --------------------------------------------------------------------------- #
async def test_inspect_reads_the_file_and_suggests_a_mapping(client_for) -> None:
    t = await make_tenant("impex-inspect")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        report = (
            await c.post(
                "/api/v1/impex/company/inspect", files=_file(CLIENT_LIST), headers=headers
            )
        ).json()

        assert report["source_format"] == "csv"
        assert report["delimiter"] == ","
        assert report["encoding"] == "utf-8-sig"
        assert report["rows"] == 2
        assert len(report["fingerprint"]) == 32

        columns = {column["index"]: column for column in report["columns"]}
        assert columns[0]["header"] == "Klantnummer"
        assert (columns[0]["suggested_key"], columns[0]["match"]) == ("client_number", "alias")
        assert columns[1]["suggested_key"] == "name"
        assert columns[2]["suggested_key"] == "city"
        assert columns[4]["suggested_key"] == "contact_first_name"
        # Nothing in this system is "Omzet 2025" — suggest nothing rather than something.
        assert columns[3]["suggested_key"] is None

        # Sample cells, so a wrong encoding or a shifted column is visible before any write.
        assert columns[1]["samples"] == ["Acme BV", "Beta Systems"]

        assert report["missing_required"] == []
        assert report["suggested_match_key"] == "client_number"


async def test_inspect_reports_a_required_column_nothing_matched(client_for) -> None:
    t = await make_tenant("impex-inspect-req")
    headers = await auth_cookie(t.user)
    source = _csv([["Plaats", "Omzet"], ["Utrecht", "12"]])
    async with client_for(t.host) as c:
        report = (
            await c.post(
                "/api/v1/impex/company/inspect", files=_file(source), headers=headers
            )
        ).json()
        assert report["missing_required"] == ["name"]
        assert report["suggested_match_key"] is None


async def test_inspect_writes_nothing(client_for) -> None:
    t = await make_tenant("impex-inspect-ro")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.post("/api/v1/impex/company/inspect", files=_file(CLIENT_LIST), headers=headers)
        listing = (await c.get("/api/v1/companies", headers=headers)).json()
        assert listing["items"] == []


# --------------------------------------------------------------------------- #
# Mapping
# --------------------------------------------------------------------------- #
async def test_a_mapped_import_skips_the_columns_it_was_not_told_about(client_for) -> None:
    """The direct inverse of the header path, where an unknown column is fatal. "Omzet 2025"
    is left unmapped and simply ignored — which is what makes an arbitrary file importable."""
    t = await make_tenant("impex-map")
    headers = await auth_cookie(t.user)
    mapping = {"0": "client_number", "1": "name", "2": "city"}
    async with client_for(t.host) as c:
        r = await c.post(
            "/api/v1/impex/company/import",
            params={"dry_run": "false"},
            files=_file(CLIENT_LIST),
            data={"mapping": json.dumps(mapping)},
            headers=headers,
        )
        assert r.status_code == 200
        report = r.json()
        assert (report["creates"], report["updates"], report["errors"]) == (2, 0, [])
        assert report["applied"] is True

        items = {row["name"]: row for row in (
            await c.get("/api/v1/companies", headers=headers)
        ).json()["items"]}
        assert items["Acme BV"]["client_number"] == "K-001"
        assert items["Acme BV"]["city"] == "Utrecht"

        # Re-importing the same file updates rather than duplicating: the klantnummer matched.
        again = (
            await c.post(
                "/api/v1/impex/company/import",
                params={"dry_run": "false"},
                files=_file(CLIENT_LIST),
                data={"mapping": json.dumps(mapping)},
                headers=headers,
            )
        ).json()
        assert (again["creates"], again["updates"]) == (0, 2)


async def test_the_same_file_without_a_mapping_is_unchanged_behaviour(client_for) -> None:
    """Aliases feed suggestions only. Without an explicit mapping the header must still be
    exact keys, so an export round-trips and a caller who automated against this is not moved
    under. "Klantnummer" is an alias of `client_number` and still an unknown column here."""
    t = await make_tenant("impex-nomap")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        report = (
            await c.post(
                "/api/v1/impex/company/import", files=_file(CLIENT_LIST), headers=headers
            )
        ).json()
        errors = {(e["row"], e["field"], e["message_key"]) for e in report["errors"]}
        assert (0, "Klantnummer", "impex.errors.unknown_column") in errors
        assert (0, "name", "impex.errors.missing_column") in errors
        assert report["applied"] is False


async def test_mapping_problems_are_header_errors_not_a_422(client_for) -> None:
    """All of them at once, in the preview — not one 422 per fix-and-retry."""
    t = await make_tenant("impex-map-err")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:

        async def report(mapping: dict[str, str]) -> dict:
            response = await c.post(
                "/api/v1/impex/company/import",
                files=_file(CLIENT_LIST),
                data={"mapping": json.dumps(mapping)},
                headers=headers,
            )
            assert response.status_code == 200
            return response.json()

        # Two file columns claiming one target — the second would silently win.
        duplicate = await report({"1": "name", "2": "name"})
        assert any(
            e["message_key"] == "impex.errors.duplicate_column" for e in duplicate["errors"]
        )

        # A required column nobody mapped.
        missing = await report({"2": "city"})
        assert (0, "name", "impex.errors.missing_column") in {
            (e["row"], e["field"], e["message_key"]) for e in missing["errors"]
        }

        # A target this entity does not have.
        unknown = await report({"1": "name", "3": "revenue"})
        assert any(
            e["message_key"] == "impex.errors.unknown_column" for e in unknown["errors"]
        )

        # A column index past the end of the file.
        out_of_range = await report({"1": "name", "99": "city"})
        assert any(
            e["message_key"] == "impex.errors.invalid_mapping" for e in out_of_range["errors"]
        )

        # Not even JSON.
        r = await c.post(
            "/api/v1/impex/company/import",
            files=_file(CLIENT_LIST),
            data={"mapping": "not json"},
            headers=headers,
        )
        assert r.json()["errors"][0]["message_key"] == "impex.errors.invalid_mapping"


async def test_match_key_forces_which_column_the_upsert_uses(client_for) -> None:
    t = await make_tenant("impex-matchkey")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        # An existing client whose number is *not* the one in the file.
        assert (
            await c.post(
                "/api/v1/companies",
                json={"name": "Acme BV", "client_number": "OLD-9"},
                headers=headers,
            )
        ).status_code == 201

        mapping = {"0": "client_number", "1": "name"}
        # By default the klantnummer wins and K-001 is a different client → a create.
        default = (
            await c.post(
                "/api/v1/impex/company/import",
                files=_file(CLIENT_LIST),
                data={"mapping": json.dumps(mapping)},
                headers=headers,
            )
        ).json()
        assert default["creates"] == 2

        # Told to match on the name, the same file updates Acme instead.
        forced = (
            await c.post(
                "/api/v1/impex/company/import",
                files=_file(CLIENT_LIST),
                data={"mapping": json.dumps(mapping), "match_key": "name"},
                headers=headers,
            )
        ).json()
        assert (forced["creates"], forced["updates"]) == (1, 1)


async def test_a_match_key_that_is_not_a_natural_key_is_refused(client_for) -> None:
    t = await make_tenant("impex-matchkey-bad")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        r = await c.post(
            "/api/v1/impex/company/import",
            files=_file(CLIENT_LIST),
            data={"mapping": json.dumps({"1": "name"}), "match_key": "city"},
            headers=headers,
        )
        assert r.status_code == 422
        assert r.json()["error"]["message"] == "impex.errors.invalid_match_key"


async def test_a_mapping_cannot_be_applied_to_a_different_file(client_for) -> None:
    """The mapping is positional: mapping one file and importing another writes the wrong
    columns into the right fields, with every row valid and every value wrong."""
    t = await make_tenant("impex-fingerprint")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        inspected = (
            await c.post(
                "/api/v1/impex/company/inspect", files=_file(CLIENT_LIST), headers=headers
            )
        ).json()

        other = _csv([["Naam", "Plaats"], ["Gamma", "Breda"]])
        r = await c.post(
            "/api/v1/impex/company/import",
            files=_file(other),
            data={
                "mapping": json.dumps({"0": "name"}),
                "fingerprint": inspected["fingerprint"],
            },
            headers=headers,
        )
        assert r.status_code == 409
        assert r.json()["error"]["message"] == "impex.errors.source_changed"

        # The right file, same fingerprint, goes through.
        ok = await c.post(
            "/api/v1/impex/company/import",
            params={"dry_run": "false"},
            files=_file(CLIENT_LIST),
            data={
                "mapping": json.dumps({"1": "name"}),
                "fingerprint": inspected["fingerprint"],
            },
            headers=headers,
        )
        assert ok.status_code == 200
        assert ok.json()["applied"] is True


# --------------------------------------------------------------------------- #
# The contributed contact columns
# --------------------------------------------------------------------------- #
async def test_a_client_row_brings_its_contact_person(client_for) -> None:
    t = await make_tenant("impex-contact-ext")
    headers = await auth_cookie(t.user)
    mapping = {
        "1": "name",
        "4": "contact_first_name",
        "5": "contact_email",
    }
    async with client_for(t.host) as c:
        report = (
            await c.post(
                "/api/v1/impex/company/import",
                params={"dry_run": "false"},
                files=_file(CLIENT_LIST),
                data={"mapping": json.dumps(mapping)},
                headers=headers,
            )
        ).json()
        assert (report["creates"], report["errors"]) == (2, [])

        company = next(
            row
            for row in (await c.get("/api/v1/companies", headers=headers)).json()["items"]
            if row["name"] == "Acme BV"
        )
        contacts = (
            await c.get(
                f"/api/v1/contacts?company_id={company['id']}", headers=headers
            )
        ).json()["items"]
        assert [(row["first_name"], row["email"]) for row in contacts] == [
            ("Sanne", "sanne@acme.nl")
        ]

        # The first contact of a company becomes its primary — the import never has to
        # decide who that is, and a later import never reassigns it.
        assert [
            (link["company_id"], link["is_primary"]) for link in contacts[0]["companies"]
        ] == [(company["id"], True)]


async def test_re_importing_updates_the_contact_and_never_demotes_the_primary(
    client_for,
) -> None:
    t = await make_tenant("impex-contact-again")
    headers = await auth_cookie(t.user)
    mapping = {"1": "name", "4": "contact_first_name", "5": "contact_email"}
    changed = _csv(
        [
            ["Klantnummer", "Bedrijfsnaam", "Plaats", "Omzet", "Contactpersoon", "E-mail"],
            ["K-001", "Acme BV", "Utrecht", "1", "Sanneke", "sanne@acme.nl"],
        ]
    )
    async with client_for(t.host) as c:
        for source in (CLIENT_LIST, changed):
            r = await c.post(
                "/api/v1/impex/company/import",
                params={"dry_run": "false"},
                files=_file(source),
                data={"mapping": json.dumps({**mapping, "0": "client_number"})},
                headers=headers,
            )
            assert r.status_code == 200, r.text
            assert r.json()["applied"] is True

        # Matched on e-mail: the person was renamed, not duplicated.
        contacts = (await c.get("/api/v1/contacts", headers=headers)).json()["items"]
        by_email = {row["email"]: row for row in contacts}
        assert by_email["sanne@acme.nl"]["first_name"] == "Sanneke"
        assert len(contacts) == 2  # Sanne(ke) and Joris, once each


async def test_a_client_list_without_contact_emails_does_not_duplicate_on_re_import(
    client_for,
) -> None:
    """A list carrying contact *names* but no addresses has no org-wide key to match on, so
    without a per-company name fallback every import grows a fresh copy of every contact.
    Found in a browser run, not by the suite."""
    t = await make_tenant("impex-contact-nomail")
    headers = await auth_cookie(t.user)
    source = _csv(
        [["name", "contact_first_name", "contact_last_name"], ["Acme BV", "Sanne", "Jansen"]]
    )
    mapping = json.dumps(
        {"0": "name", "1": "contact_first_name", "2": "contact_last_name"}
    )
    async with client_for(t.host) as c:
        for _ in range(3):
            r = await c.post(
                "/api/v1/impex/company/import",
                params={"dry_run": "false"},
                files=_file(source),
                data={"mapping": mapping},
                headers=headers,
            )
            assert r.json()["applied"] is True

        contacts = (await c.get("/api/v1/contacts", headers=headers)).json()["items"]
        assert [(row["first_name"], row["last_name"]) for row in contacts] == [
            ("Sanne", "Jansen")
        ]

        # And adding the addresses later fills them in on that same person.
        with_email = _csv(
            [
                ["name", "contact_first_name", "contact_last_name", "contact_email"],
                ["Acme BV", "Sanne", "Jansen", "sanne@acme.nl"],
            ]
        )
        assert (
            await c.post(
                "/api/v1/impex/company/import",
                params={"dry_run": "false"},
                files=_file(with_email),
                data={
                    "mapping": json.dumps(
                        {
                            "0": "name",
                            "1": "contact_first_name",
                            "2": "contact_last_name",
                            "3": "contact_email",
                        }
                    )
                },
                headers=headers,
            )
        ).json()["applied"] is True

        contacts = (await c.get("/api/v1/contacts", headers=headers)).json()["items"]
        assert [(row["first_name"], row["email"]) for row in contacts] == [
            ("Sanne", "sanne@acme.nl")
        ]


async def test_a_namesake_at_another_client_is_never_merged(client_for) -> None:
    """The name fallback is scoped to the host company on purpose: two people called Sanne at
    two clients are two people, and merging them is far worse than a duplicate."""
    t = await make_tenant("impex-contact-namesake")
    headers = await auth_cookie(t.user)
    source = _csv(
        [
            ["name", "contact_first_name"],
            ["Acme BV", "Sanne"],
            ["Beta Systems", "Sanne"],
        ]
    )
    async with client_for(t.host) as c:
        assert (
            await c.post(
                "/api/v1/impex/company/import",
                params={"dry_run": "false"},
                files=_file(source),
                data={"mapping": json.dumps({"0": "name", "1": "contact_first_name"})},
                headers=headers,
            )
        ).json()["applied"] is True

        contacts = (await c.get("/api/v1/contacts", headers=headers)).json()["items"]
        assert len(contacts) == 2
        assert {link["company_id"] for row in contacts for link in row["companies"]} == {
            row["id"] for row in (await c.get("/api/v1/companies", headers=headers)).json()["items"]
        }


async def test_a_caller_without_contacts_write_can_neither_see_nor_map_them(
    client_for,
) -> None:
    t = await make_tenant("impex-contact-denied", role="admin")
    await _drop_permissions(t.org.id, ["contacts.contact.write"])
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        body = (await c.get("/api/v1/impex/company/columns", headers=headers)).json()
        assert "contact_email" not in {column["key"] for column in body["columns"]}

        # And mapping onto one is an unknown column, not a silent write.
        report = (
            await c.post(
                "/api/v1/impex/company/import",
                files=_file(CLIENT_LIST),
                data={"mapping": json.dumps({"1": "name", "5": "contact_email"})},
                headers=headers,
            )
        ).json()
        assert any(
            e["message_key"] == "impex.errors.unknown_column" for e in report["errors"]
        )


async def test_an_empty_contact_column_never_clears_the_contact(client_for) -> None:
    """A *company* import has no standing to wipe a contact's phone number, and a
    round-tripped export must not either."""
    t = await make_tenant("impex-contact-keep")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        assert (
            await c.post(
                "/api/v1/impex/company/import",
                params={"dry_run": "false"},
                files=_file(
                    _csv(
                        [
                            ["name", "contact_first_name", "contact_email", "contact_phone"],
                            ["Acme BV", "Sanne", "sanne@acme.nl", "0612345678"],
                        ]
                    )
                ),
                data={
                    "mapping": json.dumps(
                        {
                            "0": "name",
                            "1": "contact_first_name",
                            "2": "contact_email",
                            "3": "contact_phone",
                        }
                    )
                },
                headers=headers,
            )
        ).json()["applied"] is True

        # The same client list, this time without a phone column filled in.
        r = await c.post(
            "/api/v1/impex/company/import",
            params={"dry_run": "false"},
            files=_file(
                _csv(
                    [
                        ["name", "contact_first_name", "contact_email", "contact_phone"],
                        ["Acme BV", "Sanne", "sanne@acme.nl", ""],
                    ]
                )
            ),
            data={
                "mapping": json.dumps(
                    {
                        "0": "name",
                        "1": "contact_first_name",
                        "2": "contact_email",
                        "3": "contact_phone",
                    }
                )
            },
            headers=headers,
        )
        assert r.json()["applied"] is True
        contact = (await c.get("/api/v1/contacts", headers=headers)).json()["items"][0]
        assert contact["phone"] == "+31612345678"


async def test_a_mapped_import_never_crosses_tenants(client_for) -> None:
    a = await make_tenant("impex-map-iso-a")
    b = await make_tenant("impex-map-iso-b")
    mapping = json.dumps({"0": "client_number", "1": "name"})
    async with client_for(b.host) as c:
        assert (
            await c.post(
                "/api/v1/companies",
                json={"name": "Acme BV", "client_number": "K-001"},
                headers=await auth_cookie(b.user),
            )
        ).status_code == 201

    async with client_for(a.host) as c:
        report = (
            await c.post(
                "/api/v1/impex/company/import",
                params={"dry_run": "false"},
                files=_file(CLIENT_LIST),
                data={"mapping": mapping},
                headers=await auth_cookie(a.user),
            )
        ).json()
        # B's identically-numbered client is invisible: the same klantnummer is free in A.
        assert (report["creates"], report["updates"]) == (2, 0)

    async with client_for(b.host) as c:
        items = (await c.get("/api/v1/companies", headers=await auth_cookie(b.user))).json()
        assert len(items["items"]) == 1
