"""A one-time product is an agreement with ``interval = once`` — a subscription with no cycle.

Everything an agreement has applies unchanged (a standard subscription, a type, project links,
notes, custom fields, the backlog, the editor's picker); what is pinned here is the one thing
"once" changes: the agreement owes exactly one period, the cycle fires once and **completes** it,
a document that claims the period by hand completes it too, and the document letting go
reopens it. Plus the product provenance: a preset and an agreement may name the price-list
product they sell, and the price list answers where it is used.
"""

from __future__ import annotations

from decimal import Decimal

from app.core import events
from app.core.billing import add_months
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


async def _once(client, headers, company_id: str, **overrides) -> dict:
    body = {
        "company_id": company_id,
        "name": "Website bouwen",
        "status": "active",
        "interval": "once",
        "start_date": org_today().isoformat(),
        "amount": "2500.00",
        **overrides,
    }
    resp = await client.post("/api/v1/subscriptions", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _sub(client, headers, sub_id: str) -> dict:
    resp = await client.get(f"/api/v1/subscriptions/{sub_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _backlog(client, headers) -> list[dict]:
    resp = await client.get(
        "/api/v1/invoicing/recurring-backlog", params={"source": "subscription"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["items"]


async def test_once_is_billed_on_delivery_and_owes_exactly_one_period(client_for) -> None:
    """Activation derives the one boundary — the start date, never in the past — and the
    backlog and the picker offer that period and nothing after it."""
    tenant: Tenant = await make_tenant("subs-once-period")
    headers = await auth_cookie(tenant.user)
    today = org_today()
    async with client_for(tenant.host) as client:
        company_id = await _company(client, headers)
        sub = await _once(client, headers, company_id)
        assert sub["next_invoice_date"] == today.isoformat()
        assert sub["monthly_equivalent"] is None  # a sale is not run-rate

        # Started last month: billed today, not on a boundary in the past.
        old = await _once(
            client, headers, company_id, name="Oud", start_date=add_months(today, -1).isoformat()
        )
        assert old["next_invoice_date"] == today.isoformat()

        items = [i for i in await _backlog(client, headers) if i["source_id"] == sub["id"]]
        assert len(items) == 1
        assert items[0]["period_start"] is None
        assert items[0]["period_end"] == today.isoformat()
        assert Decimal(items[0]["amount"]) == Decimal("2500.00")

        resp = await client.get(
            "/api/v1/invoicing/outstanding", params={"company_id": company_id}, headers=headers
        )
        [offer] = [s for s in resp.json()["subscriptions"] if s["id"] == sub["id"]]
        assert len(offer["periods"]) == 1
        assert offer["periods"][0]["period_start"] is None
        assert offer["periods"][0]["already_billed"] is False

        # MRR does not count it.
        resp = await client.get("/api/v1/invoicing/summary", headers=headers)
        assert resp.status_code == 200
        resp = await client.get("/api/v1/subscriptions/summary", headers=headers)
        assert Decimal(str(resp.json()["mrr"])) == Decimal(0)


async def test_the_cycle_fires_once_and_completes_the_agreement(client_for) -> None:
    from app.modules.subscriptions.jobs import advance_subscriptions

    tenant: Tenant = await make_tenant("subs-once-cron")
    headers = await auth_cookie(tenant.user)
    today = org_today()
    async with client_for(tenant.host) as client:
        company_id = await _company(client, headers)
        sub = await _once(client, headers, company_id)

    fired: list[dict] = []

    async def listener(ctx, payload) -> None:
        fired.append(payload)

    events.subscribe("subscription.due", listener)
    try:
        await advance_subscriptions({})
        # A second night finds nothing: no next boundary, and the status has moved on.
        await advance_subscriptions({})
    finally:
        events._handlers["subscription.due"].remove(listener)

    assert len(fired) == 1
    assert fired[0]["period_start"] is None
    assert fired[0]["period_end"] == today.isoformat()
    assert fired[0]["amount"] == "2500.00"

    async with client_for(tenant.host) as client:
        after = await _sub(client, headers, sub["id"])
        assert after["status"] == "completed"
        assert after["next_invoice_date"] is None
        # The draft the cron raised claims the period, so nothing is outstanding.
        assert [i for i in await _backlog(client, headers) if i["source_id"] == sub["id"]] == []


async def test_a_line_picked_by_hand_completes_and_the_delete_reopens(client_for) -> None:
    """The other path to the same state: a document claims the one period (the editor's
    picker, an invoice built from the backlog) and lets it go again."""
    tenant: Tenant = await make_tenant("subs-once-claim")
    headers = await auth_cookie(tenant.user)
    today = org_today()
    async with client_for(tenant.host) as client:
        company_id = await _company(client, headers)
        sub = await _once(client, headers, company_id)
        line = {
            "description": "Website bouwen",
            "line_kind": "subscription",
            "quantity": "1",
            "unit_price": "2500",
            "subscription_id": sub["id"],
            "period_end": today.isoformat(),
        }
        resp = await client.post(
            "/api/v1/invoicing/invoices",
            json={"company_id": company_id, "lines": [line]},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        invoice = resp.json()
        after = await _sub(client, headers, sub["id"])
        assert after["status"] == "completed"
        assert after["next_invoice_date"] is None

        # Replacing the line on the draft hands the period back: active again, same day.
        # (A plain product line, not a provenance-less subscription line: that shape is the
        # pre-provenance legacy guard's, which keeps claims on purpose.)
        resp = await client.patch(
            f"/api/v1/invoicing/invoices/{invoice['id']}",
            json={"lines": [{"description": "Iets anders", "unit_price": "1"}]},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        reopened = await _sub(client, headers, sub["id"])
        assert reopened["status"] == "active"
        assert reopened["next_invoice_date"] == today.isoformat()

        # And the same through a delete of a document that holds it.
        resp = await client.post(
            "/api/v1/invoicing/invoices",
            json={"company_id": company_id, "lines": [line]},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        assert (await _sub(client, headers, sub["id"]))["status"] == "completed"
        resp = await client.delete(
            f"/api/v1/invoicing/invoices/{resp.json()['id']}", headers=headers
        )
        assert resp.status_code == 204, resp.text
        assert (await _sub(client, headers, sub["id"]))["status"] == "active"


async def test_a_preset_and_an_agreement_name_the_product_they_sell(client_for) -> None:
    """Provenance, not a copy source: the price list answers where it is used, and re-pricing
    the product changes no agreement. A foreign product id is refused with the field named."""
    tenant: Tenant = await make_tenant("subs-once-product")
    other: Tenant = await make_tenant("subs-once-product-b")
    headers = await auth_cookie(tenant.user)
    other_headers = await auth_cookie(other.user)
    async with client_for(other.host) as client:
        foreign = await _product(client, other_headers, name="Andermans")
    async with client_for(tenant.host) as client:
        company_id = await _company(client, headers)
        product = await _product(client, headers)
        resp = await client.post(
            "/api/v1/subscriptions/templates",
            json={
                "name": "Website bouwen",
                "interval": "once",
                "amount": "2500.00",
                "product_id": product["id"],
            },
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        template = resp.json()
        assert template["product_id"] == product["id"]

        sub = await _once(
            client,
            headers,
            company_id,
            product_id=product["id"],
            subscription_template_id=template["id"],
        )
        assert sub["product_id"] == product["id"]

        resp = await client.get(
            "/api/v1/invoicing/products", params={"usage": "true"}, headers=headers
        )
        [row] = [p for p in resp.json() if p["id"] == product["id"]]
        assert row["agreement_count"] == 1
        assert row["template_count"] == 1
        assert row["last_sold_on"] == org_today().isoformat()

        # Re-pricing the product leaves the agreement's own price alone.
        resp = await client.patch(
            f"/api/v1/invoicing/products/{product['id']}",
            json={"unit_price": "9999.00"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert Decimal((await _sub(client, headers, sub["id"]))["amount"]) == Decimal("2500.00")

        # Another tenant's product does not exist here.
        resp = await client.post(
            "/api/v1/subscriptions",
            json={
                "company_id": company_id,
                "name": "X",
                "interval": "once",
                "start_date": org_today().isoformat(),
                "amount": "1",
                "product_id": foreign["id"],
            },
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["fields"] == {"product_id": "errors.not_found"}

        # Detaching is an explicit null.
        resp = await client.patch(
            f"/api/v1/subscriptions/{sub['id']}", json={"product_id": None}, headers=headers
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["product_id"] is None
