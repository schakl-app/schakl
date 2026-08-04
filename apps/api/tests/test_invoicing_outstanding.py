"""``GET /api/v1/invoicing/outstanding`` — the arrears enumeration behind the invoice picker.

One call, three buckets (hours, agreement periods, domain renewals), because the editor opens
on all three at once. What these tests pin is the half that is easy to get quietly wrong: an
agreement owes **every period it has served**, not just the next one, and each of those periods
is worth what it was worth *then*. Offer only the newest and the older months become unbillable
except by hand-typing — which is exactly the shape a picker exists to remove.

The three ways the enumeration can lie, each with a test:

* it can be **short** (only the next period, so arrears vanish),
* it can be **wrong** (today's price applied to last winter's month), or
* it can be **silent** (a billed period hidden instead of marked, which produces the duplicate
  a week later; an agreement with no cycle dropped instead of reported).

#250's floor is the opposite failure and is pinned too: onboarding a nineteen-year-old domain
must never invent nineteen years of arrears nobody agreed to.
"""

from __future__ import annotations

import uuid as uuid_mod
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.auth.models import User
from app.core.billing import add_months
from app.core.models import Membership
from app.db import async_session_maker, set_current_org
from app.modules.subscriptions.models import Subscription
from tests.conftest import Tenant, auth_cookie, make_tenant

AMS = ZoneInfo("Europe/Amsterdam")


def _today():
    return datetime.now(AMS).date()


