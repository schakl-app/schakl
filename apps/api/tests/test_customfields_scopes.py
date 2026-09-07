"""A subscription custom field attached to a type or a standard subscription (CLAUDE.md §13).

``config_json.scope`` narrows a definition to the agreements of some types and/or presets
(``core/customfields/scoping.py``): the field is required only where it applies, a value on an
agreement it no longer applies to is kept rather than dropped, the settings screen is offered
this tenant's options only, and a flagged field rides the invoice line only of the agreements
it is attached to. Also covers the import: a scoped column is never file-required.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime

from tests.conftest import auth_cookie, make_tenant
from tests.test_customfields import _define

SUB = "/api/v1/subscriptions"


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


async def _company(c, headers, name: str = "Klant BV") -> str:
    r = await c.post("/api/v1/companies", json={"name": name}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _types(c, headers) -> dict[str, str]:
    """Seeded type ids by key (the first list seeds the starter set)."""
    r = await c.get(f"{SUB}/types", headers=headers)
    assert r.status_code == 200, r.text
    return {row["key"]: row["id"] for row in r.json()}


async def _template(c, headers, name: str, type_id: str | None = None) -> str:
    r = await c.post(
        f"{SUB}/templates",
        json={"name": name, "subscription_type_id": type_id, "amount": "25.00"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _body(company_id: str, **extra) -> dict:
    return {
        "company_id": company_id,
        "name": "Pakket",
        "interval": "monthly",
        "start_date": _today(),
        "amount": "25.00",
        **extra,
    }


def _csv(header: list[str], rows: list[list[str]]) -> dict:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    return {"file": ("import.csv", buffer.getvalue().encode("utf-8"), "text/csv")}


async def test_scopes_endpoint_lists_this_tenants_types_and_presets_only(client_for) -> None:
    a = await make_tenant("cfscope-a")
    b = await make_tenant("cfscope-b")
    ha, hb = await auth_cookie(a.user), await auth_cookie(b.user)
    async with client_for(a.host) as c:
        types = await _types(c, ha)
        preset = await _template(c, ha, "Hosting Pro", types["hosting"])
        r = await c.get("/api/v1/custom-fields/scopes?entity_type=subscription", headers=ha)
        assert r.status_code == 200, r.text
        by_key = {row["key"]: row for row in r.json()}
        assert set(by_key) == {"subscription_type_id", "subscription_template_id"}
        assert by_key["subscription_type_id"]["label_key"] == "subscriptions.scope.type"
        assert {o["value"] for o in by_key["subscription_type_id"]["options"]} == set(
            types.values()
        )
        assert [o["value"] for o in by_key["subscription_template_id"]["options"]] == [preset]
        # An entity type with no registered dimension has no scope control at all (#253).
        r = await c.get("/api/v1/custom-fields/scopes?entity_type=company", headers=ha)
        assert r.json() == []
    async with client_for(b.host) as c:
        r = await c.get("/api/v1/custom-fields/scopes?entity_type=subscription", headers=hb)
        assert all(o["value"] != preset for row in r.json() for o in row["options"])
        # …and tenant A's preset cannot be written into tenant B's definition either.
        r = await c.post(
            "/api/v1/custom-fields/definitions",
            json={
                "entity_type": "subscription",
                "key": "website",
                "data_type": "text",
                "config_json": {"scope": {"subscription_template_id": [preset]}},
            },
            headers=hb,
        )
        assert r.status_code == 422, r.text
        assert r.json()["error"]["fields"]["scope"] == "customfields.errors.invalid_scope"


async def test_scope_is_validated_and_normalised_on_write(client_for) -> None:
    t = await make_tenant("cfscope-write")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        types = await _types(c, headers)
        unknown_dimension = await c.post(
            "/api/v1/custom-fields/definitions",
            json={
                "entity_type": "subscription",
                "key": "website",
                "data_type": "text",
                "config_json": {"scope": {"company_id": [types["hosting"]]}},
            },
            headers=headers,
        )
        assert unknown_dimension.status_code == 422
        assert unknown_dimension.json()["error"]["fields"]["scope"] == (
            "customfields.errors.invalid_scope"
        )
        # An empty selection is the same as no scope, and is stored that way.
        d = await _define(
            c, headers,
            entity_type="subscription", key="website", data_type="text",
            config_json={"print_on_document": True, "scope": {"subscription_type_id": []}},
        )
        assert d["config_json"] == {"print_on_document": True}
        # Duplicates fold; an update re-validates against the definition's own entity type.
        r = await c.patch(
            f"/api/v1/custom-fields/definitions/{d['id']}",
            json={
                "config_json": {
                    "scope": {"subscription_type_id": [types["hosting"], types["hosting"]]}
                }
            },
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["config_json"] == {
            "scope": {"subscription_type_id": [types["hosting"]]}
        }
        # A field scoped to a since-deactivated type may still be relabelled.
        r = await c.patch(
            f"{SUB}/types/{types['hosting']}", json={"active": False}, headers=headers
        )
        assert r.status_code == 200, r.text
        r = await c.patch(
            f"/api/v1/custom-fields/definitions/{d['id']}",
            json={
                "label_i18n": {"nl": "Site", "en": "Site"},
                "config_json": {"scope": {"subscription_type_id": [types["hosting"]]}},
            },
            headers=headers,
        )
        assert r.status_code == 200, r.text


async def test_required_binds_only_where_the_field_applies(client_for) -> None:
    t = await make_tenant("cfscope-required")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        types = await _types(c, headers)
        preset = await _template(c, headers, "SEO Plus", types["marketing"])
        await _define(
            c, headers,
            entity_type="subscription", key="website", data_type="text", required=True,
            config_json={
                "scope": {
                    "subscription_type_id": [types["hosting"]],
                    "subscription_template_id": [preset],
                }
            },
        )
        # No type at all: the field does not apply, nothing is required.
        r = await c.post(SUB, json=_body(company), headers=headers)
        assert r.status_code == 201, r.text
        # Another type: still not required.
        r = await c.post(
            SUB, json=_body(company, subscription_type_id=types["support"]), headers=headers
        )
        assert r.status_code == 201, r.text
        # The scoped type: required.
        r = await c.post(
            SUB, json=_body(company, subscription_type_id=types["hosting"]), headers=headers
        )
        assert r.status_code == 422, r.text
        assert r.json()["error"]["fields"]["website"] == "errors.required"
        # The scoped preset, under a *different* type — attached to either is enough.
        r = await c.post(
            SUB,
            json=_body(
                company,
                subscription_type_id=types["marketing"],
                subscription_template_id=preset,
            ),
            headers=headers,
        )
        assert r.status_code == 422, r.text
        assert r.json()["error"]["fields"]["website"] == "errors.required"
        ok = await c.post(
            SUB,
            json=_body(
                company, subscription_type_id=types["hosting"], custom={"website": "klant.nl"}
            ),
            headers=headers,
        )
        assert ok.status_code == 201, ok.text
        assert ok.json()["custom"] == {"website": "klant.nl"}


async def test_value_survives_a_move_away_and_back(client_for) -> None:
    t = await make_tenant("cfscope-move")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        types = await _types(c, headers)
        await _define(
            c, headers,
            entity_type="subscription", key="website", data_type="text", required=True,
            config_json={"scope": {"subscription_type_id": [types["hosting"]]}},
        )
        sub = (
            await c.post(
                SUB,
                json=_body(
                    company,
                    subscription_type_id=types["hosting"],
                    custom={"website": "klant.nl"},
                ),
                headers=headers,
            )
        ).json()
        # Moved to another type, resubmitting the whole value set as the form does: kept.
        r = await c.patch(
            f"{SUB}/{sub['id']}",
            json={"subscription_type_id": types["support"], "custom": {"website": "klant.nl"}},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["custom"] == {"website": "klant.nl"}
        # Off that type the field is not required, so an empty value passes…
        r = await c.patch(f"{SUB}/{sub['id']}", json={"custom": {}}, headers=headers)
        assert r.status_code == 200, r.text
        # …and moving back onto the scoped type in one request asks for it again.
        r = await c.patch(
            f"{SUB}/{sub['id']}",
            json={"subscription_type_id": types["hosting"], "custom": {}},
            headers=headers,
        )
        assert r.status_code == 422, r.text
        assert r.json()["error"]["fields"]["website"] == "errors.required"
        # A PATCH that does not touch ``custom`` is never judged on it.
        r = await c.patch(
            f"{SUB}/{sub['id']}", json={"subscription_type_id": types["hosting"]}, headers=headers
        )
        assert r.status_code == 200, r.text


async def test_flagged_field_rides_only_the_agreements_it_is_attached_to(client_for) -> None:
    from datetime import timedelta

    from tests.test_invoicing_outstanding import _outstanding, _setup_org

    t = await make_tenant("cfscope-note")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _setup_org(c, headers)
        company = await _company(c, headers)
        types = await _types(c, headers)
        await _define(
            c, headers,
            entity_type="subscription", key="website", data_type="text",
            label_i18n={"nl": "Website", "en": "Website"},
            config_json={
                "print_on_document": True,
                "scope": {"subscription_type_id": [types["hosting"]]},
            },
        )
        start = (datetime.now(UTC).date() - timedelta(days=40)).isoformat()
        hosting = (
            await c.post(
                SUB,
                json=_body(
                    company,
                    name="Hosting",
                    status="active",
                    start_date=start,
                    next_invoice_date=_today(),
                    subscription_type_id=types["hosting"],
                    custom={"website": "klant.nl"},
                ),
                headers=headers,
            )
        ).json()
        # A stale value on an SEO agreement: stored, printed nowhere.
        seo = (
            await c.post(
                SUB,
                json=_body(
                    company,
                    name="SEO",
                    status="active",
                    start_date=start,
                    next_invoice_date=_today(),
                    subscription_type_id=types["marketing"],
                    custom={"website": "oud.nl"},
                ),
                headers=headers,
            )
        ).json()
        assert seo["custom"] == {"website": "oud.nl"}
        out = await _outstanding(c, headers, company)

    by_id = {row["id"]: row for row in out["subscriptions"]}

    def descriptions(sub_id: str) -> list[str]:
        return [line["description"] for p in by_id[sub_id]["periods"] for line in p["lines"]]

    hosting_lines = descriptions(hosting["id"])
    assert hosting_lines and all(d == "Hosting \u00b7 Website: klant.nl" for d in hosting_lines)
    seo_lines = descriptions(seo["id"])
    assert seo_lines and all(d == "SEO" for d in seo_lines)


async def test_import_never_requires_a_scoped_column_of_the_whole_file(client_for) -> None:
    t = await make_tenant("cfscope-impex")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _company(c, headers, "Retainer BV")
        types = await _types(c, headers)
        await _define(
            c, headers,
            entity_type="subscription", key="website", data_type="text", required=True,
            config_json={"scope": {"subscription_type_id": [types["hosting"]]}},
        )
        header = ["name", "company", "status", "interval", "start_date", "amount"]
        # The column is absent and the file is still fine at the header…
        report = (
            await c.post(
                "/api/v1/impex/subscription/import",
                files=_csv(header, [["SLA", "Retainer BV", "active", "monthly", _today(), "5"]]),
                headers=headers,
            )
        ).json()
        assert report["errors"] == []
        assert report["creates"] == 1
        # …and with the type column present, only the hosting row is asked for the value.
        header = [*header, "type", "website"]
        report = (
            await c.post(
                "/api/v1/impex/subscription/import",
                files=_csv(
                    header,
                    [
                        ["Host", "Retainer BV", "active", "monthly", _today(), "5", "hosting", ""],
                        ["Seo", "Retainer BV", "active", "monthly", _today(), "5", "marketing", ""],
                        [
                            "Host2", "Retainer BV", "active", "monthly", _today(), "5",
                            "hosting", "klant.nl",
                        ],
                    ],
                ),
                headers=headers,
            )
        ).json()
        assert report["errors"] == [
            {"row": 1, "field": "website", "message_key": "errors.required"}
        ]
        assert report["creates"] == 2
