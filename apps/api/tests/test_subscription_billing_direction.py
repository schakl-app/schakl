"""Which period a subscription invoice covers is the *kind's* decision (``billed_in_advance``).

The cycle cron's original reading — the invoice raised on the cycle date covers the period that
ends there — was written into the subscriptions module rather than onto the kind of thing sold,
so a hosting agreement renewing on 16-05-2026 could only ever offer "16-05-2025 – 16-05-2026",
and saying "everything up to 16-05-2026 is invoiced" then left it with no period at all. The
direction lives on the subscription type now, a standard subscription may say otherwise for the
agreements made from it, and one resolution (``billing_directions``) is read by the backlog, the
picker and the cron alike — a test per reader, plus one for the claims a flip has to carry along.
"""

from __future__ import annotations

from app.core import events
from app.core.billing import add_months
from app.modules.subscriptions.jobs import advance_subscriptions
from tests.conftest import auth_cookie, make_tenant, org_today


def _iso(day) -> str:
    return day.isoformat()


async def _company(client, headers, name: str = "Klant BV") -> str:
    resp = await client.post("/api/v1/companies", json={"name": name}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _hosting_type(client, headers) -> dict:
    types = (await client.get("/api/v1/subscriptions/types", headers=headers)).json()
    return next(row for row in types if row["key"] == "hosting")


async def _backlog_rows(client, headers, subscription_id: str) -> list[tuple[str, str, bool]]:
    backlog = (
        await client.get(
            "/api/v1/invoicing/recurring-backlog",
            params={"source": "subscription"},
            headers=headers,
        )
    ).json()
    return [
        (row["period_start"], row["period_end"], row["future"])
        for row in backlog["items"]
        if row["source_id"] == subscription_id
    ]


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


async def test_a_type_billed_in_advance_offers_the_year_ahead_and_the_cron_drafts_it(
    client_for,
) -> None:
    """The live case that found this: a yearly hosting agreement onboarded with its renewal
    date four months back and "invoiced up to" set to that same date. In arrears the only
    period it could name ended on that date and was therefore settled; billed in advance the
    renewal date opens the year ahead, which is what the agency meant and what the cron bills."""
    t = await make_tenant("direction-type")
    headers = await auth_cookie(t.user)
    today = org_today()
    renewal = add_months(today, -4)
    async with client_for(t.host) as c:
        hosting = await _hosting_type(c, headers)
        assert hosting["billed_in_advance"] is False  # the cron's original reading, untouched
        flipped = await c.patch(
            f"/api/v1/subscriptions/types/{hosting['id']}",
            json={"billed_in_advance": True},
            headers=headers,
        )
        assert flipped.status_code == 200, flipped.text
        assert flipped.json()["billed_in_advance"] is True
        assert flipped.json()["shifted_subscriptions"] == 0  # nothing of this kind existed yet

        company = await _company(c, headers)
        created = await c.post(
            "/api/v1/subscriptions",
            json={
                "company_id": company,
                "subscription_type_id": hosting["id"],
                "name": "Webhosting & Licenties",
                "status": "active",
                "interval": "yearly",
                "amount": "155.00",
                "start_date": _iso(add_months(renewal, -12)),
                "next_invoice_date": _iso(renewal),
                "billed_until": _iso(renewal),
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        sub = created.json()
        assert sub["billed_in_advance"] is True  # resolved through the type, read-only

        # The renewal date opens the year *ahead*, which "invoiced up to the renewal date"
        # leaves outstanding — and it is overdue, not "not yet due".
        assert await _backlog_rows(c, headers, sub["id"]) == [
            (_iso(renewal), _iso(add_months(renewal, 12)), False)
        ]
        # The editor's picker names the same year.
        outstanding = (
            await c.get(
                "/api/v1/invoicing/outstanding", params={"company_id": company}, headers=headers
            )
        ).json()
        offered = next(s for s in outstanding["subscriptions"] if s["id"] == sub["id"])
        assert [(p["period_start"], p["period_end"]) for p in offered["periods"]] == [
            (_iso(renewal), _iso(add_months(renewal, 12)))
        ]

    # The cron drafts that same year and moves the cycle on by one.
    fired = await _fired("subscription.due", advance_subscriptions)
    mine = [p for p in fired if str(p["subscription_id"]) == sub["id"]]
    assert [(p["period_start"], p["period_end"]) for p in mine] == [
        (_iso(renewal), _iso(add_months(renewal, 12)))
    ]
    async with client_for(t.host) as c:
        after = (await c.get(f"/api/v1/subscriptions/{sub['id']}", headers=headers)).json()
        assert after["next_invoice_date"] == _iso(add_months(renewal, 12))


async def test_a_standard_subscription_overrides_its_type_and_null_follows_it_again(
    client_for,
) -> None:
    t = await make_tenant("direction-template")
    headers = await auth_cookie(t.user)
    today = org_today()
    anchor = add_months(today, -1)
    async with client_for(t.host) as c:
        hosting = await _hosting_type(c, headers)  # in arrears, the default
        template = await c.post(
            "/api/v1/subscriptions/templates",
            json={
                "name": "Licentie",
                "subscription_type_id": hosting["id"],
                "interval": "monthly",
                "amount": "20.00",
                "billed_in_advance": True,
            },
            headers=headers,
        )
        assert template.status_code == 201, template.text
        assert template.json()["billed_in_advance"] is True
        company = await _company(c, headers)
        sub = (
            await c.post(
                "/api/v1/subscriptions",
                json={
                    "company_id": company,
                    "subscription_type_id": hosting["id"],
                    "subscription_template_id": template.json()["id"],
                    "name": "Licentie",
                    "status": "active",
                    "interval": "monthly",
                    "amount": "20.00",
                    "start_date": _iso(add_months(anchor, -6)),
                    "next_invoice_date": _iso(anchor),
                },
                headers=headers,
            )
        ).json()
        assert sub["billed_in_advance"] is True  # the preset's say wins over the type's
        rows = await _backlog_rows(c, headers, sub["id"])
        # The anchor's month ahead, and the month after it that the calendar has since passed.
        assert rows == [
            (_iso(anchor), _iso(add_months(anchor, 1)), False),
            (_iso(add_months(anchor, 1)), _iso(add_months(anchor, 2)), False),
        ]

        # Back to following the type: an explicit null, and the agreements made from the
        # preset are reported as re-read.
        reverted = await c.patch(
            f"/api/v1/subscriptions/templates/{template.json()['id']}",
            json={"billed_in_advance": None},
            headers=headers,
        )
        assert reverted.status_code == 200, reverted.text
        assert reverted.json()["billed_in_advance"] is None
        assert reverted.json()["shifted_subscriptions"] == 1
        after = (await c.get(f"/api/v1/subscriptions/{sub['id']}", headers=headers)).json()
        assert after["billed_in_advance"] is False
        rows = await _backlog_rows(c, headers, sub["id"])
        assert rows == [
            (_iso(add_months(anchor, -1)), _iso(anchor), False),
            (_iso(anchor), _iso(add_months(anchor, 1)), False),
        ]


async def test_flipping_a_type_moves_the_periods_already_invoiced_with_it(client_for) -> None:
    """A claim says "boundary B is billed" as ``period_end = B``; read the other way round that
    names the boundary a month earlier, and the month the document actually paid for would be
    offered again. So the flip carries the claims — and the lines' provenance — with it, in the
    same transaction, and reports how many agreements it reached."""
    t = await make_tenant("direction-claims")
    headers = await auth_cookie(t.user)
    today = org_today()
    anchor = add_months(today, -2)
    async with client_for(t.host) as c:
        await c.put(
            "/api/v1/invoicing/settings",
            json={"company_details": {"name": "Agency BV", "country": "NL"}},
            headers=headers,
        )
        await c.get("/api/v1/invoicing/tax-rates", headers=headers)
        hosting = await _hosting_type(c, headers)
        company = await _company(c, headers)
        sub = (
            await c.post(
                "/api/v1/subscriptions",
                json={
                    "company_id": company,
                    "subscription_type_id": hosting["id"],
                    "name": "Hosting",
                    "status": "active",
                    "interval": "monthly",
                    "amount": "25.00",
                    "start_date": _iso(add_months(anchor, -6)),
                    "next_invoice_date": _iso(anchor),
                },
                headers=headers,
            )
        ).json()
        # Bill the anchor's period by hand, in arrears: [anchor − 1m, anchor].
        outstanding = (
            await c.get(
                "/api/v1/invoicing/outstanding", params={"company_id": company}, headers=headers
            )
        ).json()
        offered = next(s for s in outstanding["subscriptions"] if s["id"] == sub["id"])
        period = offered["periods"][0]
        assert (period["period_start"], period["period_end"]) == (
            _iso(add_months(anchor, -1)),
            _iso(anchor),
        )
        invoice = await c.post(
            "/api/v1/invoicing/invoices",
            json={
                "company_id": company,
                "lines": [
                    {
                        "description": "Hosting",
                        "quantity": "1",
                        "unit_price": "25.00",
                        "line_kind": "subscription",
                        "subscription_id": sub["id"],
                        "period_start": period["period_start"],
                        "period_end": period["period_end"],
                    }
                ],
            },
            headers=headers,
        )
        assert invoice.status_code == 201, invoice.text
        assert (_iso(add_months(anchor, -1)), _iso(anchor), False) not in await _backlog_rows(
            c, headers, sub["id"]
        )

        flipped = await c.patch(
            f"/api/v1/subscriptions/types/{hosting['id']}",
            json={"billed_in_advance": True},
            headers=headers,
        )
        assert flipped.status_code == 200, flipped.text
        assert flipped.json()["shifted_subscriptions"] == 1

        # The claim now names the month the document paid for under the new reading...
        billed = (
            await c.get(
                "/api/v1/invoicing/billed-periods",
                params={"source": "subscription", "source_id": sub["id"]},
                headers=headers,
            )
        ).json()
        assert [(r["period_start"], r["period_end"]) for r in billed] == [
            (_iso(anchor), _iso(add_months(anchor, 1)))
        ]
        # ...so the backlog does not offer it again, and does offer the month after it.
        rows = await _backlog_rows(c, headers, sub["id"])
        assert (_iso(anchor), _iso(add_months(anchor, 1)), False) not in rows
        assert (_iso(add_months(anchor, 1)), _iso(add_months(anchor, 2)), False) in rows
        # The document's own line carries the same provenance.
        detail = (
            await c.get(f"/api/v1/invoicing/invoices/{invoice.json()['id']}", headers=headers)
        ).json()
        assert (detail["lines"][0]["period_start"], detail["lines"][0]["period_end"]) == (
            _iso(anchor),
            _iso(add_months(anchor, 1)),
        )

        # And back: the reverse flip walks the claim back to where it was.
        back = await c.patch(
            f"/api/v1/subscriptions/types/{hosting['id']}",
            json={"billed_in_advance": False},
            headers=headers,
        )
        assert back.json()["shifted_subscriptions"] == 1
        billed = (
            await c.get(
                "/api/v1/invoicing/billed-periods",
                params={"source": "subscription", "source_id": sub["id"]},
                headers=headers,
            )
        ).json()
        assert [(r["period_start"], r["period_end"]) for r in billed] == [
            (_iso(add_months(anchor, -1)), _iso(anchor))
        ]


async def test_an_agreement_overrides_its_preset_and_type_and_null_follows_them_again(
    client_for,
) -> None:
    """The third layer: one client negotiated the other arrangement. Its own say wins over the
    preset's and the type's, an explicit null hands the decision back to them, and a flip of
    the *type* leaves an agreement that decided for itself alone — and reports it as such."""
    t = await make_tenant("direction-agreement")
    headers = await auth_cookie(t.user)
    today = org_today()
    anchor = add_months(today, -1)
    async with client_for(t.host) as c:
        hosting = await _hosting_type(c, headers)  # in arrears, the default
        template = await c.post(
            "/api/v1/subscriptions/templates",
            json={
                "name": "Licentie",
                "subscription_type_id": hosting["id"],
                "interval": "monthly",
                "amount": "20.00",
                "billed_in_advance": True,
            },
            headers=headers,
        )
        assert template.status_code == 201, template.text
        company = await _company(c, headers)
        sub = (
            await c.post(
                "/api/v1/subscriptions",
                json={
                    "company_id": company,
                    "subscription_type_id": hosting["id"],
                    "subscription_template_id": template.json()["id"],
                    "name": "Licentie",
                    "status": "active",
                    "interval": "monthly",
                    "amount": "20.00",
                    "start_date": _iso(add_months(anchor, -6)),
                    "next_invoice_date": _iso(anchor),
                    "billed_in_advance_override": False,
                },
                headers=headers,
            )
        ).json()
        # The preset says advance; this one agreement says arrears, and that is what it does.
        assert sub["billed_in_advance_override"] is False
        assert sub["billed_in_advance"] is False
        assert await _backlog_rows(c, headers, sub["id"]) == [
            (_iso(add_months(anchor, -1)), _iso(anchor), False),
            (_iso(anchor), _iso(add_months(anchor, 1)), False),
        ]

        # A PATCH naming other fields leaves the override alone (absent means leave alone).
        untouched = await c.patch(
            f"/api/v1/subscriptions/{sub['id']}", json={"name": "Licentie Pro"}, headers=headers
        )
        assert untouched.json()["billed_in_advance_override"] is False

        # An explicit null hands the decision back to the preset, and the periods follow.
        reverted = await c.patch(
            f"/api/v1/subscriptions/{sub['id']}",
            json={"billed_in_advance_override": None},
            headers=headers,
        )
        assert reverted.status_code == 200, reverted.text
        assert reverted.json()["billed_in_advance_override"] is None
        assert reverted.json()["billed_in_advance"] is True
        assert await _backlog_rows(c, headers, sub["id"]) == [
            (_iso(anchor), _iso(add_months(anchor, 1)), False),
            (_iso(add_months(anchor, 1)), _iso(add_months(anchor, 2)), False),
        ]

        # Decided for itself again — and now a flip of the *type* reaches every agreement of
        # the kind except this one, which is what "shifted" has to count.
        decided = await c.patch(
            f"/api/v1/subscriptions/{sub['id']}",
            json={"billed_in_advance_override": True},
            headers=headers,
        )
        assert decided.json()["billed_in_advance"] is True
        flipped = await c.patch(
            f"/api/v1/subscriptions/types/{hosting['id']}",
            json={"billed_in_advance": True},
            headers=headers,
        )
        assert flipped.status_code == 200, flipped.text
        assert flipped.json()["shifted_subscriptions"] == 0
        after = (await c.get(f"/api/v1/subscriptions/{sub['id']}", headers=headers)).json()
        assert after["billed_in_advance"] is True


async def test_an_agreement_s_own_flip_moves_its_invoiced_periods_with_it(client_for) -> None:
    """The claim shift the type and preset flips carry, one layer down: an agreement that
    states its own direction moves what it already invoiced in the same transaction."""
    t = await make_tenant("direction-agreement-claims")
    headers = await auth_cookie(t.user)
    today = org_today()
    anchor = add_months(today, -2)
    async with client_for(t.host) as c:
        await c.put(
            "/api/v1/invoicing/settings",
            json={"company_details": {"name": "Agency BV", "country": "NL"}},
            headers=headers,
        )
        await c.get("/api/v1/invoicing/tax-rates", headers=headers)
        hosting = await _hosting_type(c, headers)
        company = await _company(c, headers)
        sub = (
            await c.post(
                "/api/v1/subscriptions",
                json={
                    "company_id": company,
                    "subscription_type_id": hosting["id"],
                    "name": "Hosting",
                    "status": "active",
                    "interval": "monthly",
                    "amount": "25.00",
                    "start_date": _iso(add_months(anchor, -6)),
                    "next_invoice_date": _iso(anchor),
                },
                headers=headers,
            )
        ).json()
        invoice = await c.post(
            "/api/v1/invoicing/invoices",
            json={
                "company_id": company,
                "lines": [
                    {
                        "description": "Hosting",
                        "quantity": "1",
                        "unit_price": "25.00",
                        "line_kind": "subscription",
                        "subscription_id": sub["id"],
                        "period_start": _iso(add_months(anchor, -1)),
                        "period_end": _iso(anchor),
                    }
                ],
            },
            headers=headers,
        )
        assert invoice.status_code == 201, invoice.text

        flipped = await c.patch(
            f"/api/v1/subscriptions/{sub['id']}",
            json={"billed_in_advance_override": True},
            headers=headers,
        )
        assert flipped.status_code == 200, flipped.text
        billed = (
            await c.get(
                "/api/v1/invoicing/billed-periods",
                params={"source": "subscription", "source_id": sub["id"]},
                headers=headers,
            )
        ).json()
        assert [(r["period_start"], r["period_end"]) for r in billed] == [
            (_iso(anchor), _iso(add_months(anchor, 1)))
        ]
        rows = await _backlog_rows(c, headers, sub["id"])
        assert (_iso(anchor), _iso(add_months(anchor, 1)), False) not in rows
        assert (_iso(add_months(anchor, 1)), _iso(add_months(anchor, 2)), False) in rows

        # And back to following the type: the claim walks back with it.
        back = await c.patch(
            f"/api/v1/subscriptions/{sub['id']}",
            json={"billed_in_advance_override": None},
            headers=headers,
        )
        assert back.json()["billed_in_advance"] is False
        billed = (
            await c.get(
                "/api/v1/invoicing/billed-periods",
                params={"source": "subscription", "source_id": sub["id"]},
                headers=headers,
            )
        ).json()
        assert [(r["period_start"], r["period_end"]) for r in billed] == [
            (_iso(add_months(anchor, -1)), _iso(anchor))
        ]
