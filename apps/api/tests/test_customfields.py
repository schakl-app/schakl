"""Per-tenant custom-fields framework (CLAUDE.md §13).

Covers dynamic validation on a customizable entity (required enforcement, type coercion and
rejection, select options, unknown-key rejection) and cross-tenant isolation of definitions.
"""

from __future__ import annotations

from tests.conftest import auth_cookie, make_tenant


async def _define(c, headers, **payload):
    r = await c.post("/api/v1/custom-fields/definitions", json=payload, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


async def test_required_field_enforced_on_write(client_for) -> None:
    t = await make_tenant("cf-req")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _define(
            c, headers,
            entity_type="company", key="vat", data_type="text",
            required=True, label_i18n={"nl": "BTW", "en": "VAT"},
        )
        # Missing the required custom value → 422 with a per-field i18n key.
        missing = await c.post("/api/v1/companies", json={"name": "NoVat"}, headers=headers)
        assert missing.status_code == 422
        assert missing.json()["error"]["fields"]["vat"] == "errors.required"

        ok = await c.post(
            "/api/v1/companies",
            json={"name": "HasVat", "custom": {"vat": "NL0001"}},
            headers=headers,
        )
        assert ok.status_code == 201
        assert ok.json()["custom"] == {"vat": "NL0001"}


async def test_number_coercion_and_rejection(client_for) -> None:
    t = await make_tenant("cf-num")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _define(
            c, headers,
            entity_type="company", key="employees", data_type="number",
            config_json={"min": 1, "max": 1000},
        )
        bad = await c.post(
            "/api/v1/companies",
            json={"name": "Bad", "custom": {"employees": "abc"}},
            headers=headers,
        )
        assert bad.status_code == 422
        assert bad.json()["error"]["fields"]["employees"] == "customfields.errors.invalid_number"

        good = await c.post(
            "/api/v1/companies",
            json={"name": "Good", "custom": {"employees": "42"}},
            headers=headers,
        )
        assert good.status_code == 201
        # Coerced to a real integer, not the string "42".
        assert good.json()["custom"]["employees"] == 42


async def test_select_option_validation(client_for) -> None:
    t = await make_tenant("cf-sel")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _define(
            c, headers,
            entity_type="company", key="tier", data_type="select",
            options_json=[{"value": "gold", "label_i18n": {"en": "Gold"}},
                          {"value": "silver", "label_i18n": {"en": "Silver"}}],
        )
        bad = await c.post(
            "/api/v1/companies",
            json={"name": "Bad", "custom": {"tier": "bronze"}},
            headers=headers,
        )
        assert bad.status_code == 422
        assert bad.json()["error"]["fields"]["tier"] == "customfields.errors.invalid_option"

        good = await c.post(
            "/api/v1/companies",
            json={"name": "Good", "custom": {"tier": "gold"}},
            headers=headers,
        )
        assert good.status_code == 201


async def test_unknown_key_rejected(client_for) -> None:
    t = await make_tenant("cf-unknown")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        r = await c.post(
            "/api/v1/companies",
            json={"name": "X", "custom": {"nope": "value"}},
            headers=headers,
        )
        assert r.status_code == 422
        assert r.json()["error"]["fields"]["nope"] == "customfields.errors.unknown_field"


async def test_only_managers_can_define(client_for) -> None:
    t = await make_tenant("cf-member", role="member")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        r = await c.post(
            "/api/v1/custom-fields/definitions",
            json={"entity_type": "company", "key": "x", "data_type": "text"},
            headers=headers,
        )
        assert r.status_code == 403


async def test_definitions_isolated_per_tenant(client_for) -> None:
    a = await make_tenant("cf-iso-a")
    b = await make_tenant("cf-iso-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)

    async with client_for(a.host) as ca:
        await _define(
            ca, a_headers, entity_type="company", key="secret_a", data_type="text"
        )

    # B never sees A's definition, and B is free to define the same key itself.
    async with client_for(b.host) as cb:
        listing = await cb.get(
            "/api/v1/custom-fields/definitions",
            params={"entity_type": "company"},
            headers=b_headers,
        )
        assert listing.status_code == 200
        assert listing.json() == []


async def test_entity_types_registry_exposed(client_for) -> None:
    t = await make_tenant("cf-types")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        r = await c.get("/api/v1/custom-fields/entity-types", headers=headers)
        assert r.status_code == 200
        types = r.json()
        assert "company" in types
        assert "contact" in types
