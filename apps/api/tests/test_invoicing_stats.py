"""``GET /invoicing/stats/revenue`` — the ledger's turnover for the Overzicht → Omzet page.

What is asserted: the year's totals excl./incl. tax beside the previous year's, a credit note
netting the month it was issued in, a cancelled document and a draft counting for nothing, the
per-client ranking, the per-kind split, the ``:any`` gate, a restricted membership's horizon,
and the cost — three grouped statements, never a row per document.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.db import async_session_maker, set_current_org
from tests.conftest import Tenant, add_membership, auth_cookie, make_tenant, org_today
from tests.test_invoicing_api import _company, _setup_org
from tests.test_task_subresources import add_member


async def _issued(
    client, headers, company_id: str, *, lines: list[dict], issue_date: date
) -> dict:
    created = await client.post(
        "/api/v1/invoicing/invoices",
        json={"company_id": company_id, "lines": lines},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    issued = await client.post(
        f"/api/v1/invoicing/invoices/{created.json()['id']}/issue",
        json={"issue_date": issue_date.isoformat()},
        headers=headers,
    )
    assert issued.status_code == 200, issued.text
    return issued.json()


async def test_revenue_stats_totals_clients_and_kinds(client_for, count_queries) -> None:
    tenant: Tenant = await make_tenant("inv-stats")
    headers = await auth_cookie(tenant.user)
    today = org_today()
    year = today.year
    # A fixed month so the month-series assertion never straddles New Year's Eve.
    this_year = date(year, 3, 15)
    last_year = date(year - 1, 3, 15)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        alpha = await _company(client, headers, "Alpha")
        beta = await _company(client, headers, "Beta")

        # Alpha, this year: 10 h × € 85 = € 850 hours + € 50 hosting → € 900 excl, € 1089 incl.
        await _issued(
            client,
            headers,
            alpha,
            lines=[
                {
                    "description": "Uren",
                    "quantity": "10",
                    "unit_price": "85",
                    "line_kind": "hours",
                },
                {
                    "description": "Hosting",
                    "quantity": "1",
                    "unit_price": "50",
                    "line_kind": "subscription",
                },
            ],
            issue_date=this_year,
        )
        # Beta, this year: € 200 excl — then credited in full, in the same month.
        beta_invoice = await _issued(
            client,
            headers,
            beta,
            lines=[{"description": "Werk", "quantity": "1", "unit_price": "200"}],
            issue_date=this_year,
        )
        credit = await client.post(
            f"/api/v1/invoicing/invoices/{beta_invoice['id']}/credit", headers=headers
        )
        assert credit.status_code in (200, 201), credit.text
        issued_credit = await client.post(
            f"/api/v1/invoicing/invoices/{credit.json()['id']}/issue",
            json={"issue_date": this_year.isoformat()},
            headers=headers,
        )
        assert issued_credit.status_code == 200, issued_credit.text
        # Beta, this year: € 300 excl, cancelled — not revenue.
        cancelled = await _issued(
            client,
            headers,
            beta,
            lines=[{"description": "Nooit", "quantity": "1", "unit_price": "300"}],
            issue_date=this_year,
        )
        assert (
            await client.post(
                f"/api/v1/invoicing/invoices/{cancelled['id']}/cancel", headers=headers
            )
        ).status_code == 200
        # A draft: not yet anything.
        draft = await client.post(
            "/api/v1/invoicing/invoices",
            json={
                "company_id": alpha,
                "lines": [{"description": "Concept", "quantity": "1", "unit_price": "999"}],
            },
            headers=headers,
        )
        assert draft.status_code == 201
        # Last year: Alpha € 400, Beta € 600 — Beta led then; Alpha leads now.
        await _issued(
            client,
            headers,
            alpha,
            lines=[{"description": "Vorig", "quantity": "1", "unit_price": "400"}],
            issue_date=last_year,
        )
        await _issued(
            client,
            headers,
            beta,
            lines=[
                {
                    "description": "Vorig",
                    "quantity": "1",
                    "unit_price": "600",
                    "line_kind": "hours",
                }
            ],
            issue_date=last_year,
        )

        with count_queries() as counter:
            res = await client.get(
                "/api/v1/invoicing/stats/revenue", params={"year": year}, headers=headers
            )
        assert res.status_code == 200, res.text
        stats = res.json()

        # This year nets to Alpha's € 900: Beta's € 200 is cancelled out by its credit note,
        # the cancelled € 300 never counted, the draft is not a document yet.
        assert stats["total_excl"] == 900.0
        assert stats["total_tax"] == 189.0
        assert stats["total_incl"] == 1089.0
        assert stats["months_excl"][2] == 900.0
        assert stats["months_incl"][2] == 1089.0
        assert sum(stats["months_excl"]) == 900.0
        assert stats["previous_excl"] == 1000.0
        assert stats["previous_incl"] == 1210.0
        assert stats["months_previous_excl"][2] == 1000.0
        # Three documents were issued this year: two invoices and one credit note.
        assert stats["invoice_count"] == 3
        assert stats["credited_excl"] == 200.0
        # Nothing paid yet: Alpha's € 1089 is owed; Beta's invoice was written off in full.
        assert stats["paid_incl"] == 0.0
        assert stats["outstanding_incl"] == 1089.0
        assert stats["outstanding_count"] == 1

        clients = stats["top_clients"]
        assert [c["name"] for c in clients] == ["Alpha", "Beta"]
        assert clients[0]["excl"] == 900.0
        assert clients[0]["incl"] == 1089.0
        assert clients[0]["previous_excl"] == 400.0
        assert clients[1]["excl"] == 0.0
        assert clients[1]["previous_excl"] == 600.0
        assert stats["other_excl"] == 0.0

        kinds = {k["kind"]: k for k in stats["by_kind"]}
        assert kinds["hours"]["excl"] == 850.0
        assert kinds["hours"]["previous_excl"] == 600.0
        assert kinds["subscription"]["excl"] == 50.0
        # Beta's € 200 product line and its − € 200 credit line net to nothing this year.
        assert kinds["product"]["excl"] == 0.0
        assert kinds["product"]["previous_excl"] == 400.0

        # Three aggregates plus the request's own context reads — never a row per document.
        assert len(counter) <= 12, "\n".join(counter.statements)

        # The year before has its own view, with this year as the "previous" of nothing.
        earlier = (
            await client.get(
                "/api/v1/invoicing/stats/revenue", params={"year": year - 1}, headers=headers
            )
        ).json()
        assert earlier["total_excl"] == 1000.0
        assert earlier["previous_excl"] == 0.0
        assert [c["name"] for c in earlier["top_clients"]] == ["Beta", "Alpha"]


async def test_revenue_stats_needs_the_module_scope(client_for) -> None:
    """``invoicing.invoice.read:own`` opens documents, never the agency's turnover (#266)."""
    tenant: Tenant = await make_tenant("inv-stats-scope")
    member = await add_member(tenant)
    member_headers = await auth_cookie(member)
    async with client_for(tenant.host) as client:
        res = await client.get(
            "/api/v1/invoicing/stats/revenue",
            params={"year": org_today().year},
            headers=member_headers,
        )
        assert res.status_code == 403


