"""``GET /api/v1/invoicing/recurring-backlog`` and the ``domain`` line kind (issue #302).

Two halves of one change, and each has a failure mode that no functional test would otherwise
catch.

**The backlog** is the recurring half of "nog te factureren": agreement periods and domain
renewals that no document claims, org-wide. Its three ways of lying are what is pinned here —
it can be **scoped** (one client's arrears, because the seam it is built on only ever answered
per client), it can be **stale** (offering a period an invoice already bills, which is how a
client gets charged for March twice), and it can be **capped in the wrong place** (a truncated
*count*, so a backlog of 900 reads as 500 and the difference is never invoiced).

**The kind** is provenance. A renewal used to be stamped ``subscription``, so the claim
reconcile's legacy guard fired on that kind for both sources. Splitting them re-opens the exact
bug provenance was added to close unless the domain source still answers to the old kind: a
pre-split draft's renewal lines name no period, the guard has to recognise them anyway, and a
guard that does not release the claim and hands the year back to the cron.
"""

from __future__ import annotations

import uuid as uuid_mod
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.billing import add_months
from app.db import async_session_maker, set_current_org
from app.modules.domains.models import Domain
from app.modules.invoicing.models import InvoiceDomainPeriod, InvoiceLine
from app.modules.subscriptions.models import Subscription
from tests.conftest import Tenant, auth_cookie, make_tenant

AMS = ZoneInfo("Europe/Amsterdam")


def _today() -> date:
    return datetime.now(AMS).date()


async def _company(client, headers, name: str) -> str:
    resp = await client.post("/api/v1/companies", json={"name": name}, headers=headers)
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


async def _age_row(org_id, model, row_id: str, day: date) -> None:
    """Move a record's ``created_at`` back to when the agency really took it on.

    Both seams floor their period walk at ``created_at`` (#250's *onboarding an old record
    never back-bills history*), so a row inserted a second ago owes exactly one period however
    far back its ``start_date`` reaches. Arrears are what a genuinely running agreement
    accumulates, so the row is aged the way the passage of time would age it.
    """
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        row = (
            await session.execute(select(model).where(model.id == uuid_mod.UUID(row_id)))
        ).scalar_one()
        row.created_at = datetime(day.year, day.month, day.day, tzinfo=UTC)
        await session.commit()


