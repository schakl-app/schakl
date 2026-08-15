"""companies module API coverage (CLAUDE.md §6, §9): auth, CRUD, custom fields, panels."""

from __future__ import annotations

from tests.conftest import auth_cookie, make_tenant


async def test_requires_authentication(client_for) -> None:
    t = await make_tenant("noauth")
    async with client_for(t.host) as c:
        r = await c.get("/api/v1/companies")
        assert r.status_code == 401
        assert r.json()["error"]["message"] == "errors.unauthorized"


async def test_local_login_then_crud_with_custom_fields(client_for) -> None:
    t = await make_tenant("crud", email="crud@example.com", password="secret1234")
    async with client_for(t.host) as c:
        # Real local login (proves password auth works out of the box).
        login = await c.post(
            "/api/v1/auth/login",
            data={"username": "crud@example.com", "password": "secret1234"},
        )
        assert login.status_code in (200, 204)

        # Define a per-tenant custom field on companies (proves the framework end-to-end).
        definition = await c.post(
            "/api/v1/custom-fields/definitions",
            json={
                "entity_type": "company",
                "key": "vat",
                "label_i18n": {"nl": "BTW", "en": "VAT"},
                "data_type": "text",
            },
        )
        assert definition.status_code == 201

        # Create with a per-tenant custom value; it round-trips.
        created = await c.post(
            "/api/v1/companies",
            json={
                "name": "Acme",
                "website": "https://acme.test",
                "custom": {"vat": "NL0001"},
            },
        )
        assert created.status_code == 201
        company = created.json()
        assert company["custom"] == {"vat": "NL0001"}
        company_id = company["id"]

        listing = await c.get("/api/v1/companies")
        assert listing.json()["total"] == 1

        updated = await c.patch(
            f"/api/v1/companies/{company_id}", json={"name": "Acme B.V."}
        )
        assert updated.status_code == 200
        assert updated.json()["name"] == "Acme B.V."

        deleted = await c.delete(f"/api/v1/companies/{company_id}")
        assert deleted.status_code == 204
        assert (await c.get("/api/v1/companies")).json()["total"] == 0


async def test_register_endpoint_creates_user(client_for) -> None:
    t = await make_tenant("reg")
    async with client_for(t.host) as c:
        r = await c.post(
            "/api/v1/auth/register",
            json={"email": "newbie@example.com", "password": "secret1234"},
        )
        assert r.status_code == 201
        assert r.json()["email"] == "newbie@example.com"


async def test_validation_error_envelope(client_for) -> None:
    t = await make_tenant("val")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        r = await c.post("/api/v1/companies", json={}, headers=headers)
        assert r.status_code == 422
        error = r.json()["error"]
        assert error["code"] == "validation"
        assert error["message"] == "errors.validation"
        assert error["fields"]["name"] == "errors.required"


async def test_client_role_cannot_write(client_for) -> None:
    t = await make_tenant("ro", role="client")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        r = await c.post("/api/v1/companies", json={"name": "Nope"}, headers=headers)
        assert r.status_code == 403
        assert r.json()["error"]["message"] == "errors.forbidden"


async def test_company_panels_compose(client_for) -> None:
    t = await make_tenant("pan")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Panel Co"}, headers=headers)
        ).json()
        r = await c.get(f"/api/v1/companies/{company['id']}/panels", headers=headers)
        assert r.status_code == 200
        panels = {p["key"]: p for p in r.json()}
        assert "companies.details" in panels
        assert panels["companies.details"]["title_key"] == "companies.panel.details"
        assert panels["companies.details"]["data"]["name"] == "Panel Co"


async def test_meta_modules(client_for) -> None:
    t = await make_tenant("meta")
    async with client_for(t.host) as c:
        r = await c.get("/api/v1/meta/modules")
        assert r.status_code == 200
        data = r.json()
        assert "companies" in data["enabled_modules"]
        assert "company" in data["customizable_entity_types"]
        assert data["default_locale"] == "nl"
        assert data["local_login_enabled"] is True


async def test_invoice_email_validation_and_roundtrip(client_for) -> None:
    t = await make_tenant("invemail")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        # Blank normalises to NULL (not every client has one yet).
        blank = await c.post(
            "/api/v1/companies",
            json={"name": "Blank Co", "invoice_email": "  "},
            headers=headers,
        )
        assert blank.status_code == 201
        assert blank.json()["invoice_email"] is None

        # Round-trips through create, get and update.
        created = await c.post(
            "/api/v1/companies",
            json={"name": "Acme", "invoice_email": "facturen@example.com"},
            headers=headers,
        )
        assert created.status_code == 201
        company = created.json()
        assert company["invoice_email"] == "facturen@example.com"

        fetched = await c.get(f"/api/v1/companies/{company['id']}", headers=headers)
        assert fetched.json()["invoice_email"] == "facturen@example.com"

        updated = await c.patch(
            f"/api/v1/companies/{company['id']}",
            json={"invoice_email": "administratie@example.com"},
            headers=headers,
        )
        assert updated.json()["invoice_email"] == "administratie@example.com"

        # Rejected as invalid, not silently dropped.
        invalid = await c.post(
            "/api/v1/companies",
            json={"name": "Bad Co", "invoice_email": "not-an-email"},
            headers=headers,
        )
        assert invalid.status_code == 422
        assert invalid.json()["error"]["fields"]["invoice_email"] == "errors.invalid_email"


