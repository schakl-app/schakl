"""TLD-based pricing + renewal invoicing for domains (issue #250).

Covers the append-only ``domain_tld_prices`` history (resolution, CRUD, the #231-shaped
price increase), the pricing fields on ``domains`` (tld stamping, renewal derivation, the
activity trail), the renewal cron (``domain.due`` + one-year advance) and the invoicing
consumer's idempotency — plus the tenant-isolation test every module owes (CLAUDE.md §9).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.events import SystemContext
from app.core.models import Org
from app.db import async_session_maker, set_current_org
from app.modules.domains.models import Domain
from app.modules.domains.service import add_months, first_future_anniversary
from app.modules.invoicing.events import on_domain_due
from tests.conftest import auth_cookie, make_tenant


def _today():
    return datetime.now(UTC).date()


def _iso(day) -> str:
    return day.isoformat()


async def _company(client, headers, name: str = "Acme") -> str:
    r = await client.post("/api/v1/companies", json={"name": name}, headers=headers)
    return r.json()["id"]


async def _domain(client, headers, name: str, company: str, **extra) -> dict:
    r = await client.post(
        "/api/v1/domains",
        json={"name": name, "company_id": company, **extra},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _set_price(client, headers, tld: str, amount: str, valid_from: str | None = None):
    body: dict = {"tld": tld, "amount": amount}
    if valid_from is not None:
        body["valid_from"] = valid_from
    r = await client.post("/api/v1/domains/tld-prices", json=body, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _force_next_invoice(org_id, domain_id: str, day) -> None:
    """The API derives ``next_invoice_date`` in the future on purpose; a cron test needs a
    due one, so it reaches into the row the way the passage of time would."""
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        domain = (
            await session.execute(select(Domain).where(Domain.id == uuid.UUID(domain_id)))
        ).scalar_one()
        domain.next_invoice_date = day
        await session.commit()


async def test_domain_pricing_fields_and_tld_stamping(client_for) -> None:
    t = await make_tenant("dom-price-fields")
    headers = await auth_cookie(t.user)
    today = _today()
    start = today - timedelta(days=400)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        domain = await _domain(
            c, headers, "example.co.uk", company,
            start_date=_iso(start), price_override="19.95",
        )
        assert domain["tld"] == "co.uk"
        assert domain["start_date"] == _iso(start)
        assert domain["price_override"] == "19.95"
        # The renewal is the first anniversary still ahead — never a back-billed one.
        assert domain["next_invoice_date"] == _iso(first_future_anniversary(start, today))

        # start_date omitted → the org-local today; billable → renewal in a year.
        defaulted = await _domain(c, headers, "vandaag.nl", company)
        assert defaulted["start_date"] == _iso(today)
        assert defaulted["next_invoice_date"] == _iso(add_months(today, 12))

        # A dead status schedules nothing; becoming billable derives the date then.
        parked_later = await _domain(
            c, headers, "later.nl", company, status="expired", start_date=_iso(start)
        )
        assert parked_later["next_invoice_date"] is None
        revived = (
            await c.patch(
                f"/api/v1/domains/{parked_later['id']}",
                json={"status": "active"},
                headers=headers,
            )
        ).json()
        assert revived["next_invoice_date"] == _iso(first_future_anniversary(start, today))

        # A rename re-stamps the tld.
        renamed = (
            await c.patch(
                f"/api/v1/domains/{domain['id']}",
                json={"name": "example.nl"},
                headers=headers,
            )
        ).json()
        assert renamed["tld"] == "nl"


async def test_tld_price_resolution(client_for) -> None:
    t = await make_tenant("dom-price-resolve")
    headers = await auth_cookie(t.user)
    today = _today()
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        domain = await _domain(c, headers, "resolve.nl", company)
        # No price anywhere → nothing resolves.
        assert domain["resolved_price"] is None

        # The newest row not in the future wins; a scheduled row is inert until its day.
        await _set_price(c, headers, "nl", "10.00", _iso(today - timedelta(days=365)))
        await _set_price(c, headers, "nl", "12.50", _iso(today))
        await _set_price(c, headers, "nl", "99.00", _iso(today + timedelta(days=30)))
        fetched = (await c.get(f"/api/v1/domains/{domain['id']}", headers=headers)).json()
        assert fetched["resolved_price"] == "12.50"
        assert fetched["resolved_currency"] == "EUR"

        # An override wins over the list; clearing it falls back.
        overridden = (
            await c.patch(
                f"/api/v1/domains/{domain['id']}",
                json={"price_override": "8.00"},
                headers=headers,
            )
        ).json()
        assert overridden["resolved_price"] == "8.00"
        cleared = (
            await c.patch(
                f"/api/v1/domains/{domain['id']}",
                json={"price_override": None},
                headers=headers,
            )
        ).json()
        assert cleared["resolved_price"] == "12.50"

        # The ".NL " the user typed is the "nl" the list stores.
        upper = await _set_price(c, headers, ".COM", "14.00")
        assert upper["tld"] == "com"


async def test_tld_price_groups_and_delete(client_for) -> None:
    t = await make_tenant("dom-price-groups")
    headers = await auth_cookie(t.user)
    today = _today()
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        await _domain(c, headers, "een.nl", company)
        await _domain(c, headers, "twee.nl", company)
        await _domain(c, headers, "drie.be", company)

        await _set_price(c, headers, "nl", "10.00", _iso(today - timedelta(days=1)))
        scheduled = await _set_price(c, headers, "nl", "11.00", _iso(today + timedelta(days=7)))
        # Same-day re-set corrects in place instead of stacking a second row.
        await _set_price(c, headers, "nl", "11.50", _iso(today + timedelta(days=7)))

        groups = (await c.get("/api/v1/domains/tld-prices", headers=headers)).json()
        by_tld = {g["tld"]: g for g in groups}
        assert by_tld["nl"]["domain_count"] == 2
        assert by_tld["nl"]["current"]["amount"] == "10.00"
        assert [row["amount"] for row in by_tld["nl"]["upcoming"]] == ["11.50"]
        # An unpriced TLD the org holds domains under is listed — that's the point.
        assert by_tld["be"]["domain_count"] == 1
        assert by_tld["be"]["current"] is None

        # Undo the scheduled increase.
        gone = await c.delete(
            f"/api/v1/domains/tld-prices/{scheduled['id']}", headers=headers
        )
        assert gone.status_code == 204
        groups = (await c.get("/api/v1/domains/tld-prices", headers=headers)).json()
        assert {g["tld"]: g["upcoming"] for g in groups}["nl"] == []


async def test_tld_price_increase_preview_then_apply(client_for) -> None:
    t = await make_tenant("dom-price-bump")
    headers = await auth_cookie(t.user)
    today = _today()
    effective = today + timedelta(days=30)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        await _domain(c, headers, "bump.nl", company)
        await _domain(c, headers, "vast.nl", company, price_override="5.00")
        await _set_price(c, headers, "nl", "10.00", _iso(today))
        await _set_price(c, headers, "com", "14.00", _iso(today))

        preview = (
            await c.post(
                "/api/v1/domains/tld-prices/price-increase/preview",
                json={"mode": "percent", "value": "10", "valid_from": _iso(effective)},
                headers=headers,
            )
        ).json()
        items = {i["tld"]: i for i in preview["items"]}
        assert items["nl"]["new_amount"] == "11.00"
        assert items["com"]["new_amount"] == "15.40"
        # The overridden domain doesn't ride the list, so it doesn't count as impact.
        assert items["nl"]["domain_count"] == 1
        # A preview writes nothing.
        groups = (await c.get("/api/v1/domains/tld-prices", headers=headers)).json()
        assert all(g["upcoming"] == [] for g in groups)

        applied = (
            await c.post(
                "/api/v1/domains/tld-prices/price-increase",
                json={"mode": "percent", "value": "10", "valid_from": _iso(effective)},
                headers=headers,
            )
        ).json()
        assert {i["tld"]: i["new_amount"] for i in applied["items"]} == {
            "nl": "11.00", "com": "15.40",
        }

        # Re-running with a corrected value replaces the on-date row — never compounds.
        corrected = (
            await c.post(
                "/api/v1/domains/tld-prices/price-increase",
                json={
                    "mode": "percent", "value": "20",
                    "valid_from": _iso(effective), "tld": "nl",
                },
                headers=headers,
            )
        ).json()
        assert [i["new_amount"] for i in corrected["items"]] == ["12.00"]
        groups = (await c.get("/api/v1/domains/tld-prices", headers=headers)).json()
        by_tld = {g["tld"]: g for g in groups}
        assert [row["amount"] for row in by_tld["nl"]["upcoming"]] == ["12.00"]
        assert [row["amount"] for row in by_tld["com"]["upcoming"]] == ["15.40"]

        # An unknown TLD is a 404, never an empty preview.
        missing = await c.post(
            "/api/v1/domains/tld-prices/price-increase/preview",
            json={"mode": "set", "value": "9", "valid_from": _iso(effective), "tld": "xyz"},
            headers=headers,
        )
        assert missing.status_code == 404


async def test_tld_price_manage_is_not_a_member_grant(client_for) -> None:
    t = await make_tenant("dom-price-rbac", role="member")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        # Default member: may see the list (the domain form shows the rate)…
        assert (await c.get("/api/v1/domains/tld-prices", headers=headers)).status_code == 200
        # …but managing it is an admin's call.
        denied = await c.post(
            "/api/v1/domains/tld-prices",
            json={"tld": "nl", "amount": "10.00"},
            headers=headers,
        )
        assert denied.status_code == 403


async def test_tld_price_tenant_isolation(client_for) -> None:
    a = await make_tenant("dom-price-iso-a")
    b = await make_tenant("dom-price-iso-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)
    async with client_for(a.host) as ca:
        company = await _company(ca, a_headers)
        await _domain(ca, a_headers, "iso.nl", company)
        row = await _set_price(ca, a_headers, "nl", "10.00")
    async with client_for(b.host) as cb:
        # B sees neither A's prices nor A's TLD-with-domains rows…
        assert (await cb.get("/api/v1/domains/tld-prices", headers=b_headers)).json() == []
        # …cannot delete A's row by naming its id…
        assert (
            await cb.delete(f"/api/v1/domains/tld-prices/{row['id']}", headers=b_headers)
        ).status_code == 404
        # …and B's own .nl domain resolves nothing from A's list.
        b_company = await _company(cb, b_headers, "Bedrijf B")
        b_domain = await _domain(cb, b_headers, "isolatie.nl", b_company)
        assert b_domain["resolved_price"] is None


async def test_renewal_cron_emits_and_advances_a_year(client_for) -> None:
    from app.core import events
    from app.modules.domains.jobs import advance_domain_renewals

    t = await make_tenant("dom-renew-cron")
    headers = await auth_cookie(t.user)
    today = _today()
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        await _set_price(c, headers, "nl", "12.50", _iso(today - timedelta(days=1)))
        priced = await _domain(c, headers, "cyclus.nl", company)
        overridden = await _domain(
            c, headers, "afspraak.nl", company, price_override="20.00"
        )
        unpriced = await _domain(c, headers, "gratis.be", company)
        dead = await _domain(c, headers, "dood.nl", company, status="expired")

    for row in (priced, overridden, unpriced, dead):
        await _force_next_invoice(t.org.id, row["id"], today)

    fired: list[dict] = []

    async def listener(ctx, payload) -> None:
        fired.append(payload)

    events.subscribe("domain.due", listener)
    try:
        await advance_domain_renewals({})
    finally:
        events._handlers["domain.due"].remove(listener)

    by_name = {p["name"]: p for p in fired}
    # The priced and the overridden domain billed; the unpriced and the dead one did not.
    assert set(by_name) == {"cyclus.nl", "afspraak.nl"}
    assert by_name["cyclus.nl"]["amount"] == "12.50"
    assert by_name["afspraak.nl"]["amount"] == "20.00"
    assert by_name["cyclus.nl"]["period_end"] == _iso(today)
    assert by_name["cyclus.nl"]["period_start"] == _iso(add_months(today, -12))

    async with client_for(t.host) as c:
        after = {
            d["name"]: d
            for d in (await c.get("/api/v1/domains", headers=headers)).json()["items"]
        }
        assert after["cyclus.nl"]["next_invoice_date"] == _iso(add_months(today, 12))
        assert after["afspraak.nl"]["next_invoice_date"] == _iso(add_months(today, 12))
        # Unpriced: untouched, so it bills from the original due date once priced.
        assert after["gratis.be"]["next_invoice_date"] == _iso(today)
        assert after["dood.nl"]["next_invoice_date"] == _iso(today)


async def test_domain_due_drafts_one_invoice_idempotently(client_for) -> None:
    t = await make_tenant("dom-due-draft")
    headers = await auth_cookie(t.user)
    today = _today()
    async with client_for(t.host) as c:
        resp = await c.put(
            "/api/v1/invoicing/settings",
            json={"company_details": {"name": "Agency BV", "country": "NL"}},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        # Seeds the NL rates (21% default among them) — the lazy-seed read.
        assert (
            await c.get("/api/v1/invoicing/tax-rates", headers=headers)
        ).status_code == 200
        company_id = await _company(c, headers, "Klant BV")

    domain_id = uuid.uuid4()
    payload = {
        "domain_id": domain_id,
        "company_id": company_id,
        "name": "voorbeeld.nl",
        "tld": "nl",
        "amount": "12.50",
        "currency": "EUR",
        "period_start": _iso(add_months(today, -12)),
        "period_end": _iso(today),
    }
    async with async_session_maker() as session:
        org = await session.get(Org, t.org.id)
        await set_current_org(session, org.id)
        ctx = SystemContext(org=org, session=session)
        await on_domain_due(ctx, dict(payload))
        await on_domain_due(ctx, dict(payload))  # a double emit must not double-bill
        await session.commit()

    async with client_for(t.host) as c:
        page = (await c.get("/api/v1/invoicing/invoices", headers=headers)).json()
        assert page["total"] == 1
        invoice = page["items"][0]
        assert invoice["status"] == "draft"  # a human issues, never the cron
        assert invoice["domain_id"] == str(domain_id)
        assert invoice["period_end"] == _iso(today)
        assert invoice["reference"] == "voorbeeld.nl"
        # 12.50 + the seeded default 21% — the org's own tax, not the event's business.
        assert invoice["total"] == "15.13"
        detail = (
            await c.get(f"/api/v1/invoicing/invoices/{invoice['id']}", headers=headers)
        ).json()
        description = detail["lines"][0]["description"]
        # The line speaks the org's locale through the catalog — never a raw key.
        assert "voorbeeld.nl" in description
        assert "domains.renewal_line" not in description


async def test_pricing_edits_land_on_the_activity_trail(client_for) -> None:
    t = await make_tenant("dom-price-trail")
    headers = await auth_cookie(t.user)
    today = _today()
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        domain = await _domain(c, headers, "spoor.nl", company)
        await c.patch(
            f"/api/v1/domains/{domain['id']}",
            json={"start_date": _iso(today - timedelta(days=30)), "price_override": "9.99"},
            headers=headers,
        )
        feed = (
            await c.get(
                "/api/v1/activity",
                params={"entity_type": "domain", "entity_id": domain["id"]},
                headers=headers,
            )
        ).json()
        assert [item["action"] for item in feed] == ["updated", "created"]
        changes = feed[0]["payload"]["changes"]
        assert changes["price_override"] == {"from": None, "to": 9.99}
        assert changes["start_date"]["to"] == _iso(today - timedelta(days=30))