async def _setup_org(client, headers) -> None:
    """Seller details + seeded tax rates: what a real org does once in Instellingen."""
    resp = await client.put(
        "/api/v1/invoicing/settings",
        json={
            "company_details": {
                "name": "Agency BV",
                "address_line1": "Kerkstraat 1",
                "postal_code": "1234 AB",
                "city": "Amsterdam",
                "country": "NL",
                "vat_number": "NL123456789B01",
                "iban": "NL02ABNA0123456789",
                "email": "administratie@agency.nl",
            }
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    rates = (await client.get("/api/v1/invoicing/tax-rates", headers=headers)).json()
    assert any(float(r["rate"]) == 21.0 for r in rates)


async def _company(client, headers, name: str = "Klant BV") -> str:
    resp = await client.post(
        "/api/v1/companies",
        json={"name": name, "invoice_email": "boekhouding@klant.nl"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _subscription(client, headers, company_id: str, **overrides) -> dict:
    body = {
        "company_id": company_id,
        "name": "Hosting & onderhoud",
        "status": "active",
        "interval": "monthly",
        "amount": "100.00",
        **overrides,
    }
    resp = await client.post("/api/v1/subscriptions", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _onboarded_on(org_id, subscription_id: str, day) -> None:
    """Move an agreement's ``created_at`` back to the day the agency actually took it on.

    ``open_agreements`` floors the walk at ``created_at`` — #250's rule that onboarding an old
    record never back-bills history — so an agreement inserted by a test one second ago has, by
    that rule, served exactly one period however far back its ``start_date`` reaches. Arrears
    are what a *genuinely running* agreement accumulates, so the row is aged the way the passage
    of time would age it, the same reach-into-the-row the domains cron tests use. The floor
    itself is asserted on its own terms in the renewals test below.
    """
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        sub = (
            await session.execute(
                select(Subscription).where(Subscription.id == uuid_mod.UUID(subscription_id))
            )
        ).scalar_one()
        sub.created_at = datetime(day.year, day.month, day.day, tzinfo=UTC)
        await session.commit()


async def _outstanding(client, headers, company_id: str) -> dict:
    resp = await client.get(
        "/api/v1/invoicing/outstanding",
        params={"company_id": company_id},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _ends(periods: list[dict]) -> list[str]:
    return [period["period_end"] for period in periods]


async def test_outstanding_enumerates_the_arrears_not_just_the_next_period(client_for) -> None:
    """An agreement seven months in owes seven months, oldest first.

    The whole point of the picker: automation off for a quarter, a paused agreement resumed, or
    simply nobody getting round to it, and the client owes several periods. Offering only the
    period the cron is about to bill leaves the rest reachable only by hand-typing a line — and
    a hand-typed line claims nothing, so the cron would raise it again later.

    Periods chain: each one's ``period_start`` is the previous one's ``period_end``, so the grid
    has no gap and no overlap — a month billed twice and a month never billed are the same bug
    seen from two sides. The count is asserted as a floor rather than a number because the
    calendar clamps (31 Jan + 1 month is 28 Feb, and the day never climbs back), which can cost
    the oldest boundary in a span containing February.
    """
    tenant: Tenant = await make_tenant("inv-out-arrears")
    headers = await auth_cookie(tenant.user)
    today = _today()
    async with client_for(tenant.host) as client:
        company_id = await _company(client, headers)
        sub = await _subscription(
            client,
            headers,
            company_id,
            start_date=add_months(today, -7).isoformat(),
            next_invoice_date=today.isoformat(),
            amount="249.00",
        )
        await _onboarded_on(tenant.org.id, sub["id"], add_months(today, -7))

        agreements = (await _outstanding(client, headers, company_id))["subscriptions"]
        assert len(agreements) == 1
        agreement = agreements[0]
        assert agreement["id"] == sub["id"]
        assert agreement["no_cycle"] is False
        assert agreement["truncated"] is False

        periods = agreement["periods"]
        assert len(periods) >= 6, _ends(periods)
        # Oldest first: arrears are worked through in the order they fell due.
        assert _ends(periods) == sorted(_ends(periods))
        # One month apart, and joined end-to-start — no gap, no overlap.
        for earlier, later in zip(periods, periods[1:], strict=False):
            assert later["period_start"] == earlier["period_end"]
        assert periods[0]["period_start"] == add_months(today, -7).isoformat()
        # The newest period is the one the cron is about to bill, and it has ended: billing it
        # is not billing in advance.
        assert periods[-1]["period_end"] == today.isoformat()
        assert periods[-1]["future"] is False
        assert all(period["amount"] == "249.00" for period in periods)
        assert all(period["already_billed"] is False for period in periods)


async def test_each_period_is_priced_at_its_own_boundary(client_for) -> None:
    """History answers; current state never reprices.

    A raise recorded halfway through the arrears must reach the months after it and no earlier
    one. Pricing every outstanding period at the agreement's *current* amount would silently
    re-bill last autumn at this spring's rate — money, invisible in the JSON shape, and only
    ever noticed by the client.
    """
    tenant: Tenant = await make_tenant("inv-out-prices")
    headers = await auth_cookie(tenant.user)
    today = _today()
    cutover = add_months(today, -3)
    async with client_for(tenant.host) as client:
        company_id = await _company(client, headers)
        sub = await _subscription(
            client,
            headers,
            company_id,
            start_date=add_months(today, -7).isoformat(),
            next_invoice_date=today.isoformat(),
            amount="100.00",
        )
        await _onboarded_on(tenant.org.id, sub["id"], add_months(today, -7))
        raised = await client.patch(
            f"/api/v1/subscriptions/{sub['id']}",
            json={"amount": "150.00", "amount_valid_from": cutover.isoformat()},
            headers=headers,
        )
        assert raised.status_code == 200, raised.text

        agreement = (await _outstanding(client, headers, company_id))["subscriptions"][0]
        # The header figure is the price *now* — each period carries its own.
        assert agreement["amount"] == "150.00"
        periods = agreement["periods"]
        assert {p["amount"] for p in periods} == {"100.00", "150.00"}
        for period in periods:
            expected = "150.00" if period["period_end"] >= cutover.isoformat() else "100.00"
            assert period["amount"] == expected, period
            # The offered line follows the period's price, not the agreement's: a hand-picked
            # arrears month bills exactly what the cron would have raised for it.
            assert [line["unit_price"] for line in period["lines"]] == [expected]


async def test_an_already_billed_period_is_marked_not_hidden(client_for) -> None:
    """"Did I invoice March?" is the question the picker exists to answer.

    Answering it by omission is what produces the duplicate: the month disappears, someone
    types it again by hand, and the client gets two invoices. So a period a document already
    claims comes back flagged, sitting between its unbilled neighbours.
    """
    tenant: Tenant = await make_tenant("inv-out-billed")
    headers = await auth_cookie(tenant.user)
    today = _today()
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        company_id = await _company(client, headers)
        sub = await _subscription(
            client,
            headers,
            company_id,
            start_date=add_months(today, -7).isoformat(),
            next_invoice_date=today.isoformat(),
            amount="249.00",
        )
        await _onboarded_on(tenant.org.id, sub["id"], add_months(today, -7))

        periods = (await _outstanding(client, headers, company_id))["subscriptions"][0]["periods"]
        target = periods[2]  # a month in the middle of the arrears, neighbours either side
        invoice = await client.post(
            "/api/v1/invoicing/invoices",
            json={
                "company_id": company_id,
                "lines": [
                    {
                        "description": "Hosting (achterstallige maand)",
                        "line_kind": "subscription",
                        "quantity": "1",
                        "unit_price": target["amount"],
                        "subscription_id": sub["id"],
                        "period_start": target["period_start"],
                        "period_end": target["period_end"],
                    }
                ],
            },
            headers=headers,
        )
        assert invoice.status_code == 201, invoice.text

        after = (await _outstanding(client, headers, company_id))["subscriptions"][0]["periods"]
        # Shown, not dropped: the same grid comes back, one entry of it flagged.
        assert _ends(after) == _ends(periods)
        billed = {p["period_end"] for p in after if p["already_billed"]}
        assert billed == {target["period_end"]}


async def test_a_paused_agreement_still_owes_its_served_periods(client_for) -> None:
    """A pause stops the cycle; it does not forgive the months already served.

    Cancelled is the other half of the same rule and the reason both are asserted together: an
    agreement that has ended owes nothing further, so dropping it is right — and a test that
    only proved "paused appears" would pass with the status filter removed entirely.
    """
    tenant: Tenant = await make_tenant("inv-out-paused")
    headers = await auth_cookie(tenant.user)
    today = _today()
    async with client_for(tenant.host) as client:
        company_id = await _company(client, headers)
        paused = await _subscription(
            client,
            headers,
            company_id,
            name="Onderhoud (gepauzeerd)",
            start_date=add_months(today, -5).isoformat(),
            next_invoice_date=today.isoformat(),
            amount="80.00",
        )
        cancelled = await _subscription(
            client,
            headers,
            company_id,
            name="Beheer (opgezegd)",
            start_date=add_months(today, -5).isoformat(),
            next_invoice_date=today.isoformat(),
            amount="80.00",
        )
        for sub in (paused, cancelled):
            await _onboarded_on(tenant.org.id, sub["id"], add_months(today, -5))
        before = len(
            next(
                a
                for a in (await _outstanding(client, headers, company_id))["subscriptions"]
                if a["id"] == paused["id"]
            )["periods"]
        )
        assert before >= 4

        for sub, status in ((paused, "paused"), (cancelled, "cancelled")):
            changed = await client.patch(
                f"/api/v1/subscriptions/{sub['id']}", json={"status": status}, headers=headers
            )
            assert changed.status_code == 200, changed.text

        agreements = (await _outstanding(client, headers, company_id))["subscriptions"]
        assert [a["id"] for a in agreements] == [paused["id"]]
        assert len(agreements[0]["periods"]) == before


async def test_an_agreement_without_a_cycle_is_reported_not_dropped(client_for) -> None:
    """No ``next_invoice_date`` means no period can be named — and that is worth saying.

    A mis-set or never-activated agreement is precisely what someone is hunting for when they
    open the picker and find nothing to bill. Dropping it silently makes the screen say "this
    client owes nothing", which is a different claim and a false one.
    """
    tenant: Tenant = await make_tenant("inv-out-nocycle")
    headers = await auth_cookie(tenant.user)
    today = _today()
    async with client_for(tenant.host) as client:
        company_id = await _company(client, headers)
        # Paused from the start: activation is what derives a missing cycle date (#223), so an
        # agreement that never activated genuinely has none.
        sub = await _subscription(
            client,
            headers,
            company_id,
            name="Nog niet ingeregeld",
            status="paused",
            start_date=add_months(today, -2).isoformat(),
            next_invoice_date=None,
            amount="60.00",
        )
        assert sub["next_invoice_date"] is None

        agreements = (await _outstanding(client, headers, company_id))["subscriptions"]
        assert len(agreements) == 1
        assert agreements[0]["id"] == sub["id"]
        assert agreements[0]["no_cycle"] is True
        assert agreements[0]["periods"] == []


async def test_domain_renewals_are_offered_and_onboarding_never_back_bills(client_for) -> None:
    """Renewals are the second bucket, and #250's floor is what keeps it honest.

    A renewal already prints in a document's subscription section, so a picker that claimed to
    show everything outstanding and omitted domains would be lying by eleven lines. The floor is
    the opposite failure: a domain registered in 2005 and entered into schakl last week has
    *reached* twenty boundaries and owes none of them — the agency did not bill any of those
    years, and offering them would invent arrears nobody agreed to.
    """
    tenant: Tenant = await make_tenant("inv-out-domains")
    headers = await auth_cookie(tenant.user)
    today = _today()
    async with client_for(tenant.host) as client:
        company_id = await _company(client, headers)
        priced = await client.post(
            "/api/v1/domains/tld-prices", json={"tld": "nl", "amount": "12.50"}, headers=headers
        )
        assert priced.status_code == 200, priced.text

        recent = await client.post(
            "/api/v1/domains",
            json={
                "name": "recent.nl",
                "company_id": company_id,
                "start_date": add_months(today, -3).isoformat(),
            },
            headers=headers,
        )
        assert recent.status_code == 201, recent.text
        old = await client.post(
            "/api/v1/domains",
            json={
                "name": "oud.nl",
                "company_id": company_id,
                # Registered getting on for twenty years ago; its anniversary falls a couple of
                # months out, so the walk has plenty of boundaries behind it to offer wrongly.
                "start_date": add_months(today, -238).isoformat(),
            },
            headers=headers,
        )
        assert old.status_code == 201, old.text

        by_name = {
            d["name"]: d for d in (await _outstanding(client, headers, company_id))["domains"]
        }
        assert set(by_name) == {"recent.nl", "oud.nl"}

        offered = by_name["recent.nl"]
        assert offered["no_cycle"] is False
        assert offered["no_price"] is False
        assert offered["amount"] == "12.50"
        assert len(offered["periods"]) == 1
        anniversary = date.fromisoformat(recent.json()["next_invoice_date"])
        renewal = offered["periods"][0]
        assert renewal["period_end"] == anniversary.isoformat()
        assert renewal["period_start"] == add_months(anniversary, -12).isoformat()
        assert renewal["amount"] == "12.50"
        assert [line["unit_price"] for line in renewal["lines"]] == ["12.50"]
        # Its boundary is still ahead: billing it is billing in advance, a choice, so it is
        # offered and labelled rather than withheld.
        assert renewal["future"] is True

        # Nineteen years old, onboarded a second ago: its next anniversary, and nothing else.
        aged = by_name["oud.nl"]
        assert _ends(aged["periods"]) == [old.json()["next_invoice_date"]], _ends(aged["periods"])
        assert aged["truncated"] is False


async def test_the_hours_bucket_counts_and_prices_the_whole_backlog(client_for) -> None:
    """The third bucket, and its totals are exact whatever the list does.

    ``total_count``/``total_amount``/``total_minutes`` come from an aggregate over the whole
    outstanding set rather than from the rows returned, so "2 posten, € 160,00" is never a
    number the detail cap quietly shrank. ``truncated`` is the flag that admits to the cap —
    over a limit is reported, never a silent truncation that reads as "this is everything".
    """
    tenant: Tenant = await make_tenant("inv-out-hours")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        company_id = await _company(client, headers)
        # No personal rate: the leave org default is the effective rate (#226/#113).
        assert (
            await client.put(
                "/api/v1/leave/settings",
                json={"default_hourly_rate": "80.00"},
                headers=headers,
            )
        ).status_code == 200
        project = (
            await client.post(
                "/api/v1/projects",
                json={"name": "Retainer", "company_id": company_id},
                headers=headers,
            )
        ).json()

        started = datetime.now(UTC) - timedelta(days=2)
        entry_ids = []
        for minutes in (90, 30):
            entry = await client.post(
                "/api/v1/time/entries",
                json={
                    "company_id": company_id,
                    "project_id": project["id"],
                    "description": "Werkzaamheden",
                    "started_at": started.isoformat(),
                    "minutes": minutes,
                    "billable": True,
                },
                headers=headers,
            )
            assert entry.status_code == 201, entry.text
            entry_ids.append(entry.json()["id"])
        assert (
            await client.post(
                "/api/v1/time/entries/approve",
                json={"entry_ids": entry_ids, "approved": True},
                headers=headers,
            )
        ).status_code == 200

        hours = (await _outstanding(client, headers, company_id))["hours"]
        assert len(hours["entries"]) == 2
        assert hours["total_count"] == 2
        assert hours["total_minutes"] == 120
        # 120 minutes at the org's €80: exact money, computed in SQL over the whole set.
        assert hours["total_amount"] == "160.00"
        assert hours["truncated"] is False
        assert all(entry["rate"] == "80.00" for entry in hours["entries"])


async def test_outstanding_needs_invoice_write_and_resolves_the_client_first(client_for) -> None:
    """Deny-by-default (§15) plus the tenant boundary, on a build-an-invoice surface.

    It hangs off ``invoicing.invoice.write`` rather than ``.read``: this is what an editor picks
    from, not a report. And the client is resolved *before* anything is read, so asking about
    another tenant's company is ``not_found`` — an empty answer would be safe for the rows and
    still confirm the id exists somewhere, which is the leak §15 spends its 404 rule on.
    """
    tenant: Tenant = await make_tenant("inv-out-rbac")
    other: Tenant = await make_tenant("inv-out-rbac-b")
    headers = await auth_cookie(tenant.user)
    other_headers = await auth_cookie(other.user)

    # A bare membership, no roles attached: authenticates, and then holds nothing. Constructed
    # by hand rather than via ``add_membership``, whose whole job is to attach the system role.
    async with async_session_maker() as session:
        nobody = User(
            id=uuid_mod.uuid4(),
            email="niemand@example.com",
            hashed_password="",
            is_active=True,
            is_verified=True,
        )
        session.add(nobody)
        await session.flush()
        await set_current_org(session, tenant.org.id)
        session.add(Membership(org_id=tenant.org.id, user_id=nobody.id))
        await session.commit()
    empty_headers = await auth_cookie(nobody)

    async with client_for(tenant.host) as client:
        company_id = await _company(client, headers)
        # Non-vacuous: the owner reads it, so the 403 below is the gate and not a broken route.
        assert (
            await client.get(
                "/api/v1/invoicing/outstanding",
                params={"company_id": company_id},
                headers=headers,
            )
        ).status_code == 200
        assert (
            await client.get(
                "/api/v1/invoicing/outstanding",
                params={"company_id": company_id},
                headers=empty_headers,
            )
        ).status_code == 403

    async with client_for(other.host) as cb:
        cross = await cb.get(
            "/api/v1/invoicing/outstanding",
            params={"company_id": company_id},
            headers=other_headers,
        )
        assert cross.status_code == 404
        assert cross.json()["error"]["message"] == "errors.not_found"
