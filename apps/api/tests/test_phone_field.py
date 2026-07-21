"""Phone-number fields (issue #256): E.164 storage, ``phonenumbers`` validation, and the
grandfather rule that keeps pre-validation freeform ``contacts.phone`` rows editable.

Numbers from two countries (NL and US) prove the validation is country-aware, not a Dutch
regex; the invalid cases include a number that *looks* right but is impossible in its
country's plan — exactly what a hand-rolled pattern cannot reject.
"""

from __future__ import annotations

from sqlalchemy import text

from app.db import async_session_maker, set_current_org
from tests.conftest import auth_cookie, make_tenant


async def test_company_phone_stores_e164(client_for) -> None:
    t = await make_tenant("phone-co")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        # Formatting noise (spaces, dashes) normalises away; E.164 is what lands in the row.
        created = await c.post(
            "/api/v1/companies",
            json={"name": "Acme", "phone": "+31 6 1234-5678"},
            headers=headers,
        )
        assert created.status_code == 201
        company = created.json()
        assert company["phone"] == "+31612345678"

        # A non-NL number is just as welcome — country-awareness isn't hardcoded to NL.
        us = await c.patch(
            f"/api/v1/companies/{company['id']}",
            json={"phone": "+1 415 555 2671"},
            headers=headers,
        )
        assert us.status_code == 200
        assert us.json()["phone"] == "+14155552671"

        # Blank clears the field rather than failing validation.
        cleared = await c.patch(
            f"/api/v1/companies/{company['id']}", json={"phone": ""}, headers=headers
        )
        assert cleared.status_code == 200
        assert cleared.json()["phone"] is None


async def test_company_phone_rejects_invalid(client_for) -> None:
    t = await make_tenant("phone-co-bad")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        for bad in (
            "junk",
            "0612345678",  # national format: ambiguous without a country, so refused
            "+3161234",  # too short for any Dutch number
            "+31999999999",  # right length, but no such NL number range exists
        ):
            resp = await c.post(
                "/api/v1/companies", json={"name": "Bad", "phone": bad}, headers=headers
            )
            assert resp.status_code == 422, bad
            body = resp.json()["error"]
            assert body["fields"]["phone"] == "errors.invalid_phone"


async def test_contact_phone_validates_on_write(client_for) -> None:
    t = await make_tenant("phone-con")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        created = await c.post(
            "/api/v1/contacts",
            json={"first_name": "Ada", "phone": "+31 20 624 1111"},
            headers=headers,
        )
        assert created.status_code == 201
        contact = created.json()
        assert contact["phone"] == "+31206241111"

        bad = await c.post(
            "/api/v1/contacts",
            json={"first_name": "Bob", "phone": "not-a-number"},
            headers=headers,
        )
        assert bad.status_code == 422
        assert bad.json()["error"]["fields"]["phone"] == "errors.invalid_phone"


async def test_contact_legacy_freeform_phone_roundtrips(client_for) -> None:
    """A pre-#256 freeform value never blocks an unrelated edit; only a *changed* phone
    goes through validation."""
    t = await make_tenant("phone-legacy")
    headers = await auth_cookie(t.user)
    legacy = "06 - 12 34 56 78 (na 17u)"
    async with client_for(t.host) as c:
        contact = (
            await c.post("/api/v1/contacts", json={"first_name": "Old"}, headers=headers)
        ).json()

    # Seed the freeform value the way history did: straight into the row, pre-validation.
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        await session.execute(
            text("UPDATE contacts SET phone = :phone WHERE id = :id"),
            {"phone": legacy, "id": contact["id"]},
        )
        await session.commit()

    async with client_for(t.host) as c:
        # An unrelated edit posts the stored phone back unchanged — must not 422.
        edited = await c.patch(
            f"/api/v1/contacts/{contact['id']}",
            json={"job_title": "CTO", "phone": legacy},
            headers=headers,
        )
        assert edited.status_code == 200
        assert edited.json()["phone"] == legacy

        # An edit that leaves phone out entirely is equally safe.
        edited = await c.patch(
            f"/api/v1/contacts/{contact['id']}", json={"notes": "hi"}, headers=headers
        )
        assert edited.status_code == 200
        assert edited.json()["phone"] == legacy

        # But actually *changing* the number goes through the gate…
        rejected = await c.patch(
            f"/api/v1/contacts/{contact['id']}",
            json={"phone": "still not a number"},
            headers=headers,
        )
        assert rejected.status_code == 422

        # …and a real new number replaces the freeform value with E.164.
        upgraded = await c.patch(
            f"/api/v1/contacts/{contact['id']}",
            json={"phone": "+31612345678"},
            headers=headers,
        )
        assert upgraded.status_code == 200
        assert upgraded.json()["phone"] == "+31612345678"


async def test_invoicing_seller_phone_same_gate(client_for) -> None:
    """The agency's own phone (Instellingen → Facturatie, printed on invoices) rides the
    same implementation: E.164 on write, 422 on junk, legacy freeform grandfathered."""
    t = await make_tenant("phone-seller")
    headers = await auth_cookie(t.user)
    seller = {"name": "Agency BV"}
    async with client_for(t.host) as c:
        saved = await c.put(
            "/api/v1/invoicing/settings",
            json={"company_details": {**seller, "phone": "+31 20 624 1111"}},
            headers=headers,
        )
        assert saved.status_code == 200
        assert saved.json()["company_details"]["phone"] == "+31206241111"

        bad = await c.put(
            "/api/v1/invoicing/settings",
            json={"company_details": {**seller, "phone": "geen nummer"}},
            headers=headers,
        )
        assert bad.status_code == 422
        assert bad.json()["error"]["fields"]["phone"] == "errors.invalid_phone"

    # A pre-#256 freeform value posted back unchanged must not block an unrelated save.
    legacy = "020 - 624 11 11"
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        await session.execute(
            text(
                "UPDATE invoicing_settings SET company_details ="
                " jsonb_set(company_details, '{phone}', to_jsonb(CAST(:phone AS text)))"
            ),
            {"phone": legacy},
        )
        await session.commit()
    async with client_for(t.host) as c:
        edited = await c.put(
            "/api/v1/invoicing/settings",
            json={"company_details": {"name": "Renamed BV", "phone": legacy}},
            headers=headers,
        )
        assert edited.status_code == 200
        assert edited.json()["company_details"]["phone"] == legacy


async def test_company_phone_tenant_isolation(client_for) -> None:
    """The new column changes nothing about Golden Rule 1: another org's company — phone
    included — is unreadable and unwritable."""
    t1 = await make_tenant("phone-iso-a")
    t2 = await make_tenant("phone-iso-b")
    h1 = await auth_cookie(t1.user)
    h2 = await auth_cookie(t2.user)
    async with client_for(t1.host) as c:
        company = (
            await c.post(
                "/api/v1/companies",
                json={"name": "Secret", "phone": "+31612345678"},
                headers=h1,
            )
        ).json()
    async with client_for(t2.host) as c:
        read = await c.get(f"/api/v1/companies/{company['id']}", headers=h2)
        assert read.status_code == 404
        write = await c.patch(
            f"/api/v1/companies/{company['id']}",
            json={"phone": "+31687654321"},
            headers=h2,
        )
        assert write.status_code == 404
