"""``GET /invoicing/stats/revenue`` — the ledger's turnover for the Overzicht → Omzet page.

What is asserted: the year's totals excl./incl. tax beside the previous year's, a credit note
netting the month it was issued in, a cancelled document and a draft counting for nothing, the
per-client ranking, the per-kind split, the ``:any`` gate, a restricted membership's horizon,
and the cost — three grouped statements, never a row per document.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.core.periods import calendar_span, previous_calendar_span
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


# --- VAT per return period ------------------------------------------------------------- #
async def _vat_fixture(client, headers) -> tuple[date, date]:
    """One invoice in the current quarter, one in the quarter before, one older than both.

    Dated on the second day of each span so a run on the first or last day of a quarter still
    lands every document where the comment says it is.
    """
    today = org_today()
    current, _ = calendar_span("quarter", today)
    previous, _ = previous_calendar_span("quarter", today)
    await _setup_org(client, headers)
    alpha = await _company(client, headers, "Alpha")
    for when, price in (
        (current + timedelta(days=1), "1000"),  # € 210 tax, this quarter
        (previous + timedelta(days=1), "500"),  # € 105 tax, last quarter
        (previous - timedelta(days=40), "300"),  # older than both: in neither figure
    ):
        await _issued(
            client,
            headers,
            alpha,
            lines=[{"description": "Werk", "quantity": "1", "unit_price": price}],
            issue_date=when,
        )
    # A draft is not yet anything — it must not reach a return.
    draft = await client.post(
        "/api/v1/invoicing/invoices",
        json={
            "company_id": alpha,
            "lines": [{"description": "Concept", "quantity": "1", "unit_price": "9999"}],
        },
        headers=headers,
    )
    assert draft.status_code == 201, draft.text
    return current, previous


async def test_vat_stats_sum_the_orgs_return_period_in_one_statement(
    client_for, count_queries
) -> None:
    tenant: Tenant = await make_tenant("inv-vat")
    headers = await auth_cookie(tenant.user)
    today = org_today()
    async with client_for(tenant.host) as client:
        current, previous = await _vat_fixture(client, headers)
        with count_queries() as counter:
            res = await client.get("/api/v1/invoicing/stats/vat", headers=headers)
        assert res.status_code == 200, res.text
        body = res.json()
        # Nobody set anything: the seeded period is the quarter, and the span is the whole one.
        assert body["period"] == "quarter"
        assert body["current"] == {
            "start": current.isoformat(),
            "end": calendar_span("quarter", today)[1].isoformat(),
            "tax": 210.0,
            "excl": 1000.0,
            "invoice_count": 1,
        }
        assert body["previous"] == {
            "start": previous.isoformat(),
            "end": (current - timedelta(days=1)).isoformat(),
            "tax": 105.0,
            "excl": 500.0,
            "invoice_count": 1,
        }
        # One statement over the documents, however many there are.
        assert len(counter.matching("from invoices")) == 1
        assert len(counter) <= 8

        # The year is asked for by name and folds every document dated inside it.
        year = (
            await client.get(
                "/api/v1/invoicing/stats/vat", params={"period": "year"}, headers=headers
            )
        ).json()
        assert year["period"] == "year"
        assert year["current"]["start"] == date(today.year, 1, 1).isoformat()
        assert year["current"]["end"] == date(today.year, 12, 31).isoformat()
        assert (
            await client.get(
                "/api/v1/invoicing/stats/vat", params={"period": "week"}, headers=headers
            )
        ).status_code == 422


async def test_vat_stats_follow_the_setting_and_a_credit_note_nets_its_period(
    client_for,
) -> None:
    tenant: Tenant = await make_tenant("inv-vat-setting")
    headers = await auth_cookie(tenant.user)
    today = org_today()
    month_start, month_end = calendar_span("month", today)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        alpha = await _company(client, headers, "Alpha")
        invoice = await _issued(
            client,
            headers,
            alpha,
            lines=[{"description": "Werk", "quantity": "1", "unit_price": "1000"}],
            issue_date=month_start,
        )
        saved = await client.put(
            "/api/v1/invoicing/settings", json={"vat_period": "month"}, headers=headers
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["vat_period"] == "month"
        body = (await client.get("/api/v1/invoicing/stats/vat", headers=headers)).json()
        assert body["period"] == "month"
        assert body["current"]["start"] == month_start.isoformat()
        assert body["current"]["end"] == month_end.isoformat()
        assert body["current"]["tax"] == 210.0

        credited = await client.post(
            f"/api/v1/invoicing/invoices/{invoice['id']}/credit",
            json={"issue": True},
            headers=headers,
        )
        assert credited.status_code in (200, 201), credited.text
        after = (await client.get("/api/v1/invoicing/stats/vat", headers=headers)).json()
        # The credit note is issued today, which is inside this month: the period nets to zero
        # and counts two documents, exactly as the revenue report does.
        assert after["current"]["tax"] == 0.0
        assert after["current"]["invoice_count"] == 2

        refused = await client.put(
            "/api/v1/invoicing/settings", json={"vat_period": "weekly"}, headers=headers
        )
        assert refused.status_code == 422


async def test_vat_stats_need_the_module_scope_and_follow_the_horizon(client_for) -> None:
    tenant: Tenant = await make_tenant("inv-vat-scope")
    member = await add_member(tenant)
    restricted = await make_tenant("inv-vat-scope-m", email="rm-inv-vat@example.com")
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        membership = await add_membership(
            session, tenant.org.id, restricted.user.id, role="admin"
        )
        membership_id = membership.id
        await session.commit()
    owner_headers = await auth_cookie(tenant.user)
    restricted_headers = await auth_cookie(restricted.user, org_id=tenant.org.id)
    when = calendar_span("quarter", org_today())[0]
    async with client_for(tenant.host) as client:
        assert (
            await client.get("/api/v1/invoicing/stats/vat", headers=await auth_cookie(member))
        ).status_code == 403
        await _setup_org(client, owner_headers)
        alpha = await _company(client, owner_headers, "Alpha")
        beta = await _company(client, owner_headers, "Beta")
        for company, price in ((alpha, "100"), (beta, "250")):
            await _issued(
                client,
                owner_headers,
                company,
                lines=[{"description": "X", "quantity": "1", "unit_price": price}],
                issue_date=when,
            )
        group = (
            await client.post(
                "/api/v1/companies/groups", json={"name": "Noord"}, headers=owner_headers
            )
        ).json()
        await client.put(
            f"/api/v1/companies/groups/{group['id']}/companies",
            json={"company_ids": [alpha]},
            headers=owner_headers,
        )
        await client.put(
            f"/api/v1/companies/groups/{group['id']}/memberships",
            json={"membership_ids": [str(membership_id)]},
            headers=owner_headers,
        )
        whole = (await client.get("/api/v1/invoicing/stats/vat", headers=owner_headers)).json()
        assert whole["current"]["tax"] == 73.5
        narrowed = (
            await client.get("/api/v1/invoicing/stats/vat", headers=restricted_headers)
        ).json()
        assert narrowed["current"]["tax"] == 21.0


def test_calendar_spans_are_whole_and_step_over_a_year() -> None:
    assert calendar_span("month", date(2028, 2, 10)) == (date(2028, 2, 1), date(2028, 2, 29))
    assert calendar_span("quarter", date(2026, 9, 27)) == (date(2026, 7, 1), date(2026, 9, 30))
    assert calendar_span("year", date(2026, 9, 27)) == (date(2026, 1, 1), date(2026, 12, 31))
    assert previous_calendar_span("month", date(2026, 1, 3)) == (
        date(2025, 12, 1),
        date(2025, 12, 31),
    )
    assert previous_calendar_span("quarter", date(2026, 2, 3)) == (
        date(2025, 10, 1),
        date(2025, 12, 31),
    )
    assert previous_calendar_span("year", date(2026, 2, 3)) == (
        date(2025, 1, 1),
        date(2025, 12, 31),
    )
