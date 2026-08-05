"""What an invoice line *bills*, and that it survives being saved again.

The regression these tests exist for: the editor replaces a document's lines wholesale on
every save, so a line that could not carry its own provenance came back from the browser
having forgotten what it billed. The service dutifully rebuilt the claim tables from those
amnesiac lines, released the period, and the cycle cron billed the client a second time a
week later. The hours half was worse: ``update`` never linked or released entries at all, so
an hours line added to a draft billed nothing and an hours line removed from one left its
entries stamped invoiced with no line billing them — unbillable without a database edit.

So the line is now the record and the claim tables are rebuilt from it, which is exactly the
kind of change whose correctness is invisible in a single request. Every test here is a
round trip: write, read back, write what was read.
"""

from __future__ import annotations

import uuid as uuid_mod
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.db import async_session_maker, set_current_org
from tests.conftest import Tenant, auth_cookie, make_tenant
from tests.test_invoicing_api import _company, _setup_org

AMS = ZoneInfo("Europe/Amsterdam")


def _today():
    return datetime.now(AMS).date()


async def _subscription(client, headers, company_id: str) -> dict:
    """A monthly agreement whose next cycle falls today — one period on offer, priced."""
    today = _today()
    resp = await client.post(
        "/api/v1/subscriptions",
        json={
            "company_id": company_id,
            "name": "Hosting & onderhoud",
            "status": "active",
            "interval": "monthly",
            "start_date": (today - timedelta(days=40)).isoformat(),
            "next_invoice_date": today.isoformat(),
            "amount": "249.00",
            "lines": [{"description": "Hosting", "quantity": "1", "unit_amount": "249.00"}],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _offer(client, headers, company_id: str, subscription_id: str) -> dict:
    """The newest period the picker offers for this agreement."""
    body = (
        await client.get(
            "/api/v1/invoicing/outstanding",
            params={"company_id": company_id},
            headers=headers,
        )
    ).json()
    agreement = next(a for a in body["subscriptions"] if a["id"] == subscription_id)
    return agreement["periods"][-1]


async def _already_billed(
    client, headers, company_id: str, subscription_id: str, period_end: str
) -> bool:
    """Does a document hold this period? The picker's own answer, not a peek at the table."""
    body = (
        await client.get(
            "/api/v1/invoicing/outstanding",
            params={"company_id": company_id},
            headers=headers,
        )
    ).json()
    agreement = next(a for a in body["subscriptions"] if a["id"] == subscription_id)
    period = next(p for p in agreement["periods"] if p["period_end"] == period_end)
    return period["already_billed"]


def _repost(line: dict) -> dict:
    """A line read back from the API, shaped as the editor re-posts it.

    Deliberately the *whole* row: the browser round-trips what it was given, so anything the
    read forgot is silently dropped on the next save. That is precisely the failure mode.
    """
    return {
        key: value
        for key, value in line.items()
        if key not in ("id", "position", "amount", "tax_rate_pct", "tax_name", "tax_category")
    }


async def _billable_entries(client, headers, company_id: str, minutes: tuple[int, ...]):
    """Approved + billable time on one project — the set the Hours picker draws from."""
    project = (
        await client.post(
            "/api/v1/projects",
            json={"name": "Retainer", "company_id": company_id},
            headers=headers,
        )
    ).json()
    start = datetime.now(UTC) - timedelta(days=2)
    entry_ids: list[str] = []
    for count in minutes:
        entry = await client.post(
            "/api/v1/time/entries",
            json={
                "company_id": company_id,
                "project_id": project["id"],
                "description": "Werkzaamheden",
                "started_at": start.isoformat(),
                "minutes": count,
                "billable": True,
            },
            headers=headers,
        )
        assert entry.status_code == 201, entry.text
        entry_ids.append(entry.json()["id"])
    approved = await client.post(
        "/api/v1/time/entries/approve",
        json={"entry_ids": entry_ids, "approved": True},
        headers=headers,
    )
    assert approved.status_code == 200, approved.text
    return project, entry_ids


async def _unbilled_ids(client, headers, company_id: str) -> set[str]:
    body = (
        await client.get(
            f"/api/v1/invoicing/unbilled?company_id={company_id}", headers=headers
        )
    ).json()
    return {entry["id"] for entry in body["entries"]}


async def test_re_saving_a_draft_keeps_the_period_it_claims(client_for) -> None:
    """The bug in one request: open a draft, change nothing, press save.

    The editor posts the lines it was handed, so the read has to carry the claim or the write
    releases it. Before the line carried its own provenance the round trip lost it, the period
    went back on offer, and ``subscription.due`` billed the client a second time — from a draft
    that still had the month on it, in words, on screen.
    """
    t: Tenant = await make_tenant("inv-prov-resave")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _setup_org(c, headers)
        company_id = await _company(c, headers)
        sub = await _subscription(c, headers, company_id)
        offer = await _offer(c, headers, company_id, sub["id"])

        created = await c.post(
            "/api/v1/invoicing/invoices",
            json={
                "company_id": company_id,
                "lines": [
                    {
                        "description": "Hosting & onderhoud",
                        "line_kind": "subscription",
                        "quantity": "1",
                        "unit_price": "249",
                        "subscription_id": sub["id"],
                        "period_start": offer["period_start"],
                        "period_end": offer["period_end"],
                    },
                    {"description": "Extra werk", "line_kind": "hours",
                     "quantity": "2", "unit_price": "95"},
                ],
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        invoice_id = created.json()["id"]
        assert await _already_billed(
            c, headers, company_id, sub["id"], offer["period_end"]
        )

        # The read echoes what the line bills — without this the editor cannot re-post it.
        read = (
            await c.get(f"/api/v1/invoicing/invoices/{invoice_id}", headers=headers)
        ).json()
        claimed = next(
            line for line in read["lines"] if line["line_kind"] == "subscription"
        )
        assert claimed["subscription_id"] == sub["id"]
        assert claimed["period_start"] == offer["period_start"]
        assert claimed["period_end"] == offer["period_end"]

        # Save with no edits at all: exactly the lines the API just returned.
        resaved = await c.patch(
            f"/api/v1/invoicing/invoices/{invoice_id}",
            json={"lines": [_repost(line) for line in read["lines"]]},
            headers=headers,
        )
        assert resaved.status_code == 200, resaved.text

        assert await _already_billed(
            c, headers, company_id, sub["id"], offer["period_end"]
        )
        # And the claim still travels on the line, so the *next* save is safe too.
        again = (
            await c.get(f"/api/v1/invoicing/invoices/{invoice_id}", headers=headers)
        ).json()
        assert next(
            line for line in again["lines"] if line["line_kind"] == "subscription"
        )["period_end"] == offer["period_end"]


async def test_dropping_the_subscription_line_hands_the_period_back(client_for) -> None:
    """The other half of the same rule: the claim is rebuilt from the lines that survive.

    Deleting the line the client is no longer being billed for has to put the month back on
    the cron's list, or an agency that corrects a draft silently never bills that month again.
    """
    t: Tenant = await make_tenant("inv-prov-drop")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _setup_org(c, headers)
        company_id = await _company(c, headers)
        sub = await _subscription(c, headers, company_id)
        offer = await _offer(c, headers, company_id, sub["id"])

        invoice_id = (
            await c.post(
                "/api/v1/invoicing/invoices",
                json={
                    "company_id": company_id,
                    "lines": [
                        {
                            "description": "Hosting & onderhoud",
                            "line_kind": "subscription",
                            "quantity": "1",
                            "unit_price": "249",
                            "subscription_id": sub["id"],
                            "period_start": offer["period_start"],
                            "period_end": offer["period_end"],
                        },
                        {"description": "Extra werk", "line_kind": "hours",
                         "quantity": "2", "unit_price": "95"},
                    ],
                },
                headers=headers,
            )
        ).json()["id"]
        assert await _already_billed(
            c, headers, company_id, sub["id"], offer["period_end"]
        )

        dropped = await c.patch(
            f"/api/v1/invoicing/invoices/{invoice_id}",
            json={
                "lines": [
                    {"description": "Extra werk", "line_kind": "hours",
                     "quantity": "2", "unit_price": "95"},
                ]
            },
            headers=headers,
        )
        assert dropped.status_code == 200, dropped.text
        assert not await _already_billed(
            c, headers, company_id, sub["id"], offer["period_end"]
        )


async def test_editing_a_draft_bills_and_un_bills_hours(client_for) -> None:
    """``update`` never touched the hours at all — this is the half that was missing.

    Adding an hours line to an existing draft left the entry outstanding (it would be billed
    again on the next build), and removing one left the entry stamped invoiced with no line
    billing it: permanently unbillable without a database edit. Both directions now follow
    the lines.
    """
    t: Tenant = await make_tenant("inv-prov-hours")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _setup_org(c, headers)
        company_id = await _company(c, headers)
        await c.put(
            f"/api/v1/leave/rate/{t.user.id}", json={"hourly_rate": "90.00"}, headers=headers
        )
        _, entry_ids = await _billable_entries(c, headers, company_id, (90, 30))
        assert await _unbilled_ids(c, headers, company_id) == set(entry_ids)

        # A draft that bills nothing yet — the invoice someone starts by hand.
        invoice_id = (
            await c.post(
                "/api/v1/invoicing/invoices",
                json={"company_id": company_id, "lines": []},
                headers=headers,
            )
        ).json()["id"]
        assert await _unbilled_ids(c, headers, company_id) == set(entry_ids)

        added = await c.patch(
            f"/api/v1/invoicing/invoices/{invoice_id}",
            json={
                "lines": [
                    {"description": "Uren", "line_kind": "hours", "quantity": "1.50",
                     "unit": "uur", "unit_price": "90", "time_entry_ids": [entry_ids[0]]},
                ]
            },
            headers=headers,
        )
        assert added.status_code == 200, added.text
        assert await _unbilled_ids(c, headers, company_id) == {entry_ids[1]}
        # The line says which entries it bills, so the next save can say it again.
        assert added.json()["lines"][0]["time_entry_ids"] == [entry_ids[0]]

        removed = await c.patch(
            f"/api/v1/invoicing/invoices/{invoice_id}",
            json={"lines": []},
            headers=headers,
        )
        assert removed.status_code == 200, removed.text
        assert await _unbilled_ids(c, headers, company_id) == set(entry_ids)


async def test_a_grouped_hours_line_carries_every_entry_it_covers(client_for) -> None:
    """"24 uur, Project X" is one line over many entries, so provenance is a list.

    ``from_time`` groups per project by design; a line that could only remember one entry
    would release thirteen of fourteen on the first edit. The whole group travels, and
    deleting the draft gives all of them back.
    """
    t: Tenant = await make_tenant("inv-prov-group")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _setup_org(c, headers)
        company_id = await _company(c, headers)
        await c.put(
            f"/api/v1/leave/rate/{t.user.id}", json={"hourly_rate": "90.00"}, headers=headers
        )
        _, entry_ids = await _billable_entries(c, headers, company_id, (90, 30, 60))

        built = await c.post(
            "/api/v1/invoicing/invoices/from-time",
            json={"company_id": company_id, "group_by": "project"},
            headers=headers,
        )
        assert built.status_code == 201, built.text
        invoice_id = built.json()["id"]

        read = (
            await c.get(f"/api/v1/invoicing/invoices/{invoice_id}", headers=headers)
        ).json()
        assert len(read["lines"]) == 1  # one project, one rate, one line
        line = read["lines"][0]
        assert line["line_kind"] == "hours"
        assert sorted(line["time_entry_ids"]) == sorted(entry_ids)
        assert await _unbilled_ids(c, headers, company_id) == set()

        # Re-posting the grouped line keeps all three: the list survives the round trip.
        resaved = await c.patch(
            f"/api/v1/invoicing/invoices/{invoice_id}",
            json={"lines": [_repost(row) for row in read["lines"]]},
            headers=headers,
        )
        assert resaved.status_code == 200, resaved.text
        assert await _unbilled_ids(c, headers, company_id) == set()

        assert (
            await c.delete(f"/api/v1/invoicing/invoices/{invoice_id}", headers=headers)
        ).status_code == 204
        assert await _unbilled_ids(c, headers, company_id) == set(entry_ids)


async def test_a_pre_upgrade_draft_keeps_its_claim(client_for) -> None:
    """The guard that stops the upgrade from re-introducing the bug it fixes.

    A draft written before lines carried provenance has subscription-kind lines that name no
    period. Reading that as "this document claims nothing" would release a claim it is still
    billing on the very first save after the upgrade — the cron would then raise the month
    again, which is the entire defect. So an unattributed document keeps its claims until
    someone edits the lines that carry them.
    """
    t: Tenant = await make_tenant("inv-prov-legacy")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _setup_org(c, headers)
        company_id = await _company(c, headers)
        sub = await _subscription(c, headers, company_id)
        offer = await _offer(c, headers, company_id, sub["id"])

        invoice_id = (
            await c.post(
                "/api/v1/invoicing/invoices",
                json={
                    "company_id": company_id,
                    "lines": [
                        {
                            "description": "Hosting & onderhoud",
                            "line_kind": "subscription",
                            "quantity": "1",
                            "unit_price": "249",
                            "subscription_id": sub["id"],
                            "period_start": offer["period_start"],
                            "period_end": offer["period_end"],
                        },
                    ],
                },
                headers=headers,
            )
        ).json()["id"]
        assert await _already_billed(
            c, headers, company_id, sub["id"], offer["period_end"]
        )

    # Rewind the row to what the previous release stored: a claim in its own table, and a
    # line that cannot say it holds one.
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        await session.execute(
            text(
                "UPDATE invoice_lines SET subscription_id = NULL, period_start = NULL,"
                " period_end = NULL WHERE invoice_id = :iid"
            ),
            {"iid": uuid_mod.UUID(invoice_id)},
        )
        await session.commit()

    async with client_for(t.host) as c:
        read = (
            await c.get(f"/api/v1/invoicing/invoices/{invoice_id}", headers=headers)
        ).json()
        assert read["lines"][0]["subscription_id"] is None  # the pre-upgrade shape
        assert await _already_billed(
            c, headers, company_id, sub["id"], offer["period_end"]
        )

        resaved = await c.patch(
            f"/api/v1/invoicing/invoices/{invoice_id}",
            json={"lines": [_repost(line) for line in read["lines"]]},
            headers=headers,
        )
        assert resaved.status_code == 200, resaved.text
        assert await _already_billed(
            c, headers, company_id, sub["id"], offer["period_end"]
        )


async def test_a_credit_note_claims_nothing(client_for) -> None:
    """A credit note mirrors the money, never the provenance.

    It bills no hour and retires no period: the invoice it corrects still holds the month, and
    a second claim row on the same period would break the one-document-per-period rule the
    unique key exists to state.
    """
    t: Tenant = await make_tenant("inv-prov-credit")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _setup_org(c, headers)
        company_id = await _company(c, headers)
        sub = await _subscription(c, headers, company_id)
        offer = await _offer(c, headers, company_id, sub["id"])

        invoice_id = (
            await c.post(
                "/api/v1/invoicing/invoices",
                json={
                    "company_id": company_id,
                    "lines": [
                        {
                            "description": "Hosting & onderhoud",
                            "line_kind": "subscription",
                            "quantity": "1",
                            "unit_price": "249",
                            "subscription_id": sub["id"],
                            "period_start": offer["period_start"],
                            "period_end": offer["period_end"],
                        },
                    ],
                },
                headers=headers,
            )
        ).json()["id"]
        issued = await c.post(
            f"/api/v1/invoicing/invoices/{invoice_id}/issue", json={}, headers=headers
        )
        assert issued.status_code == 200, issued.text

        credit = await c.post(
            f"/api/v1/invoicing/invoices/{invoice_id}/credit", headers=headers
        )
        assert credit.status_code == 201, credit.text
        body = credit.json()
        assert body["kind"] == "credit_note"
        # The kind is mirrored (the document still reads as an agreement line); the claim is not.
        assert [line["line_kind"] for line in body["lines"]] == ["subscription"]
        assert all(line["subscription_id"] is None for line in body["lines"])
        assert all(line["period_end"] is None for line in body["lines"])
        assert all(line["time_entry_ids"] == [] for line in body["lines"])

        # The original still holds the month, and holds it exactly once.
        assert await _already_billed(
            c, headers, company_id, sub["id"], offer["period_end"]
        )

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        rows = (
            await session.execute(
                text(
                    "SELECT invoice_id FROM invoice_subscription_periods"
                    " WHERE subscription_id = :sid AND period_end = :pe"
                ),
                {
                    "sid": uuid_mod.UUID(sub["id"]),
                    "pe": datetime.strptime(offer["period_end"], "%Y-%m-%d").date(),
                },
            )
        ).scalars().all()
    assert [str(row) for row in rows] == [invoice_id]


async def test_one_line_claims_one_agreement(client_for) -> None:
    """A line that named both an agreement and a domain would retire two periods on one
    description, and no reader of the document could tell which — refused at the schema, so
    no half-claim ever reaches the tables."""
    t: Tenant = await make_tenant("inv-prov-onclaim")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _setup_org(c, headers)
        company_id = await _company(c, headers)

        clash = await c.post(
            "/api/v1/invoicing/invoices",
            json={
                "company_id": company_id,
                "lines": [
                    {
                        "description": "Van alles wat",
                        "line_kind": "subscription",
                        "quantity": "1",
                        "unit_price": "249",
                        "subscription_id": str(uuid_mod.uuid4()),
                        "domain_id": str(uuid_mod.uuid4()),
                        "period_start": _today().isoformat(),
                        "period_end": _today().isoformat(),
                    },
                ],
            },
            headers=headers,
        )
        assert clash.status_code == 422, clash.text
        error = clash.json()["error"]
        assert error["code"] == "validation"
        assert "errors.invoicing.one_claim_per_line" in error["fields"].values()


async def test_a_full_credit_hands_the_work_back(client_for) -> None:
    """Crediting an invoice makes what it billed billable again.

    A credit note claims nothing (see above) — but that is about the *credit note*. The
    invoice it corrects went on holding everything it billed: the hours stayed stamped
    ``invoiced_at`` and the agreement's month stayed retired. So the one thing you credit an
    invoice in order to do — bill the work again, correctly — was the one thing you could not
    do. `cancel` had released both since #207 for exactly this reason ("otherwise cancelling
    would silently retire an agreement's month for good"); crediting is the same act on a
    document too far along to cancel.

    Only a **full** credit releases: a partial one corrects an amount, and nothing says which
    hours or which month the corrected part belonged to.
    """
    t: Tenant = await make_tenant("inv-prov-credit-release")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _setup_org(c, headers)
        company_id = await _company(c, headers)
        await c.put(
            f"/api/v1/leave/rate/{t.user.id}", json={"hourly_rate": "90.00"}, headers=headers
        )
        _, entry_ids = await _billable_entries(c, headers, company_id, (90, 30))
        sub = await _subscription(c, headers, company_id)
        offer = await _offer(c, headers, company_id, sub["id"])

        invoice_id = (
            await c.post(
                "/api/v1/invoicing/invoices",
                json={
                    "company_id": company_id,
                    "lines": [
                        {"description": "Uren", "line_kind": "hours", "quantity": "2",
                         "unit": "uur", "unit_price": "90", "time_entry_ids": entry_ids},
                        {"description": "Hosting", "line_kind": "subscription",
                         "quantity": "1", "unit_price": "249",
                         "subscription_id": sub["id"],
                         "period_start": offer["period_start"],
                         "period_end": offer["period_end"]},
                    ],
                },
                headers=headers,
            )
        ).json()["id"]
        issued = await c.post(
            f"/api/v1/invoicing/invoices/{invoice_id}/issue", json={}, headers=headers
        )
        assert issued.status_code == 200, issued.text
        assert await _unbilled_ids(c, headers, company_id) == set()
        assert await _already_billed(
            c, headers, company_id, sub["id"], offer["period_end"]
        )

        credit = (
            await c.post(
                f"/api/v1/invoicing/invoices/{invoice_id}/credit", headers=headers
            )
        ).json()
        # A draft credit note releases nothing: it is not a document yet.
        assert await _unbilled_ids(c, headers, company_id) == set()

        await c.post(
            f"/api/v1/invoicing/invoices/{credit['id']}/issue", json={}, headers=headers
        )
        # Issued and covering the whole invoice: the work is on offer again.
        assert await _unbilled_ids(c, headers, company_id) == set(entry_ids)
        assert not await _already_billed(
            c, headers, company_id, sub["id"], offer["period_end"]
        )


async def test_a_partial_credit_holds_on_to_the_work(client_for) -> None:
    """Half the invoice still stands, and nothing says which half the hours were."""
    t: Tenant = await make_tenant("inv-prov-credit-partial")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _setup_org(c, headers)
        company_id = await _company(c, headers)
        await c.put(
            f"/api/v1/leave/rate/{t.user.id}", json={"hourly_rate": "90.00"}, headers=headers
        )
        _, entry_ids = await _billable_entries(c, headers, company_id, (90, 30))
        invoice_id = (
            await c.post(
                "/api/v1/invoicing/invoices",
                json={
                    "company_id": company_id,
                    "lines": [
                        {"description": "Uren", "line_kind": "hours", "quantity": "2",
                         "unit": "uur", "unit_price": "90", "time_entry_ids": entry_ids},
                    ],
                },
                headers=headers,
            )
        ).json()["id"]
        await c.post(
            f"/api/v1/invoicing/invoices/{invoice_id}/issue", json={}, headers=headers
        )
        credit = (
            await c.post(
                f"/api/v1/invoicing/invoices/{invoice_id}/credit", headers=headers
            )
        ).json()
        await c.patch(
            f"/api/v1/invoicing/invoices/{credit['id']}",
            json={"lines": [{"description": "Correctie", "quantity": "1",
                             "unit_price": "-50"}]},
            headers=headers,
        )
        await c.post(
            f"/api/v1/invoicing/invoices/{credit['id']}/issue", json={}, headers=headers
        )
        assert await _unbilled_ids(c, headers, company_id) == set(), (
            "a partial credit names no hours, so it releases none"
        )


async def test_a_paid_invoice_credited_also_hands_its_work_back(client_for) -> None:
    """The case `credited_total` alone cannot see.

    A paid invoice has no room, so the credit note absorbs nothing and `credited_total` stays
    zero — but the client has been credited in full and the work is exactly as re-billable as
    on an unpaid one. Release keys off the *documents*, not off what they absorbed.
    """
    t: Tenant = await make_tenant("inv-prov-credit-paid")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _setup_org(c, headers)
        company_id = await _company(c, headers)
        await c.put(
            f"/api/v1/leave/rate/{t.user.id}", json={"hourly_rate": "90.00"}, headers=headers
        )
        _, entry_ids = await _billable_entries(c, headers, company_id, (60,))
        invoice = (
            await c.post(
                "/api/v1/invoicing/invoices",
                json={
                    "company_id": company_id,
                    "lines": [
                        {"description": "Uren", "line_kind": "hours", "quantity": "1",
                         "unit": "uur", "unit_price": "90", "time_entry_ids": entry_ids},
                    ],
                },
                headers=headers,
            )
        ).json()
        issued = (
            await c.post(
                f"/api/v1/invoicing/invoices/{invoice['id']}/issue", json={}, headers=headers
            )
        ).json()
        await c.post(
            f"/api/v1/invoicing/invoices/{invoice['id']}/payments",
            json={"paid_on": _today().isoformat(), "amount": issued["total"]},
            headers=headers,
        )
        assert await _unbilled_ids(c, headers, company_id) == set()

        credit = (
            await c.post(
                f"/api/v1/invoicing/invoices/{invoice['id']}/credit", headers=headers
            )
        ).json()
        settled = (
            await c.post(
                f"/api/v1/invoicing/invoices/{credit['id']}/issue", json={}, headers=headers
            )
        ).json()
        assert settled["applied_total"] == "0.00"  # nothing absorbed…
        assert await _unbilled_ids(c, headers, company_id) == set(entry_ids)  # …released anyway

        # And that note can no longer be withdrawn: the work is back on offer, possibly
        # already re-invoiced, and re-claiming it from here cannot be done safely.
        withdrawn = await c.post(
            f"/api/v1/invoicing/invoices/{settled['id']}/cancel", headers=headers
        )
        assert withdrawn.status_code == 409
        assert (
            withdrawn.json()["error"]["message"]
            == "errors.invoicing.credit_released_work"
        )
