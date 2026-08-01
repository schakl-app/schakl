"""Import/export for the web-asset and catalog entities (issue #77, second round).

Covers what the first round's entities did not exercise: a **party** cell (the four-way
"who's responsible" reference), a **reference used as the upsert key** (a website has no name
of its own), a kind-scoped **provider** reference, per-locale **label columns**, and the
create-only rate card whose idempotence comes from the service's own upsert rather than from
the engine's matching.
"""

from __future__ import annotations

import csv
import io

from tests.conftest import auth_cookie, make_tenant


def _csv_bytes(header: list[str], rows: list[list[str]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _rows(content: bytes) -> tuple[list[str], list[dict[str, str]]]:
    parsed = list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))
    return parsed[0], [dict(zip(parsed[0], row, strict=True)) for row in parsed[1:]]


def _file(content: bytes, name: str = "import.csv") -> dict:
    return {"file": (name, content, "text/csv")}


async def _company(c, headers, name: str) -> str:
    r = await c.post("/api/v1/companies", json={"name": name}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _import(c, headers, entity: str, content: bytes, *, commit: bool = True) -> dict:
    r = await c.post(
        f"/api/v1/impex/{entity}/import",
        params={"dry_run": "false" if commit else "true"},
        files=_file(content),
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------- #
# Domains
# --------------------------------------------------------------------------- #
async def test_domain_round_trip_and_upsert_on_name(client_for) -> None:
    t = await make_tenant("impex-dom")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _company(c, headers, "Acme")
        report = await _import(
            c,
            headers,
            "domain",
            _csv_bytes(
                ["name", "company", "status", "start_date"],
                [
                    ["example.nl", "Acme", "active", "2024-01-15"],
                    ["voorbeeld.be", "Acme", "parked", "2023-06-01"],
                ],
            ),
        )
        assert (report["creates"], report["updates"], report["error_count"]) == (2, 0, 0)

        r = await c.get("/api/v1/impex/domain/export", headers=headers)
        assert r.status_code == 200
        header, rows = _rows(r.content)
        by_name = {row["name"]: row for row in rows}
        assert set(by_name) == {"example.nl", "voorbeeld.be"}
        assert by_name["example.nl"]["company"] == "Acme"
        # Derived columns are exported so the file is worth reading, and are readonly.
        assert by_name["example.nl"]["tld"] == "nl"
        assert by_name["example.nl"]["next_invoice_date"]
        assert "registry_contact" in header

        # The export re-imports as pure updates — the round-trip rule, including the readonly
        # columns being accepted in the header and ignored.
        again = await _import(c, headers, "domain", r.content)
        assert (again["creates"], again["updates"], again["error_count"]) == (0, 2, 0)


async def test_domain_name_is_normalised_before_matching(client_for) -> None:
    """A pasted column of URLs matches the domains already on file.

    The normalisation lives in the module's own schema validator, so the import inherits it
    for free — which is the point of writing through the service rather than the table.
    """
    t = await make_tenant("impex-dom-norm")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _company(c, headers, "Acme")
        await _import(
            c,
            headers,
            "domain",
            _csv_bytes(
                ["name", "company", "start_date"], [["example.nl", "Acme", "2024-01-15"]]
            ),
        )
        report = await _import(
            c,
            headers,
            "domain",
            _csv_bytes(
                ["name", "company", "status"],
                [["https://WWW.Example.NL/pagina", "Acme", "parked"]],
            ),
        )
        assert (report["creates"], report["updates"]) == (0, 1)
        r = await c.get("/api/v1/domains", headers=headers)
        assert [d["status"] for d in r.json()["items"]] == ["parked"]


async def test_domain_party_and_provider_cells(client_for) -> None:
    """A party token and a kind-scoped provider reference both resolve, and round-trip."""
    t = await make_tenant("impex-dom-party")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _company(c, headers, "Acme")
        r = await c.post(
            "/api/v1/providers",
            json={"kind": "registrar", "name": "Openprovider"},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        # A provider of another kind sharing the name: the generic name resolver would call
        # this ambiguous, the kind-scoped one must not.
        assert (
            await c.post(
                "/api/v1/providers",
                json={"kind": "dns", "name": "Openprovider"},
                headers=headers,
            )
        ).status_code == 201

        report = await _import(
            c,
            headers,
            "domain",
            _csv_bytes(
                ["name", "company", "start_date", "registrar_provider", "registry_contact"],
                [
                    ["a.nl", "Acme", "2024-01-15", "Openprovider", "agency"],
                    ["b.nl", "Acme", "2024-01-15", "Openprovider", "company"],
                    ["c.nl", "Acme", "2024-01-15", "Openprovider", f"employee:{t.user.email}"],
                    ["d.nl", "Acme", "2024-01-15", "Openprovider", "company:Acme"],
                ],
            ),
        )
        assert (report["creates"], report["error_count"]) == (4, 0)

        header, rows = _rows(
            (await c.get("/api/v1/impex/domain/export", headers=headers)).content
        )
        by_name = {row["name"]: row for row in rows}
        assert by_name["a.nl"]["registry_contact"] == "agency"
        assert by_name["b.nl"]["registry_contact"] == "company"
        assert by_name["c.nl"]["registry_contact"] == f"employee:{t.user.email}"
        assert by_name["d.nl"]["registry_contact"] == "company:Acme"
        assert by_name["a.nl"]["registrar_provider"] == "Openprovider"


async def test_contact_party_round_trips_through_a_mixed_case_email(client_for) -> None:
    """``contacts.email`` is stored as typed, so a token that carries it must match
    case-insensitively — otherwise the export writes an address its own import cannot read."""
    t = await make_tenant("impex-party-case")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _company(c, headers, "Acme")
        r = await c.post(
            "/api/v1/contacts",
            json={"first_name": "Jan", "last_name": "Jansen", "email": "Info@Klant.NL"},
            headers=headers,
        )
        assert r.status_code == 201, r.text

        report = await _import(
            c,
            headers,
            "domain",
            _csv_bytes(
                ["name", "company", "start_date", "registry_contact"],
                [["a.nl", "Acme", "2024-01-15", "contact:info@klant.nl"]],
            ),
        )
        assert (report["creates"], report["error_count"]) == (1, 0)

        export = (await c.get("/api/v1/impex/domain/export", headers=headers)).content
        _, rows = _rows(export)
        assert rows[0]["registry_contact"] == "contact:Info@Klant.NL"
        # And the exported spelling imports straight back.
        again = await _import(c, headers, "domain", export)
        assert (again["updates"], again["error_count"]) == (1, 0)


async def test_bad_party_token_is_a_row_error_not_a_request_error(client_for) -> None:
    """§17: a check the row report cannot name is a check the preview does not have."""
    t = await make_tenant("impex-dom-badparty")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _company(c, headers, "Acme")
        report = await _import(
            c,
            headers,
            "domain",
            _csv_bytes(
                ["name", "company", "start_date", "registry_contact"],
                [
                    ["a.nl", "Acme", "2024-01-15", "jan@bureau.nl"],  # unprefixed: refused
                    ["b.nl", "Acme", "2024-01-15", "employee:nobody@nowhere.test"],
                    ["c.nl", "Acme", "2024-01-15", "agency"],
                ],
            ),
            commit=False,
        )
        assert report["error_count"] == 2
        errors = {e["row"]: (e["field"], e["message_key"]) for e in report["errors"]}
        assert errors[1] == ("registry_contact", "impex.errors.invalid_party")
        assert errors[2] == ("registry_contact", "impex.errors.unresolved_reference")
        assert report["applied"] is False


# --------------------------------------------------------------------------- #
# The TLD rate card
# --------------------------------------------------------------------------- #
async def test_tld_prices_import_is_idempotent_though_create_only(client_for) -> None:
    """No natural key, yet importing the same sheet twice leaves one row per (tld, date):
    ``set_tld_price`` corrects a same-day row in place, which is the whole upsert."""
    t = await make_tenant("impex-tld")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        content = _csv_bytes(
            ["tld", "amount", "valid_from"],
            [["nl", "12.50", "2025-01-01"], ["com", "14.00", "2025-01-01"]],
        )
        assert (await _import(c, headers, "domain_tld_price", content))["creates"] == 2
        await _import(
            c,
            headers,
            "domain_tld_price",
            _csv_bytes(["tld", "amount", "valid_from"], [["nl", "13.50", "2025-01-01"]]),
        )
        _, rows = _rows(
            (await c.get("/api/v1/impex/domain_tld_price/export", headers=headers)).content
        )
        by_tld = {row["tld"]: row for row in rows}
        assert len(rows) == 2  # corrected in place, not appended
        assert by_tld["nl"]["amount"] == "13.50"
        assert by_tld["nl"]["currency"] == "EUR"


async def test_tld_prices_need_the_manage_permission(client_for) -> None:
    t = await make_tenant("impex-tld-perm", role="admin")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        # An admin holds both the bulk gate and the catalog gate.
        assert (
            await c.get("/api/v1/impex/domain_tld_price/export", headers=headers)
        ).status_code == 200


# --------------------------------------------------------------------------- #
# Websites — a reference as the natural key
# --------------------------------------------------------------------------- #
async def test_website_upserts_on_its_domain(client_for) -> None:
    t = await make_tenant("impex-web")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _company(c, headers, "Acme")
        await _import(
            c,
            headers,
            "domain",
            _csv_bytes(
                ["name", "company", "start_date"],
                [["example.nl", "Acme", "2024-01-15"], ["tweede.nl", "Acme", "2024-01-15"]],
            ),
        )
        report = await _import(
            c,
            headers,
            "website",
            _csv_bytes(
                ["domain", "root", "uptime_enabled"],
                [["example.nl", "true", "true"], ["tweede.nl", "false", "false"]],
            ),
        )
        assert (report["creates"], report["error_count"]) == (2, 0)

        r = await c.get("/api/v1/impex/website/export", headers=headers)
        _, rows = _rows(r.content)
        by_domain = {row["domain"]: row for row in rows}
        assert by_domain["example.nl"]["uptime_enabled"] == "true"
        assert by_domain["example.nl"]["company"] == "Acme"

        # Re-importing the export is the case that fails loudly without the reference key:
        # every row would try to create a second website for a domain that already has one.
        again = await _import(c, headers, "website", r.content)
        assert (again["creates"], again["updates"], again["error_count"]) == (0, 2, 0)


async def test_website_unknown_domain_is_a_row_error(client_for) -> None:
    t = await make_tenant("impex-web-miss")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        report = await _import(
            c,
            headers,
            "website",
            _csv_bytes(["domain", "root"], [["nietbestaand.nl", "true"]]),
            commit=False,
        )
        assert report["error_count"] == 1
        assert report["errors"][0]["field"] == "domain"
        assert report["errors"][0]["message_key"] == "impex.errors.unresolved_reference"


# --------------------------------------------------------------------------- #
# Hosting
# --------------------------------------------------------------------------- #
async def test_hosting_round_trip_and_detachable_company(client_for) -> None:
    t = await make_tenant("impex-host")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _company(c, headers, "Acme")
        await _import(
            c,
            headers,
            "hosting",
            _csv_bytes(
                ["name", "company", "ip_address", "contact"],
                [["Server 1", "Acme", "10.0.0.1", "agency"], ["Gedeeld", "", "10.0.0.2", ""]],
            ),
        )
        _, rows = _rows((await c.get("/api/v1/impex/hosting/export", headers=headers)).content)
        by_name = {row["name"]: row for row in rows}
        assert by_name["Server 1"]["company"] == "Acme"
        assert by_name["Server 1"]["contact"] == "agency"
        assert by_name["Gedeeld"]["company"] == ""  # shared infrastructure: a real state

        # An emptied company cell really detaches — NULL here means "shared", not "missing".
        await _import(
            c, headers, "hosting", _csv_bytes(["name", "company"], [["Server 1", ""]])
        )
        _, rows = _rows((await c.get("/api/v1/impex/hosting/export", headers=headers)).content)
        assert {row["name"]: row["company"] for row in rows}["Server 1"] == ""


async def test_hosting_duplicate_name_is_ambiguous(client_for) -> None:
    """``hosting.name`` is not org-unique, so two matches error rather than pick one."""
    t = await make_tenant("impex-host-dup")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        for _ in range(2):
            assert (
                await c.post("/api/v1/hosting", json={"name": "Server 1"}, headers=headers)
            ).status_code == 201
        report = await _import(
            c,
            headers,
            "hosting",
            _csv_bytes(["name", "ip_address"], [["Server 1", "10.0.0.9"]]),
            commit=False,
        )
        assert report["error_count"] == 1
        assert report["errors"][0]["message_key"] == "impex.errors.ambiguous_match"


# --------------------------------------------------------------------------- #
# The subscription catalogs
# --------------------------------------------------------------------------- #
async def test_subscription_type_labels_are_one_column_per_locale(client_for) -> None:
    t = await make_tenant("impex-subtype")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        r = await c.get("/api/v1/impex/subscription_type/columns", headers=headers)
        assert r.status_code == 200
        keys = [column["key"] for column in r.json()["columns"]]
        assert {"key", "label_nl", "label_en", "position", "active"} <= set(keys)

        await _import(
            c,
            headers,
            "subscription_type",
            _csv_bytes(
                ["key", "label_nl", "label_en", "position", "active"],
                [
                    ["seo", "SEO", "SEO", "10", "true"],
                    ["support", "Support", "Support", "20", "false"],
                ],
            ),
        )
        _, rows = _rows(
            (await c.get("/api/v1/impex/subscription_type/export", headers=headers)).content
        )
        by_key = {row["key"]: row for row in rows}
        assert by_key["seo"]["label_nl"] == "SEO"
        # include_inactive: an export that dropped deactivated rows would re-import as a
        # request to delete them.
        assert by_key["support"]["active"] == "false"


async def test_subscription_type_partial_label_file_keeps_the_other_locale(client_for) -> None:
    t = await make_tenant("impex-subtype-partial")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _import(
            c,
            headers,
            "subscription_type",
            _csv_bytes(["key", "label_nl", "label_en"], [["seo", "SEO NL", "SEO EN"]]),
        )
        await _import(
            c,
            headers,
            "subscription_type",
            _csv_bytes(["key", "label_nl"], [["seo", "Zoekmachine"]]),
        )
        _, rows = _rows(
            (await c.get("/api/v1/impex/subscription_type/export", headers=headers)).content
        )
        row = {r["key"]: r for r in rows}["seo"]
        assert (row["label_nl"], row["label_en"]) == ("Zoekmachine", "SEO EN")


async def test_subscription_template_and_the_new_agreement_columns(client_for) -> None:
    """The preset round-trips, and an agreement now carries what it was missing: its
    currency, its ``interval_count``, its notice period, its rollover rule and its preset."""
    t = await make_tenant("impex-subtpl")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _company(c, headers, "Acme")
        await _import(
            c,
            headers,
            "subscription_template",
            _csv_bytes(
                [
                    "name", "interval", "interval_count", "amount", "currency",
                    "included_hours", "rollover_mode", "rollover_expires_after_periods",
                    "notice_period_days", "position",
                ],
                [["Hosting Basis", "monthly", "2", "25.00", "EUR", "1", "carry", "3", "30", "5"]],
            ),
        )
        _, rows = _rows(
            (
                await c.get("/api/v1/impex/subscription_template/export", headers=headers)
            ).content
        )
        template = rows[0]
        assert template["interval_count"] == "2"
        assert template["rollover_mode"] == "carry"
        assert template["rollover_expires_after_periods"] == "3"
        assert template["notice_period_days"] == "30"

        await _import(
            c,
            headers,
            "subscription",
            _csv_bytes(
                [
                    "name", "company", "start_date", "amount", "interval", "interval_count",
                    "currency", "template", "rollover_mode",
                    "rollover_expires_after_periods", "notice_period_days",
                ],
                [
                    [
                        "Acme hosting", "Acme", "2025-01-01", "25.00", "monthly", "2",
                        "EUR", "Hosting Basis", "carry", "3", "30",
                    ]
                ],
            ),
        )
        _, rows = _rows(
            (await c.get("/api/v1/impex/subscription/export", headers=headers)).content
        )
        agreement = rows[0]
        assert agreement["interval_count"] == "2"
        assert agreement["template"] == "Hosting Basis"
        assert agreement["rollover_mode"] == "carry"
        assert agreement["notice_period_days"] == "30"


# --------------------------------------------------------------------------- #
# Tenant isolation (Golden Rule 1) — one test per new entity
# --------------------------------------------------------------------------- #
async def test_new_entities_never_export_another_tenants_rows(client_for) -> None:
    other = await make_tenant("impex-assets-other")
    other_headers = await auth_cookie(other.user)
    async with client_for(other.host) as c:
        await _company(c, other_headers, "Andere klant")
        await _import(
            c,
            other_headers,
            "domain",
            _csv_bytes(
                ["name", "company", "start_date"],
                [["geheim.nl", "Andere klant", "2024-01-15"]],
            ),
        )
        await _import(
            c, other_headers, "hosting", _csv_bytes(["name"], [["Andere server"]])
        )
        await _import(
            c,
            other_headers,
            "website",
            _csv_bytes(["domain", "root"], [["geheim.nl", "true"]]),
        )
        await _import(
            c,
            other_headers,
            "domain_tld_price",
            _csv_bytes(["tld", "amount", "valid_from"], [["xyz", "99.00", "2025-01-01"]]),
        )
        await _import(
            c,
            other_headers,
            "subscription_type",
            _csv_bytes(["key", "label_nl"], [["geheim", "Geheim"]]),
        )

    mine = await make_tenant("impex-assets-mine")
    headers = await auth_cookie(mine.user)
    async with client_for(mine.host) as c:
        for entity, needle in (
            ("domain", "geheim.nl"),
            ("hosting", "Andere server"),
            ("website", "geheim.nl"),
            ("domain_tld_price", "xyz"),
            ("subscription_type", "geheim"),
        ):
            r = await c.get(f"/api/v1/impex/{entity}/export", headers=headers)
            assert r.status_code == 200, entity
            assert needle not in r.content.decode("utf-8-sig"), entity


async def test_a_reference_never_resolves_across_tenants(client_for) -> None:
    """The other tenant's company by name, and their user by e-mail, both fail to resolve —
    the file cannot reach across the isolation boundary even by naming a real row."""
    other = await make_tenant("impex-xref-other")
    async with client_for(other.host) as c:
        await _company(c, await auth_cookie(other.user), "Andere klant")

    mine = await make_tenant("impex-xref-mine")
    headers = await auth_cookie(mine.user)
    async with client_for(mine.host) as c:
        report = await _import(
            c,
            headers,
            "domain",
            _csv_bytes(
                ["name", "company", "start_date", "registry_contact"],
                [["x.nl", "Andere klant", "2024-01-15", f"employee:{other.user.email}"]],
            ),
            commit=False,
        )
        fields = {e["field"] for e in report["errors"]}
        assert fields == {"company", "registry_contact"}
        assert all(
            e["message_key"] == "impex.errors.unresolved_reference" for e in report["errors"]
        )