async def _backlog(client, headers, **params) -> dict:
    resp = await client.get(
        "/api/v1/invoicing/recurring-backlog", params=params, headers=headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_backlog_spans_every_client_not_just_one(client_for) -> None:
    """The report is org-wide, and that is the whole reason it exists.

    Both seams it reads answered only ``company_id`` before this, so the recurring backlog was
    reachable exclusively from inside one client's invoice editor. "What do we have to invoice
    this month" then had no screen at all — and arrears least of all, because the cycle cron
    advances whether or not it drafted anything, so a period automation was off for simply sits
    there with nothing to surface it.
    """
    tenant: Tenant = await make_tenant("inv-backlog-orgwide")
    headers = await auth_cookie(tenant.user)
    today = _today()
    async with client_for(tenant.host) as client:
        first = await _company(client, headers, "Alfa BV")
        second = await _company(client, headers, "Bravo BV")
        for company_id, amount in ((first, "100.00"), (second, "250.00")):
            sub = await _subscription(
                client,
                headers,
                company_id,
                start_date=add_months(today, -3).isoformat(),
                next_invoice_date=today.isoformat(),
                amount=amount,
            )
            await _age_row(tenant.org.id, Subscription, sub["id"], add_months(today, -3))

        report = await _backlog(client, headers, group="company")
        assert {g["label"] for g in report["groups"]} == {"Alfa BV", "Bravo BV"}
        assert report["total_count"] == len(report["items"]) > 2
        assert report["truncated"] is False
        # Every item names its client: the page groups by it and a consumer that had to look
        # it up would do so once per row.
        assert all(item["company_name"] in {"Alfa BV", "Bravo BV"} for item in report["items"])
        # The subtotals are per client and sum to the whole.
        assert sum(g["count"] for g in report["groups"]) == report["total_count"]
        assert sum(float(g["amount"]) for g in report["groups"]) == float(
            report["total_amount"]
        )


async def test_backlog_drops_a_period_an_invoice_already_claims(client_for) -> None:
    """A claimed period is **excluded**, which is the opposite of what the picker does.

    Deliberately opposite: the picker shows a billed period marked ``already_billed`` because
    it is preventing a duplicate on the document you are building, and answering "did I invoice
    March?" by omission is what produces that duplicate. A backlog is a list of *work*, and work
    that is on an invoice is done. Getting this backwards bills March twice.
    """
    tenant: Tenant = await make_tenant("inv-backlog-claimed")
    headers = await auth_cookie(tenant.user)
    today = _today()
    async with client_for(tenant.host) as client:
        company_id = await _company(client, headers, "Klant BV")
        sub = await _subscription(
            client,
            headers,
            company_id,
            start_date=add_months(today, -4).isoformat(),
            next_invoice_date=today.isoformat(),
        )
        await _age_row(tenant.org.id, Subscription, sub["id"], add_months(today, -4))

        before = await _backlog(client, headers, source="subscription")
        assert before["total_count"] >= 2
        target = before["items"][0]

        # Bill exactly that period, the way the editor's picker does: a line carrying the
        # agreement and the period it covers.
        created = await client.post(
            "/api/v1/invoicing/invoices",
            json={
                "company_id": company_id,
                "lines": [
                    {
                        "line_kind": "subscription",
                        "description": "Hosting",
                        "quantity": "1",
                        "unit_price": target["amount"],
                        "subscription_id": target["source_id"],
                        "period_start": target["period_start"],
                        "period_end": target["period_end"],
                    }
                ],
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text

        after = await _backlog(client, headers, source="subscription")
        assert after["total_count"] == before["total_count"] - 1
        assert target["period_end"] not in {i["period_end"] for i in after["items"]}


async def test_backlog_totals_survive_the_detail_cap(client_for) -> None:
    """The cap bounds the *detail*; the count and the money stay exact.

    A backlog page whose headline number is silently the cap is the worst answer available: it
    reads as "this is everything" and the difference is never invoiced. Same rule the hours
    report and the impex parser already follow — over a limit is reported, never truncated in
    silence.
    """
    tenant: Tenant = await make_tenant("inv-backlog-cap")
    headers = await auth_cookie(tenant.user)
    today = _today()
    async with client_for(tenant.host) as client:
        company_id = await _company(client, headers, "Klant BV")
        sub = await _subscription(
            client,
            headers,
            company_id,
            start_date=add_months(today, -6).isoformat(),
            next_invoice_date=today.isoformat(),
        )
        await _age_row(tenant.org.id, Subscription, sub["id"], add_months(today, -6))

        full = await _backlog(client, headers)
        assert full["total_count"] >= 4

        capped = await _backlog(client, headers, limit=2)
        assert len(capped["items"]) == 2
        assert capped["truncated"] is True
        # The two numbers a reader steers by are unchanged by the cap.
        assert capped["total_count"] == full["total_count"]
        assert capped["total_amount"] == full["total_amount"]
        # And so are the per-group subtotals: they are bucketed before the cap, which is why
        # the page must render *these* rather than re-sum the item list it was sent.
        assert sum(g["count"] for g in capped["groups"]) == full["total_count"]
        assert capped["groups"] == full["groups"]


async def test_the_tiles_count_every_source_even_when_the_list_is_narrowed(client_for) -> None:
    """``totals_by_source`` ignores the ``source`` filter, and has to.

    It is what the page's three tiles are. A tile that only counted the source already
    selected would summarise nothing — you would have to click Domeinen to discover that
    domains are worth anything, which is the opposite of what a summary is for. So the filter
    narrows ``groups``/``items``/``total_*`` and leaves these alone.
    """
    tenant: Tenant = await make_tenant("inv-backlog-tiles")
    headers = await auth_cookie(tenant.user)
    today = _today()
    async with client_for(tenant.host) as client:
        company_id = await _company(client, headers, "Klant BV")
        sub = await _subscription(
            client,
            headers,
            company_id,
            start_date=add_months(today, -2).isoformat(),
            next_invoice_date=today.isoformat(),
        )
        await _age_row(tenant.org.id, Subscription, sub["id"], add_months(today, -2))
        priced = await client.post(
            "/api/v1/domains/tld-prices", json={"tld": "nl", "amount": "12.50"}, headers=headers
        )
        assert priced.status_code == 200, priced.text
        domain = await client.post(
            "/api/v1/domains",
            json={
                "name": "klant.nl",
                "company_id": company_id,
                "start_date": add_months(today, -13).isoformat(),
            },
            headers=headers,
        )
        assert domain.status_code == 201, domain.text
        await _age_row(tenant.org.id, Domain, domain.json()["id"], add_months(today, -13))

        everything = await _backlog(client, headers, source="all")
        tiles = everything["totals_by_source"]
        assert tiles["subscription"]["count"] > 0
        assert tiles["domain"]["count"] > 0

        # Narrow to one source: the list shrinks, the tiles do not.
        narrowed = await _backlog(client, headers, source="domain")
        assert narrowed["totals_by_source"] == tiles
        assert narrowed["total_count"] == tiles["domain"]["count"] < everything["total_count"]
        assert all(i["source"] == "domain" for i in narrowed["items"])
        # The narrowed subtotals are the narrowed set's, not both sources'.
        assert sum(g["count"] for g in narrowed["groups"]) == narrowed["total_count"]


async def test_backlog_reports_the_automation_level_that_applies(client_for) -> None:
    """Each row carries the level its own cron runs at, resolved over the org default.

    The page has to separate "nobody drafted this and nobody will" from "this is automated and
    simply has arrears from before it was". ``NULL`` on the agreement means *inherit*, never
    *off* (§14's three-state rule), so an agreement that has never been touched must report the
    org's level and not a fourth thing.
    """
    tenant: Tenant = await make_tenant("inv-backlog-auto")
    headers = await auth_cookie(tenant.user)
    today = _today()
    async with client_for(tenant.host) as client:
        settings = await client.put(
            "/api/v1/invoicing/settings",
            json={"company_details": {"name": "Agency BV"}, "auto_invoice_mode": "issue"},
            headers=headers,
        )
        assert settings.status_code == 200, settings.text

        company_id = await _company(client, headers, "Klant BV")
        inherits = await _subscription(
            client,
            headers,
            company_id,
            name="Volgt de organisatie",
            start_date=add_months(today, -2).isoformat(),
            next_invoice_date=today.isoformat(),
        )
        overrides = await _subscription(
            client,
            headers,
            company_id,
            name="Eigen niveau",
            start_date=add_months(today, -2).isoformat(),
            next_invoice_date=today.isoformat(),
            auto_invoice_mode="off",
        )
        for sub in (inherits, overrides):
            await _age_row(tenant.org.id, Subscription, sub["id"], add_months(today, -2))

        report = await _backlog(client, headers, source="subscription")
        assert report["org_auto_invoice_mode"] == "issue"
        modes = {i["name"]: i["auto_mode"] for i in report["items"]}
        assert modes["Volgt de organisatie"] == "issue"
        assert modes["Eigen niveau"] == "off"


async def test_backlog_leaves_out_a_domain_the_agency_does_not_invoice(client_for) -> None:
    """A domain flagged not-invoiceable (#298) is not work — it is somebody else's renewal.

    The picker lists it anyway, labelled, because "why is klant.nl not on the invoice" is a
    question a person building a document asks. A backlog answering "what must we still bill"
    would be wrong to include it: it is never going to be billed, and a permanent row nobody
    can clear is how a backlog page stops being read.
    """
    tenant: Tenant = await make_tenant("inv-backlog-noninvoiceable")
    headers = await auth_cookie(tenant.user)
    today = _today()
    async with client_for(tenant.host) as client:
        company_id = await _company(client, headers, "Klant BV")
        priced = await client.post(
            "/api/v1/domains/tld-prices", json={"tld": "nl", "amount": "12.50"}, headers=headers
        )
        assert priced.status_code == 200, priced.text
        ours = await client.post(
            "/api/v1/domains",
            json={
                "name": "onze.nl",
                "company_id": company_id,
                "start_date": add_months(today, -13).isoformat(),
            },
            headers=headers,
        )
        assert ours.status_code == 201, ours.text
        theirs = await client.post(
            "/api/v1/domains",
            json={
                "name": "hunne.nl",
                "company_id": company_id,
                "start_date": add_months(today, -13).isoformat(),
                "invoiceable": False,
            },
            headers=headers,
        )
        assert theirs.status_code == 201, theirs.text
        for row in (ours, theirs):
            await _age_row(tenant.org.id, Domain, row.json()["id"], add_months(today, -13))

        report = await _backlog(client, headers, source="domain")
        names = {item["name"] for item in report["items"]}
        assert "onze.nl" in names
        assert "hunne.nl" not in names
        assert all(item["source"] == "domain" for item in report["items"])


async def test_renewal_lines_are_their_own_kind_and_keep_their_claim(client_for) -> None:
    """A hand-built renewal line stamps ``domain``, and the claim survives a re-save.

    The round trip is the part that bites. The editor replaces lines wholesale on save, so the
    claim tables are rebuilt from the posted lines every time; if the new kind were not
    reflected everywhere the reconcile looks, re-saving a draft would release the claim and the
    renewal cron would bill the year again. The claim is asserted directly on its table rather
    than through the backlog, so a bug in one cannot mask a bug in the other.
    """
    tenant: Tenant = await make_tenant("inv-backlog-kind")
    headers = await auth_cookie(tenant.user)
    today = _today()
    async with client_for(tenant.host) as client:
        company_id = await _company(client, headers, "Klant BV")
        priced = await client.post(
            "/api/v1/domains/tld-prices", json={"tld": "nl", "amount": "12.50"}, headers=headers
        )
        assert priced.status_code == 200, priced.text
        domain = await client.post(
            "/api/v1/domains",
            json={
                "name": "klant.nl",
                "company_id": company_id,
                "start_date": add_months(today, -13).isoformat(),
            },
            headers=headers,
        )
        assert domain.status_code == 201, domain.text
        domain_id = domain.json()["id"]
        await _age_row(tenant.org.id, Domain, domain_id, add_months(today, -13))

        offered = (await _backlog(client, headers, source="domain"))["items"][0]
        line = {
            "line_kind": "domain",
            "description": "Domeinverlenging klant.nl",
            "quantity": "1",
            "unit_price": offered["amount"],
            "domain_id": domain_id,
            "period_start": offered["period_start"],
            "period_end": offered["period_end"],
        }
        created = await client.post(
            "/api/v1/invoicing/invoices",
            json={"company_id": company_id, "lines": [line]},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        invoice_id = created.json()["id"]
        assert created.json()["lines"][0]["line_kind"] == "domain"
        # The provenance round-trips, which is what lets a re-save reconcile instead of release.
        assert created.json()["lines"][0]["domain_id"] == domain_id

        async def claims() -> set[str]:
            async with async_session_maker() as session:
                await set_current_org(session, tenant.org.id)
                rows = await session.scalars(
                    select(InvoiceDomainPeriod).where(
                        InvoiceDomainPeriod.invoice_id == uuid_mod.UUID(invoice_id)
                    )
                )
                return {row.period_end.isoformat() for row in rows}

        assert await claims() == {offered["period_end"]}
        assert offered["period_end"] not in {
            i["period_end"] for i in (await _backlog(client, headers, source="domain"))["items"]
        }

        # Edit one word and save: the claim must still be there afterwards.
        edited = await client.patch(
            f"/api/v1/invoicing/invoices/{invoice_id}",
            json={"lines": [{**line, "description": "Domeinverlenging klant.nl (2026)"}]},
            headers=headers,
        )
        assert edited.status_code == 200, edited.text
        assert await claims() == {offered["period_end"]}

        # Remove the line and the year goes back to the cron — the other half of the rule.
        cleared = await client.patch(
            f"/api/v1/invoicing/invoices/{invoice_id}",
            json={
                "lines": [
                    {
                        "line_kind": "product",
                        "description": "Iets anders",
                        "quantity": "1",
                        "unit_price": "10.00",
                    }
                ]
            },
            headers=headers,
        )
        assert cleared.status_code == 200, cleared.text
        assert await claims() == set()
        assert offered["period_end"] in {
            i["period_end"] for i in (await _backlog(client, headers, source="domain"))["items"]
        }


async def test_a_pre_split_renewal_line_still_holds_its_claim(client_for) -> None:
    """The upgrade path: a draft whose renewal lines say ``subscription`` and name no period.

    Rows written before #302 carry the old kind, and the reconcile's legacy guard is what stops
    an unattributed document from releasing claims it is still billing. If the domain source
    stopped answering to ``subscription``, that guard would miss exactly those documents — the
    claim would be released on the next save and the cron would raise the year a second time.
    This drives the line back to the pre-split shape in the database and then saves through the
    API, which is what an agency does the morning after an upgrade.
    """
    tenant: Tenant = await make_tenant("inv-backlog-legacy")
    headers = await auth_cookie(tenant.user)
    today = _today()
    async with client_for(tenant.host) as client:
        company_id = await _company(client, headers, "Klant BV")
        priced = await client.post(
            "/api/v1/domains/tld-prices", json={"tld": "nl", "amount": "12.50"}, headers=headers
        )
        assert priced.status_code == 200, priced.text
        domain = await client.post(
            "/api/v1/domains",
            json={
                "name": "oud.nl",
                "company_id": company_id,
                "start_date": add_months(today, -13).isoformat(),
            },
            headers=headers,
        )
        assert domain.status_code == 201, domain.text
        domain_id = domain.json()["id"]
        await _age_row(tenant.org.id, Domain, domain_id, add_months(today, -13))

        offered = (await _backlog(client, headers, source="domain"))["items"][0]
        created = await client.post(
            "/api/v1/invoicing/invoices",
            json={
                "company_id": company_id,
                "lines": [
                    {
                        "line_kind": "domain",
                        "description": "Domeinverlenging oud.nl",
                        "quantity": "1",
                        "unit_price": offered["amount"],
                        "domain_id": domain_id,
                        "period_start": offered["period_start"],
                        "period_end": offered["period_end"],
                    }
                ],
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        invoice_id = created.json()["id"]

        # Rewind the row to what a pre-#302 release wrote: the old kind, no provenance.
        async with async_session_maker() as session:
            await set_current_org(session, tenant.org.id)
            row = (
                await session.execute(
                    select(InvoiceLine).where(
                        InvoiceLine.invoice_id == uuid_mod.UUID(invoice_id)
                    )
                )
            ).scalar_one()
            row.line_kind = "subscription"
            row.domain_id = None
            row.period_start = None
            row.period_end = None
            await session.commit()

        # Save the way the editor does — and *only* that way. The editor replaces lines
        # wholesale on every save, so it posts back what it was given; a PATCH that omits
        # ``lines`` never reaches the reconcile at all and would prove nothing here.
        current = await client.get(f"/api/v1/invoicing/invoices/{invoice_id}", headers=headers)
        assert current.status_code == 200, current.text
        posted = [
            {
                "line_kind": line["line_kind"],
                "description": line["description"],
                "quantity": line["quantity"],
                "unit_price": line["unit_price"],
                "subscription_id": line["subscription_id"],
                "domain_id": line["domain_id"],
                "period_start": line["period_start"],
                "period_end": line["period_end"],
            }
            for line in current.json()["lines"]
        ]
        assert posted[0]["line_kind"] == "subscription"
        assert posted[0]["domain_id"] is None and posted[0]["period_end"] is None
        resaved = await client.patch(
            f"/api/v1/invoicing/invoices/{invoice_id}",
            json={"lines": posted},
            headers=headers,
        )
        assert resaved.status_code == 200, resaved.text

        async with async_session_maker() as session:
            await set_current_org(session, tenant.org.id)
            held = list(
                await session.scalars(
                    select(InvoiceDomainPeriod).where(
                        InvoiceDomainPeriod.invoice_id == uuid_mod.UUID(invoice_id)
                    )
                )
            )
        assert len(held) == 1, "the legacy guard released a claim the document still bills"
        assert held[0].period_end.isoformat() == offered["period_end"]