async def test_company_status_default_and_roundtrip(client_for) -> None:
    t = await make_tenant("status")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Status Co"}, headers=headers)
        ).json()
        assert company["status"] == "active"

        patched = await c.patch(
            f"/api/v1/companies/{company['id']}",
            json={"status": "offboarding"},
            headers=headers,
        )
        assert patched.json()["status"] == "offboarding"

        fetched = await c.get(f"/api/v1/companies/{company['id']}", headers=headers)
        assert fetched.json()["status"] == "offboarding"


async def test_house_number_is_its_own_field(client_for) -> None:
    """Street and house number store apart (#241); the trail records both like any billing edit."""
    t = await make_tenant("housenr")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        created = await c.post(
            "/api/v1/companies",
            json={
                "name": "Splitsing BV",
                "address_line1": "Binnenhof",
                "house_number": "1A",
                "postal_code": "2513 AA",
                "city": "Den Haag",
                "country": "NL",
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        company = created.json()
        assert company["address_line1"] == "Binnenhof"
        assert company["house_number"] == "1A"

        # The company hub panel carries the split fields for the details view to compose.
        panels = (
            await c.get(f"/api/v1/companies/{company['id']}/panels", headers=headers)
        ).json()
        details = next(p for p in panels if p["key"] == "companies.details")["data"]
        assert details["address_line1"] == "Binnenhof"
        assert details["house_number"] == "1A"

        # A house-number edit is a billing-identity edit: tracked with before/after values.
        r = await c.patch(
            f"/api/v1/companies/{company['id']}",
            json={"house_number": "1B"},
            headers=headers,
        )
        assert r.status_code == 200
        assert r.json()["house_number"] == "1B"
        feed = (
            await c.get(
                "/api/v1/activity",
                params={"entity_type": "company", "entity_id": company["id"]},
                headers=headers,
            )
        ).json()
        assert feed[0]["payload"]["changes"]["house_number"] == {"from": "1A", "to": "1B"}


async def _named(client, headers, name: str, status: str) -> None:
    r = await client.post(
        "/api/v1/companies", json={"name": name, "status": status}, headers=headers
    )
    assert r.status_code == 201, r.text


async def test_status_filter_takes_a_set_and_the_list_is_alphabetical(client_for) -> None:
    """The list orders A–Z and ``status`` takes several values (#329).

    Both halves of what "open Klanten on the working book of business" needs, and the third
    thing it needs is the one asserted last: **no** ``status`` still means every status. The
    screen picks the narrowing default; this endpoint only makes it expressible, because the
    pickers, the impex export and the generated MCP surface all read it and would otherwise be
    told a different set of clients exists.
    """
    t = await make_tenant("status-set")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        # Created A–Z on purpose, so the old `created_at DESC` default is exactly the reverse of
        # what is asserted below and no assertion here can pass by accident.
        await _named(c, headers, "Bakkerij", "archived")
        await _named(c, headers, "Molenaar", "active")
        await _named(c, headers, "Zonnehuis", "lead")

        listing = (await c.get("/api/v1/companies", headers=headers)).json()
        assert [i["name"] for i in listing["items"]] == ["Bakkerij", "Molenaar", "Zonnehuis"]
        assert listing["total"] == 3

        # Searching sorted A–Z already; browsing now agrees with it rather than answering the
        # same question newest-first.
        searched = (
            await c.get("/api/v1/companies", params={"q": "a"}, headers=headers)
        ).json()
        assert [i["name"] for i in searched["items"]] == ["Bakkerij", "Molenaar"]

        one = (
            await c.get("/api/v1/companies", params={"status": "active"}, headers=headers)
        ).json()
        assert [i["name"] for i in one["items"]] == ["Molenaar"]
        assert one["total"] == 1

        # The working set the screen defaults to: every status except the archive.
        working = (
            await c.get(
                "/api/v1/companies",
                params={"status": "lead,onboarding,active,offboarding"},
                headers=headers,
            )
        ).json()
        assert [i["name"] for i in working["items"]] == ["Molenaar", "Zonnehuis"]
        assert working["total"] == 2  # the count is the filter's too, not the page's

        # A saved or shared sort still wins over the alphabetical default — someone who
        # deliberately sorted this list keeps their sort; only the unset case changed.
        by_age = (
            await c.get("/api/v1/companies", params={"sort": "-created_at"}, headers=headers)
        ).json()
        assert [i["name"] for i in by_age["items"]] == ["Zonnehuis", "Molenaar", "Bakkerij"]


async def test_a_status_token_that_names_nothing_falls_back_to_the_whole_list(
    client_for,
) -> None:
    """``status`` arrives from a query string anyone can edit, so it never 422s (#316's rule).

    Separated from the assertion above because it is the *failure* direction: a filter that
    resolves to no statuses at all leaves the list alone rather than emptying it, and an
    unknown status still matches nothing — that half is unchanged, and worth pinning while a
    comma is being read as a separator.
    """
    t = await make_tenant("status-junk")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _named(c, headers, "Bakkerij", "archived")
        await _named(c, headers, "Molenaar", "active")

        for token in (",", " , ", ",,"):
            r = await c.get("/api/v1/companies", params={"status": token}, headers=headers)
            assert r.status_code == 200, r.text
            assert r.json()["total"] == 2, token

        empty = await c.get("/api/v1/companies", params={"status": "klant"}, headers=headers)
        assert empty.json()["items"] == []

        # An unknown name among known ones narrows to the known ones; it does not poison them.
        mixed = (
            await c.get(
                "/api/v1/companies", params={"status": "klant,active"}, headers=headers
            )
        ).json()
        assert [i["name"] for i in mixed["items"]] == ["Molenaar"]
