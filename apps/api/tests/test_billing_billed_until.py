"""``billed_until`` — the operator's "already invoiced up to" statement — and the record's own
invoicing history (``GET /invoicing/billed-periods``).

Both halves of "what is still to invoice" have to read the statement: the backlog and the
picker through ``period_boundaries``, the crons before they draft. A test per half, because the
audit that produced ``app/core/billing.py`` found exactly the failure where one half honours a
rule and the other does not — the backlog hiding a period the cron drafted that night.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.core import events
from app.core.billing import add_months
from app.db import async_session_maker, set_current_org
from app.modules.domains.jobs import advance_domain_renewals
from app.modules.subscriptions.jobs import advance_subscriptions
from app.modules.subscriptions.models import Subscription
from tests.conftest import auth_cookie, make_tenant, org_today


def _iso(day) -> str:
    return day.isoformat()


async def _company(client, headers, name: str = "Klant BV") -> str:
    resp = await client.post("/api/v1/companies", json={"name": name}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _set_next_invoice(org_id, model, row_id: str, day) -> None:
    """The API derives a cycle date still ahead on purpose; a cron test needs one that has
    come up, so it reaches into the row the way the passage of time would."""
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        row = (
            await session.execute(select(model).where(model.id == uuid.UUID(row_id)))
        ).scalar_one()
        row.next_invoice_date = day
        await session.commit()


async def _fired(event: str, job) -> list[dict]:
    fired: list[dict] = []

    async def listener(ctx, payload) -> None:
        fired.append(payload)

    events.subscribe(event, listener)
    try:
        await job({})
    finally:
        events._handlers[event].remove(listener)
    return fired


async def test_a_domain_year_invoiced_elsewhere_is_neither_listed_nor_drafted(
    client_for,
) -> None:
    """A portfolio migrated from another system was invoiced there up to its next renewal.
    Saying so takes every year up to that date off the backlog and out of the cron's hands —
    the year after it is owed as before, and withdrawing the statement re-opens the rest."""
    t = await make_tenant("billed-until-domain")
    headers = await auth_cookie(t.user)
    today = org_today()
    renewal = add_months(today, -1)  # a renewal the calendar has passed
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        await c.post(
            "/api/v1/domains/tld-prices",
            json={"tld": "nl", "amount": "12.50", "valid_from": _iso(add_months(today, -36))},
            headers=headers,
        )
        created = await c.post(
            "/api/v1/domains",
            json={
                "name": "elders-gefactureerd.nl",
                "company_id": company,
                "start_date": _iso(add_months(renewal, -24)),
                "next_invoice_date": _iso(renewal),
                # Invoiced by the previous system up to and including the year that starts at
                # the renewal date — "01-10-2026 – 01-10-2027" is already on a document there.
                "billed_until": _iso(add_months(renewal, 12)),
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        domain = created.json()
        assert domain["billed_until"] == _iso(add_months(renewal, 12))

        backlog = (
            await c.get(
                "/api/v1/invoicing/recurring-backlog",
                params={"source": "domain"},
                headers=headers,
            )
        ).json()
        assert [i for i in backlog["items"] if i["source_id"] == domain["id"]] == []

    # The cron rolls the settled year forward without a draft...
    fired = await _fired("domain.due", advance_domain_renewals)
    assert [p for p in fired if str(p["domain_id"]) == domain["id"]] == []
    async with client_for(t.host) as c:
        after = (await c.get(f"/api/v1/domains/{domain['id']}", headers=headers)).json()
        assert after["next_invoice_date"] == _iso(add_months(renewal, 12))

        # ...and the year after it is owed as before: it starts where the statement ends.
        withdrawn = await c.patch(
            f"/api/v1/domains/{domain['id']}",
            json={"billed_until": None},
            headers=headers,
        )
        assert withdrawn.status_code == 200, withdrawn.text
        assert withdrawn.json()["billed_until"] is None
        backlog = (
            await c.get(
                "/api/v1/invoicing/recurring-backlog",
                params={"source": "domain"},
                headers=headers,
            )
        ).json()
        rows = [i for i in backlog["items"] if i["source_id"] == domain["id"]]
        # Withdrawn, the anchor (now a year on) is offered — and only it: the floor still
        # keeps an onboarded domain's history off the backlog.
        assert [(r["period_start"], r["period_end"]) for r in rows] == [
            (_iso(add_months(renewal, 12)), _iso(add_months(renewal, 24)))
        ]
        assert rows[0]["future"] is True


async def test_an_agreement_invoiced_elsewhere_skips_the_settled_months(client_for) -> None:
    t = await make_tenant("billed-until-sub")
    headers = await auth_cookie(t.user)
    today = org_today()
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        created = await c.post(
            "/api/v1/subscriptions",
            json={
                "company_id": company,
                "name": "Hosting",
                "status": "active",
                "interval": "monthly",
                "amount": "100.00",
                "start_date": _iso(add_months(today, -6)),
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        sub = created.json()
    # Three months behind, of which the first two were invoiced by the previous system.
    anchor = add_months(today, -2)
    await _set_next_invoice(t.org.id, Subscription, sub["id"], anchor)
    async with client_for(t.host) as c:
        marked = await c.patch(
            f"/api/v1/subscriptions/{sub['id']}",
            json={"billed_until": _iso(add_months(today, -1))},
            headers=headers,
        )
        assert marked.status_code == 200, marked.text
        assert marked.json()["billed_until"] == _iso(add_months(today, -1))
        backlog = (
            await c.get(
                "/api/v1/invoicing/recurring-backlog",
                params={"source": "subscription"},
                headers=headers,
            )
        ).json()
        rows = [i for i in backlog["items"] if i["source_id"] == sub["id"]]
        # Periods ending at anchor and anchor+1 are settled; the one ending today is owed.
        assert [r["period_end"] for r in rows] == [_iso(today)]

    fired = await _fired("subscription.due", advance_subscriptions)
    mine = [p for p in fired if str(p["subscription_id"]) == sub["id"]]
    assert [p["period_end"] for p in mine] == [_iso(today)]


async def test_billed_periods_name_the_document_that_holds_each_period(client_for) -> None:
    """The record page's own history: which years were billed, on which invoice, in what
    state — read off the claim a hand-built invoice writes, never a second bookkeeping."""
    t = await make_tenant("billed-periods-read")
    headers = await auth_cookie(t.user)
    today = org_today()
    async with client_for(t.host) as c:
        await c.put(
            "/api/v1/invoicing/settings",
            json={"company_details": {"name": "Agency BV", "country": "NL"}},
            headers=headers,
        )
        await c.get("/api/v1/invoicing/tax-rates", headers=headers)
        company = await _company(c, headers)
        await c.post(
            "/api/v1/domains/tld-prices",
            json={"tld": "nl", "amount": "12.50", "valid_from": _iso(add_months(today, -36))},
            headers=headers,
        )
        domain = (
            await c.post(
                "/api/v1/domains",
                json={"name": "geclaimd.nl", "company_id": company},
                headers=headers,
            )
        ).json()
        empty = await c.get(
            "/api/v1/invoicing/billed-periods",
            params={"source": "domain", "source_id": domain["id"]},
            headers=headers,
        )
        assert empty.status_code == 200, empty.text
        assert empty.json() == []

        outstanding = (
            await c.get(
                "/api/v1/invoicing/outstanding",
                params={"company_id": company},
                headers=headers,
            )
        ).json()
        offered = next(d for d in outstanding["domains"] if d["id"] == domain["id"])
        period = offered["periods"][0]
        invoice = await c.post(
            "/api/v1/invoicing/invoices",
            json={
                "company_id": company,
                "lines": [
                    {
                        "description": "Domeinverlenging geclaimd.nl",
                        "quantity": "1",
                        "unit_price": "12.50",
                        "line_kind": "domain",
                        "domain_id": domain["id"],
                        "period_start": period["period_start"],
                        "period_end": period["period_end"],
                    }
                ],
            },
            headers=headers,
        )
        assert invoice.status_code == 201, invoice.text
        rows = (
            await c.get(
                "/api/v1/invoicing/billed-periods",
                params={"source": "domain", "source_id": domain["id"]},
                headers=headers,
            )
        ).json()
        assert [(r["period_start"], r["period_end"]) for r in rows] == [
            (period["period_start"], period["period_end"])
        ]
        assert rows[0]["invoice_id"] == invoice.json()["id"]
        assert rows[0]["invoice_status"] == "draft"
        assert rows[0]["invoice_number"] is None  # a draft has no number yet
