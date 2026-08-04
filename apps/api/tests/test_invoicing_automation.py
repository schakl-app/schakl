"""How far a recurring-billing cron takes an invoice on its own: :class:`AutoInvoiceMode`.

The level is the one thing about automatic invoicing an agency actually disagrees about, and
every step of it is a step a mistake takes towards the client — ``off`` shows nobody
anything, ``send`` puts a wrong number in a client's inbox. So each rung is pinned here
end to end: the default (unchanged from every release before the level existed), the manual
path ``off`` leaves intact, the per-agreement override that beats the org default in *both*
directions, the number allocation and frozen snapshot ``issue`` performs, the flag ``send``
raises instead of mailing inline, and the degrade-to-draft that keeps a month's billing when
the automation cannot finish.

The consumers are driven the way the crons drive them (``subscriptions/jobs.py``,
``domains/jobs.py``): the handler called directly on a ``SystemContext``, in its own
transaction, with the agreement's own override on the payload.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.billing import add_months
from app.core.events import SystemContext
from app.core.models import Org
from app.db import async_session_maker, set_current_org
from app.modules.invoicing.events import on_domain_due, on_subscription_due
from app.modules.invoicing.models import Invoice
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
            }
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    # Seeds the NL rates (21% default among them) — the lazy-seed read.
    assert (await client.get("/api/v1/invoicing/tax-rates", headers=headers)).status_code == 200


async def _company(client, headers) -> str:
    resp = await client.post(
        "/api/v1/companies",
        json={"name": "Klant BV", "invoice_email": "boekhouding@klant.nl"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _set_mode(client, headers, mode: str) -> None:
    resp = await client.put(
        "/api/v1/invoicing/settings", json={"auto_invoice_mode": mode}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["auto_invoice_mode"] == mode


async def _fire(org_id, handler, payload: dict) -> None:
    """Run a ``*.due`` consumer the way its cron does.

    ``run_per_org`` hands the handler a ``SystemContext`` with the org's RLS GUC bound and one
    transaction for the whole org, so the fixture is the transaction rather than a request:
    there is no user, no permission check and no HTTP envelope in this path.
    """
    async with async_session_maker() as session:
        org = await session.get(Org, org_id)
        await set_current_org(session, org.id)
        await handler(SystemContext(org=org, session=session), dict(payload))
        await session.commit()


async def _invoices(client, headers) -> list[dict]:
    page = (await client.get("/api/v1/invoicing/invoices", headers=headers)).json()
    return page["items"]


async def _actions(client, headers, invoice_id: str) -> list[str]:
    feed = (
        await client.get(
            "/api/v1/activity",
            params={"entity_type": "invoice", "entity_id": invoice_id},
            headers=headers,
        )
    ).json()
    return [item["action"] for item in feed]


async def _row(org_id, invoice_id: str) -> Invoice:
    """The stored invoice — ``auto_send_pending`` is deliberately not on the read schema
    (it is the send pass's private handshake), so the assertion reads the row."""
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        return (
            await session.execute(
                select(Invoice).where(Invoice.id == uuid.UUID(str(invoice_id)))
            )
        ).scalar_one()


def _renewal_period(domain: dict) -> dict:
    """The year ``domains/jobs.py`` would bill: back one interval from ``next_invoice_date``.

    Derived from the domain's own cycle rather than written by hand, because the picker walks
    the same grid — a period the cron will never reach is one no test should assert about.
    """
    boundary = date.fromisoformat(domain["next_invoice_date"])
    return {
        "domain_id": domain["id"],
        "period_start": add_months(boundary, -12).isoformat(),
        "period_end": boundary.isoformat(),
    }


def _sub_payload(subscription_id, company_id: str, **extra) -> dict:
    """The shape ``subscriptions/jobs.py`` emits, including the agreement's own override."""
    return {
        "subscription_id": subscription_id,
        "company_id": company_id,
        "name": "Hosting Plus",
        "amount": "250.00",
        "currency": "EUR",
        "period_start": (_today() - timedelta(days=31)).isoformat(),
        "period_end": _today().isoformat(),
        "lines": [{"description": "Hosting Plus", "quantity": "1", "unit_amount": "250.00"}],
        **extra,
    }


async def test_default_mode_is_draft_and_is_what_the_instance_always_did(client_for) -> None:
    """An upgrade that adds a level must change nothing for an org that never touches it.

    ``draft`` is the default precisely so that ``ISSUE``/``SEND`` — the two steps a delete
    cannot undo — are opt-in: nobody wakes up to invoices their instance mailed overnight
    because a release added the capability."""
    t: Tenant = await make_tenant("auto-default")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _setup_org(c, headers)
        company_id = await _company(c, headers)
        settings = (await c.get("/api/v1/invoicing/settings", headers=headers)).json()
        assert settings["auto_invoice_mode"] == "draft"

    await _fire(t.org.id, on_subscription_due, _sub_payload(uuid.uuid4(), company_id))

    async with client_for(t.host) as c:
        items = await _invoices(c, headers)
        assert len(items) == 1
        assert items[0]["status"] == "draft"
        assert items[0]["number"] is None  # numbers belong to issued documents only


async def test_off_raises_nothing_and_loses_nothing(client_for) -> None:
    """``off`` is not "stop billing", it is "a human bills".

    The cycle still advances and nothing claims the period, so the invoice editor's picker
    still offers it — priced and dated exactly as the cron would have raised it. That is the
    whole manual path, and it is why turning automation off costs a click rather than a
    month of billing."""
    t: Tenant = await make_tenant("auto-off")
    headers = await auth_cookie(t.user)
    today = _today()
    async with client_for(t.host) as c:
        await _setup_org(c, headers)
        company_id = await _company(c, headers)
        await _set_mode(c, headers, "off")
        sub = (
            await c.post(
                "/api/v1/subscriptions",
                json={
                    "company_id": company_id,
                    "name": "Hosting & onderhoud",
                    "status": "active",
                    "interval": "monthly",
                    "start_date": (today - timedelta(days=40)).isoformat(),
                    "next_invoice_date": today.isoformat(),
                    "amount": "249.00",
                    "lines": [
                        {"description": "Hosting", "quantity": "1", "unit_amount": "249.00"}
                    ],
                },
                headers=headers,
            )
        ).json()
        offer = (
            await c.get(
                "/api/v1/invoicing/outstanding",
                params={"company_id": company_id},
                headers=headers,
            )
        ).json()["subscriptions"][0]["periods"][-1]
        assert offer["period_end"] == today.isoformat()

    await _fire(
        t.org.id,
        on_subscription_due,
        _sub_payload(
            sub["id"],
            company_id,
            period_start=offer["period_start"],
            period_end=offer["period_end"],
        ),
    )

    async with client_for(t.host) as c:
        assert await _invoices(c, headers) == []
        # The period is unclaimed, so the picker offers it — the manual path, intact.
        still = (
            await c.get(
                "/api/v1/invoicing/outstanding",
                params={"company_id": company_id},
                headers=headers,
            )
        ).json()["subscriptions"][0]["periods"][-1]
        assert still["period_end"] == today.isoformat()
        assert still["amount"] == "249.00"
        assert still["already_billed"] is False


async def test_agreement_override_beats_the_org_default_both_ways(client_for) -> None:
    """``NULL`` on the agreement means *inherit*; a value means *this one is different*.

    Both directions matter and only testing one hides half the bug: an agency automating in
    general still has the one client whose invoices are always checked by hand, and an agency
    doing everything by hand still has the retainer nobody looks at."""
    t: Tenant = await make_tenant("auto-override")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _setup_org(c, headers)
        company_id = await _company(c, headers)
        await _set_mode(c, headers, "off")

    # Org says off, this agreement says draft — the agreement wins.
    await _fire(
        t.org.id,
        on_subscription_due,
        _sub_payload(uuid.uuid4(), company_id, auto_invoice_mode="draft"),
    )
    async with client_for(t.host) as c:
        items = await _invoices(c, headers)
        assert len(items) == 1
        assert items[0]["status"] == "draft"
        await _set_mode(c, headers, "draft")

    # …and the other way round: org says draft, this agreement says off.
    await _fire(
        t.org.id,
        on_subscription_due,
        _sub_payload(uuid.uuid4(), company_id, auto_invoice_mode="off"),
    )
    async with client_for(t.host) as c:
        assert len(await _invoices(c, headers)) == 1  # still just the first one


async def test_issue_allocates_a_number_and_freezes_the_snapshot(client_for) -> None:
    """``issue`` makes the document real: it takes the org's next number, freezes its bill-to
    and starts counting towards its due date — inline, in the drafting transaction, because
    that is a number allocation and a status flip and the two commit together.

    Nobody outside the agency has seen it yet, which is exactly what separates this rung from
    ``send``: ``auto_send_pending`` stays down."""
    t: Tenant = await make_tenant("auto-issue")
    headers = await auth_cookie(t.user)
    today = _today()
    async with client_for(t.host) as c:
        await _setup_org(c, headers)
        company_id = await _company(c, headers)
        await _set_mode(c, headers, "issue")

    await _fire(t.org.id, on_subscription_due, _sub_payload(uuid.uuid4(), company_id))

    async with client_for(t.host) as c:
        items = await _invoices(c, headers)
        assert len(items) == 1
        invoice = items[0]
        assert invoice["status"] == "open"
        # The org's own numbering format ({year}-{seq:4}), first in the sequence.
        assert invoice["number"] == f"{today.year}-0001"
        assert invoice["issue_date"] == today.isoformat()
        assert invoice["due_date"] == (today + timedelta(days=14)).isoformat()
        assert invoice["customer"]["name"] == "Klant BV"  # frozen at the moment it became real
        # The trail says the system issued it, not just that a draft appeared.
        assert "issued" in await _actions(c, headers, invoice["id"])

    assert (await _row(t.org.id, invoice["id"])).auto_send_pending is False


async def test_send_flags_for_the_send_pass_and_does_not_mail_inline(client_for) -> None:
    """The transactional-safety property, and the reason ``send`` is two steps.

    ``run_per_org`` gives a whole org one transaction, so mailing here would let a later
    agreement's failure roll back an invoice whose e-mail had already reached the client — an
    unrecallable side effect on a row that no longer exists. So the drafting transaction only
    writes a flag, and ``jobs.py`` mails what committed."""
    t: Tenant = await make_tenant("auto-send")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _setup_org(c, headers)
        company_id = await _company(c, headers)
        await _set_mode(c, headers, "send")

    await _fire(t.org.id, on_subscription_due, _sub_payload(uuid.uuid4(), company_id))

    async with client_for(t.host) as c:
        items = await _invoices(c, headers)
        assert len(items) == 1
        invoice = items[0]
        assert invoice["status"] == "open"
        assert invoice["number"] is not None  # send contains issue
        assert invoice["sent_at"] is None  # nothing left this transaction

    assert (await _row(t.org.id, invoice["id"])).auto_send_pending is True


async def test_auto_issue_degrades_to_a_draft_instead_of_exploding(client_for) -> None:
    """The billing is worth more than the automation.

    An org that cannot issue — no seller name, so no document may legally be finalised —
    keeps the draft it just earned and is told once on the trail. Raising here would take the
    rest of the org's month down with it, because ``run_per_org`` is one transaction per org
    and the cron has no answer to the exception."""
    t: Tenant = await make_tenant("auto-degrade")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _setup_org(c, headers)
        company_id = await _company(c, headers)
        # The seller block is replaced wholesale, so a block without a name clears it.
        cleared = await c.put(
            "/api/v1/invoicing/settings",
            json={
                "company_details": {"city": "Amsterdam", "country": "NL"},
                "auto_invoice_mode": "issue",
            },
            headers=headers,
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["company_details"]["name"] is None

    await _fire(t.org.id, on_subscription_due, _sub_payload(uuid.uuid4(), company_id))

    async with client_for(t.host) as c:
        items = await _invoices(c, headers)
        assert len(items) == 1  # the month's billing survived
        invoice = items[0]
        assert invoice["status"] == "draft"
        assert invoice["number"] is None  # no number was burned on a document that failed
        actions = await _actions(c, headers, invoice["id"])
        assert "auto_issue_failed" in actions
        assert "issued" not in actions

    assert (await _row(t.org.id, invoice["id"])).auto_send_pending is False


async def test_domain_cron_honours_the_mode_too(client_for) -> None:
    """One level, both crons. A renewal is a recurring charge like a retainer is, and an
    agency that switched automation off did not mean "except for domains"."""
    t: Tenant = await make_tenant("auto-dom-off")
    headers = await auth_cookie(t.user)
    today = _today()
    async with client_for(t.host) as c:
        await _setup_org(c, headers)
        company_id = await _company(c, headers)
        await _set_mode(c, headers, "off")
        domain = (
            await c.post(
                "/api/v1/domains",
                json={
                    "name": "klant.nl",
                    "company_id": company_id,
                    "start_date": (today - timedelta(days=400)).isoformat(),
                    "price_override": "12.50",
                },
                headers=headers,
            )
        ).json()

    await _fire(
        t.org.id,
        on_domain_due,
        {
            **_renewal_period(domain),
            "company_id": company_id,
            "name": "klant.nl",
            "tld": "nl",
            "auto_invoice_mode": None,
            "amount": "12.50",
            "currency": "EUR",
        },
    )

    async with client_for(t.host) as c:
        assert await _invoices(c, headers) == []


async def test_hand_picked_domain_renewal_stops_the_domain_cron(client_for) -> None:
    """Owner's rule, one entity over: *the cron should know it is already paid.*

    ``invoices.domain_id`` only ever answered for the invoice the cron itself raised — an
    agency's year-end invoice carries eleven renewals next to some hours and sets no such
    column, so the year was billed a second time. The claim in ``invoice_domain_periods`` is
    what a hand-built line writes, and it is the row ``on_domain_due`` now consults."""
    t: Tenant = await make_tenant("auto-dom-claim")
    headers = await auth_cookie(t.user)
    today = _today()
    async with client_for(t.host) as c:
        await _setup_org(c, headers)
        company_id = await _company(c, headers)
        domain = (
            await c.post(
                "/api/v1/domains",
                json={
                    "name": "handmatig.nl",
                    "company_id": company_id,
                    "start_date": (today - timedelta(days=400)).isoformat(),
                    "price_override": "12.50",
                },
                headers=headers,
            )
        ).json()
        period = _renewal_period(domain)
        # The mixed invoice a human builds: the renewal sits beside ordinary hours, and the
        # document carries no ``invoices.domain_id`` at all.
        built = await c.post(
            "/api/v1/invoicing/invoices",
            json={
                "company_id": company_id,
                "lines": [
                    {"description": "Verlenging handmatig.nl", "line_kind": "subscription",
                     "quantity": "1", "unit_price": "12.50", **period},
                    {"description": "Meerwerk", "line_kind": "hours",
                     "quantity": "2", "unit_price": "95"},
                ],
            },
            headers=headers,
        )
        assert built.status_code == 201, built.text
        assert built.json()["domain_id"] is None  # nothing on the invoice names the domain
        # The picker agrees the year is taken, rather than hiding the domain.
        offered = (
            await c.get(
                "/api/v1/invoicing/outstanding",
                params={"company_id": company_id},
                headers=headers,
            )
        ).json()["domains"][0]["periods"]
        billed = [p for p in offered if p["period_end"] == period["period_end"]]
        assert billed and billed[0]["already_billed"] is True

    await _fire(
        t.org.id,
        on_domain_due,
        {
            **period,
            "company_id": company_id,
            "name": "handmatig.nl",
            "tld": "nl",
            "auto_invoice_mode": None,
            "amount": "12.50",
            "currency": "EUR",
        },
    )

    async with client_for(t.host) as c:
        assert len(await _invoices(c, headers)) == 1  # no second document for the same year