async def test_revenue_stats_follow_the_company_horizon(client_for) -> None:
    """A restricted manager (#285) reads the turnover of the clients they serve, not the org's."""
    tenant: Tenant = await make_tenant("inv-stats-horizon")
    restricted = await make_tenant("inv-stats-horizon-m", email="rm-inv-stats@example.com")
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        membership = await add_membership(
            session, tenant.org.id, restricted.user.id, role="admin"
        )
        membership_id = membership.id
        await session.commit()
    owner_headers = await auth_cookie(tenant.user)
    restricted_headers = await auth_cookie(restricted.user, org_id=tenant.org.id)
    year = org_today().year
    when = date(year, 6, 1)
    async with client_for(tenant.host) as client:
        await _setup_org(client, owner_headers)
        alpha = await _company(client, owner_headers, "Alpha")
        beta = await _company(client, owner_headers, "Beta")
        await _issued(
            client,
            owner_headers,
            alpha,
            lines=[{"description": "A", "quantity": "1", "unit_price": "100"}],
            issue_date=when,
        )
        await _issued(
            client,
            owner_headers,
            beta,
            lines=[{"description": "B", "quantity": "1", "unit_price": "250"}],
            issue_date=when,
        )
        group = (
            await client.post(
                "/api/v1/companies/groups", json={"name": "Noord"}, headers=owner_headers
            )
        ).json()
        assert (
            await client.put(
                f"/api/v1/companies/groups/{group['id']}/companies",
                json={"company_ids": [alpha]},
                headers=owner_headers,
            )
        ).status_code == 204
        assert (
            await client.put(
                f"/api/v1/companies/groups/{group['id']}/memberships",
                json={"membership_ids": [str(membership_id)]},
                headers=owner_headers,
            )
        ).status_code == 204

        whole = (
            await client.get(
                "/api/v1/invoicing/stats/revenue", params={"year": year}, headers=owner_headers
            )
        ).json()
        assert whole["total_excl"] == 350.0
        narrowed = (
            await client.get(
                "/api/v1/invoicing/stats/revenue",
                params={"year": year},
                headers=restricted_headers,
            )
        ).json()
        assert narrowed["total_excl"] == 100.0
        assert [c["name"] for c in narrowed["top_clients"]] == ["Alpha"]
        assert {k["kind"] for k in narrowed["by_kind"]} == {"product"}
        assert narrowed["by_kind"][0]["excl"] == 100.0


async def test_revenue_stats_year_is_validated(client_for) -> None:
    tenant: Tenant = await make_tenant("inv-stats-year")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        assert (
            await client.get(
                "/api/v1/invoicing/stats/revenue", params={"year": 1999}, headers=headers
            )
        ).status_code == 422
        empty = (
            await client.get(
                "/api/v1/invoicing/stats/revenue",
                params={"year": (org_today() + timedelta(days=400)).year},
                headers=headers,
            )
        ).json()
        assert empty["total_excl"] == 0.0
        assert empty["top_clients"] == []
        assert len(empty["months_excl"]) == 12
