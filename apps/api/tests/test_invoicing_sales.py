"""One-time product sales (``/api/v1/invoicing/sales``) — a subscription with no cycle.

A product sold once to a client had no record between "agreed" and "on an invoice": the price
list knew the price and the document knew what was billed, and nothing in between knew what a
client still owed for. A sale is that record, and it reaches the three places an agreement
period does — the client's ``outstanding`` picker, the org-wide backlog and the line that
bills it. What is pinned here is the *claim*: a sale is on at most one document, a save that
drops the line gives it back, a deleted or cancelled document gives it back, and a second
document asking for it is refused rather than a client billed twice for one delivery.
"""

from __future__ import annotations

from decimal import Decimal

from tests.conftest import Tenant, auth_cookie, make_tenant, org_today


async def _company(client, headers, name: str = "Klant BV") -> str:
    resp = await client.post("/api/v1/companies", json={"name": name}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _product(client, headers, **overrides) -> dict:
    body = {"name": "Website bouwen", "unit_price": "2500.00", "unit": "stuk", **overrides}
    resp = await client.post("/api/v1/invoicing/products", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _sale(client, headers, company_id: str, **overrides) -> dict:
    body = {"company_id": company_id, **overrides}
    resp = await client.post("/api/v1/invoicing/sales", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _backlog_sales(client, headers) -> list[dict]:
    resp = await client.get(
        "/api/v1/invoicing/recurring-backlog", params={"source": "sale"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["items"]


async def test_a_sale_snapshots_the_product_and_lands_on_backlog_and_picker(client_for) -> None:
    """Priced from the list once; a later re-price never rewrites what was sold."""
    tenant: Tenant = await make_tenant("inv-sales-snapshot")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        company_id = await _company(client, headers)
        product = await _product(client, headers, description="Ontwerp en bouw")
        sale = await _sale(client, headers, company_id, product_id=product["id"], quantity="2")
        assert sale["name"] == "Website bouwen"
        assert sale["description"] == "Ontwerp en bouw"
        assert sale["unit"] == "stuk"
        assert Decimal(sale["unit_price"]) == Decimal("2500.00")
        assert Decimal(sale["amount"]) == Decimal("5000.00")
        assert sale["status"] == "open"
        assert sale["sold_on"] == org_today().isoformat()
        assert sale["company_name"] == "Klant BV"

        # Re-pricing the product leaves the sale alone.
        resp = await client.patch(
            f"/api/v1/invoicing/products/{product['id']}",
            json={"unit_price": "9999.00"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        resp = await client.get(f"/api/v1/invoicing/sales/{sale['id']}", headers=headers)
        assert Decimal(resp.json()["unit_price"]) == Decimal("2500.00")

        # The backlog lists it under its own source, and the tiles count it.
        resp = await client.get("/api/v1/invoicing/recurring-backlog", headers=headers)
        report = resp.json()
        assert report["totals_by_source"]["sale"] == {"count": 1, "amount": "5000.00"}
        [item] = [i for i in report["items"] if i["source"] == "sale"]
        assert item["source_id"] == sale["id"]
        assert item["period_end"] == sale["sold_on"]
        assert item["auto_mode"] is None

        # The client's picker offers it, priced.
        resp = await client.get(
            "/api/v1/invoicing/outstanding", params={"company_id": company_id}, headers=headers
        )
        assert resp.status_code == 200, resp.text
        [offer] = resp.json()["sales"]
        assert offer["id"] == sale["id"]
        assert offer["already_billed"] is False
        assert Decimal(offer["amount"]) == Decimal("5000.00")

        # And the price list knows it was sold.
        resp = await client.get(
            "/api/v1/invoicing/products", params={"usage": "true"}, headers=headers
        )
        [row] = [p for p in resp.json() if p["id"] == product["id"]]
        assert row["sales_count"] == 1
        assert Decimal(row["sales_amount"]) == Decimal("5000.00")
        assert row["last_sold_on"] == sale["sold_on"]


async def test_a_sale_without_a_product_needs_a_name(client_for) -> None:
    tenant: Tenant = await make_tenant("inv-sales-name")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        company_id = await _company(client, headers)
        resp = await client.post(
            "/api/v1/invoicing/sales",
            json={"company_id": company_id, "unit_price": "10.00"},
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
        sale = await _sale(client, headers, company_id, name="Losse foto's", unit_price="120")
        assert sale["product_id"] is None
        assert sale["name"] == "Losse foto's"


async def test_a_line_claims_the_sale_and_dropping_it_releases(client_for) -> None:
    """The claim is rebuilt from the lines on every save — the period-claim rule."""
    tenant: Tenant = await make_tenant("inv-sales-claim")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        company_id = await _company(client, headers)
        sale = await _sale(client, headers, company_id, name="Licentie", unit_price="300")
        line = {
            "description": "Licentie",
            "line_kind": "product",
            "quantity": "1",
            "unit_price": "300",
            "sale_id": sale["id"],
        }
        resp = await client.post(
            "/api/v1/invoicing/invoices",
            json={"company_id": company_id, "lines": [line]},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        invoice = resp.json()
        assert invoice["lines"][0]["sale_id"] == sale["id"]

        resp = await client.get(f"/api/v1/invoicing/sales/{sale['id']}", headers=headers)
        assert resp.json()["status"] == "invoiced"
        assert resp.json()["invoice_id"] == invoice["id"]
        assert await _backlog_sales(client, headers) == []
        resp = await client.get(
            "/api/v1/invoicing/outstanding", params={"company_id": company_id}, headers=headers
        )
        assert resp.json()["sales"] == []

        # A second document cannot take it.
        resp = await client.post(
            "/api/v1/invoicing/invoices",
            json={"company_id": company_id, "lines": [line]},
            headers=headers,
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["error"]["message"] == "errors.invoicing.sale_already_billed"

        # Editing its price is refused while a document bills it; its notes are not.
        resp = await client.patch(
            f"/api/v1/invoicing/sales/{sale['id']}",
            json={"unit_price": "1"},
            headers=headers,
        )
        assert resp.status_code == 409, resp.text
        resp = await client.patch(
            f"/api/v1/invoicing/sales/{sale['id']}",
            json={"notes": "geleverd op maandag"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        resp = await client.delete(f"/api/v1/invoicing/sales/{sale['id']}", headers=headers)
        assert resp.status_code == 409

        # Saving the draft without the line hands the sale back.
        resp = await client.patch(
            f"/api/v1/invoicing/invoices/{invoice['id']}",
            json={"lines": [{**line, "sale_id": None}]},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        resp = await client.get(f"/api/v1/invoicing/sales/{sale['id']}", headers=headers)
        assert resp.json()["status"] == "open"
        assert len(await _backlog_sales(client, headers)) == 1


async def test_deleting_the_draft_releases_the_sale(client_for) -> None:
    tenant: Tenant = await make_tenant("inv-sales-release")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        company_id = await _company(client, headers)
        sale = await _sale(client, headers, company_id, name="SSL", unit_price="50")
        resp = await client.post(
            f"/api/v1/invoicing/sales/{sale['id']}/invoice", headers=headers
        )
        assert resp.status_code == 201, resp.text
        invoice = resp.json()
        assert invoice["status"] == "draft"
        assert invoice["company_id"] == company_id
        assert invoice["lines"][0]["sale_id"] == sale["id"]
        assert invoice["lines"][0]["line_kind"] == "product"
        assert Decimal(invoice["total"]) > 0

        # Twice is refused: it is already on a document.
        resp = await client.post(
            f"/api/v1/invoicing/sales/{sale['id']}/invoice", headers=headers
        )
        assert resp.status_code == 409

        resp = await client.delete(
            f"/api/v1/invoicing/invoices/{invoice['id']}", headers=headers
        )
        assert resp.status_code == 204, resp.text
        resp = await client.get(f"/api/v1/invoicing/sales/{sale['id']}", headers=headers)
        assert resp.json()["status"] == "open"
        assert resp.json()["invoice_id"] is None


async def test_a_sale_is_this_clients_and_this_tenants(client_for) -> None:
    """A project of another client is refused; another tenant's sale does not exist."""
    tenant: Tenant = await make_tenant("inv-sales-scope-a")
    other: Tenant = await make_tenant("inv-sales-scope-b")
    headers = await auth_cookie(tenant.user)
    other_headers = await auth_cookie(other.user)
    async with client_for(tenant.host) as client:
        alfa = await _company(client, headers, "Alfa")
        bravo = await _company(client, headers, "Bravo")
        resp = await client.post(
            "/api/v1/projects",
            json={"name": "Bravo site", "company_id": bravo, "status": "active",
                  "budget_period": "total", "currency": "EUR", "custom": {}},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        bravo_project = resp.json()["id"]
        resp = await client.post(
            "/api/v1/invoicing/sales",
            json={"company_id": alfa, "name": "X", "project_id": bravo_project},
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
        sale = await _sale(client, headers, bravo, name="Y", project_id=bravo_project)
        assert sale["project_name"] == "Bravo site"

        # The project filter finds it; a line on an invoice for the *other* client cannot
        # claim it (bill less, never guess).
        resp = await client.get(
            "/api/v1/invoicing/sales", params={"project_id": bravo_project}, headers=headers
        )
        assert [s["id"] for s in resp.json()["items"]] == [sale["id"]]
        assert resp.json()["open_count"] == 1
        resp = await client.post(
            "/api/v1/invoicing/invoices",
            json={
                "company_id": alfa,
                "lines": [{"description": "Y", "unit_price": "1", "sale_id": sale["id"]}],
            },
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        resp = await client.get(f"/api/v1/invoicing/sales/{sale['id']}", headers=headers)
        assert resp.json()["status"] == "open"

    async with client_for(other.host) as client:
        resp = await client.get(f"/api/v1/invoicing/sales/{sale['id']}", headers=other_headers)
        assert resp.status_code == 404
        resp = await client.get("/api/v1/invoicing/sales", headers=other_headers)
        assert resp.json()["total"] == 0
        assert await _backlog_sales(client, other_headers) == []
