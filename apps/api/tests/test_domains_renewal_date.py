"""When a domain's renewal is invoiced — the register's expiry, and the date a person sets.

``next_invoice_date`` used to be derived and nothing else: the first yearly anniversary of
``start_date`` still ahead. That is the real expiry exactly when ``start_date`` is the real
registration date, and wrong by however much it misses when it is not — which is the normal case
for a portfolio onboarded in one afternoon, where every domain ends up anchored to that afternoon
and every renewal invoice then goes out on the wrong day, every year.

Two changes, and this file is the guard on both:

* **A connected register that has answered supplies the default** (``app/core/registrar/
  expiry.py``), through the seam ``domains`` uses for the invoiceable question — so the module
  still names no registrar. An expiry already in the past is *not* taken: a lapsed registration
  is a thing to look at, not a date to bill on, and using it would hand the cron a date it fires
  on immediately.
* **The date is editable** — on the form, in a spreadsheet and over a selection. Which is what
  makes an already-onboarded register fixable at all, and is why an explicit ``null`` means
  *work it out again* rather than *stop invoicing this*: "never bill this domain" is
  ``invoiceable``'s job and already has a field.
"""

from __future__ import annotations

import csv
import io
from datetime import date

import pytest

from app.integrations.cloudflare import client as cf_client
from app.integrations.oxxa import client as oxxa_client
from app.modules.domains.service import add_months
from tests.cloudflare_fake import FakeCloudflare
from tests.conftest import auth_cookie, make_tenant, org_today
from tests.oxxa_fake import FakeOxxa

CF_TOKEN = "cf-token-0123456789abcdef"


@pytest.fixture
def cloudflare() -> FakeCloudflare:
    fake = FakeCloudflare()
    cf_client.set_transport(fake.transport())
    yield fake
    cf_client.set_transport(None)


@pytest.fixture
def oxxa() -> FakeOxxa:
    fake = FakeOxxa()
    oxxa_client.set_transport(fake.transport())
    yield fake
    oxxa_client.set_transport(None)


async def _company(c, headers, name: str = "Klant BV") -> str:
    res = await c.post("/api/v1/companies", json={"name": name}, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _domain(c, headers, name: str, company: str, **extra) -> dict:
    res = await c.post(
        "/api/v1/domains", json={"name": name, "company_id": company, **extra}, headers=headers
    )
    assert res.status_code == 201, res.text
    return res.json()


async def _cf_synced(c, headers) -> None:
    """A Cloudflare account whose Registrar list has been read — an *authority* (#298)."""
    account = (
        await c.post(
            "/api/v1/cloudflare/accounts",
            json={"name": "Agency", "api_token": CF_TOKEN},
            headers=headers,
        )
    ).json()
    await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/verify", headers=headers)
    synced = await c.post(
        f"/api/v1/cloudflare/accounts/{account['id']}/sync", headers=headers
    )
    assert synced.status_code == 200, synced.text


def _anniversary(start: date, today: date) -> date:
    nxt = add_months(start, 12)
    while nxt <= today:
        nxt = add_months(nxt, 12)
    return nxt


# --------------------------------------------------------------------------------------- #
# The default
# --------------------------------------------------------------------------------------- #
async def test_with_no_register_connected_the_renewal_is_still_the_anniversary(
    client_for,
) -> None:
    """The upgrade guarantee, and the state most instances are in: nothing to ask, so nothing
    changes. Same date #250 always produced."""
    t = await make_tenant("ren-none")
    headers = await auth_cookie(t.user)
    today = org_today()
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        domain = await _domain(
            c, headers, "geenregister.nl", company, start_date="2020-03-15"
        )

        assert domain["next_invoice_date"] == _anniversary(date(2020, 3, 15), today).isoformat()
        assert domain["register_expires_on"] is None


async def test_a_new_domain_takes_the_registrars_expiry(client_for, cloudflare) -> None:
    """The feature. The registration lapses on 1 March 2027 whatever ``start_date`` says, so
    that is when the renewal is invoiced — and the anniversary would have been a different day
    entirely."""
    cloudflare.add_registration("vanons.nl", expires_at="2027-03-01T23:59:59Z")
    t = await make_tenant("ren-cf")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        await _cf_synced(c, headers)

        domain = await _domain(c, headers, "vanons.nl", company, start_date="2020-08-20")

        assert domain["next_invoice_date"] == "2027-03-01"
        assert domain["register_expires_on"] == "2027-03-01"
        # Not the derived answer, which is what makes this worth having at all.
        assert domain["next_invoice_date"] != _anniversary(
            date(2020, 8, 20), org_today()
        ).isoformat()


async def test_a_register_that_holds_nothing_of_ours_supplies_no_date(
    client_for, cloudflare
) -> None:
    """A synced register is an authority about the domains it *holds*. One it has never heard
    of falls back to the anniversary, exactly as if no register were connected."""
    cloudflare.add_registration("iemandanders.nl")
    t = await make_tenant("ren-cf-miss")
    headers = await auth_cookie(t.user)
    today = org_today()
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        await _cf_synced(c, headers)

        domain = await _domain(c, headers, "vanons.nl", company, start_date="2021-05-04")

        assert domain["register_expires_on"] is None
        assert domain["next_invoice_date"] == _anniversary(date(2021, 5, 4), today).isoformat()


async def test_an_expiry_in_the_past_is_reported_but_never_billed_on(
    client_for, cloudflare
) -> None:
    """A lapsed registration is a thing to look at, not a date to invoice on.

    Taking it would hand the renewal cron a due date it fires on immediately and draft an
    invoice for a registration that has run out. So it is *reported* — the row still says what
    the registrar observed — and the billing date falls back to the anniversary.
    """
    cloudflare.add_registration("verlopen.nl", expires_at="2024-02-09T00:00:00Z")
    t = await make_tenant("ren-cf-past")
    headers = await auth_cookie(t.user)
    today = org_today()
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        await _cf_synced(c, headers)

        domain = await _domain(c, headers, "verlopen.nl", company, start_date="2019-11-30")

        assert domain["register_expires_on"] == "2024-02-09"
        assert domain["next_invoice_date"] == _anniversary(
            date(2019, 11, 30), today
        ).isoformat()


async def test_a_domain_in_a_dead_status_still_gets_no_cycle(client_for, cloudflare) -> None:
    """Whether a register knows the expiry has no bearing on whether the domain bills at all:
    an ``expired`` domain has no renewal cycle, register or no register."""
    cloudflare.add_registration("uit.nl", expires_at="2027-03-01T23:59:59Z")
    t = await make_tenant("ren-cf-dead")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        await _cf_synced(c, headers)

        domain = await _domain(c, headers, "uit.nl", company, status="inactive")

        assert domain["next_invoice_date"] is None
        # Still observed, still reported — the register did not stop knowing.
        assert domain["register_expires_on"] == "2027-03-01"


# --------------------------------------------------------------------------------------- #
# What a person sets
# --------------------------------------------------------------------------------------- #
async def test_a_sent_renewal_date_wins_over_the_register(client_for, cloudflare) -> None:
    """An agency that knows the real date should never have to correct it afterwards."""
    cloudflare.add_registration("afwijkend.nl", expires_at="2027-03-01T23:59:59Z")
    t = await make_tenant("ren-explicit")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        await _cf_synced(c, headers)

        domain = await _domain(
            c, headers, "afwijkend.nl", company, next_invoice_date="2026-12-31"
        )

        assert domain["next_invoice_date"] == "2026-12-31"
        # Decided and observed are separate columns, and this is the whole reason (§10): the
        # screen can say the registrar disagrees, which a mirror that overwrote could not.
        assert domain["register_expires_on"] == "2027-03-01"


async def test_editing_the_renewal_date_sticks_and_is_on_the_trail(client_for) -> None:
    """It is now something a person does, so §16 applies: the change is attributable."""
    t = await make_tenant("ren-edit")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        domain = await _domain(c, headers, "handmatig.nl", company, start_date="2022-01-10")

        patched = await c.patch(
            f"/api/v1/domains/{domain['id']}",
            json={"next_invoice_date": "2027-06-30"},
            headers=headers,
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["next_invoice_date"] == "2027-06-30"

        trail = await c.get(
            "/api/v1/activity",
            params={"entity_type": "domain", "entity_id": domain["id"]},
            headers=headers,
        )
        assert trail.status_code == 200, trail.text
        changed = [
            entry
            for entry in trail.json()
            if "next_invoice_date" in (entry.get("payload") or {}).get("changes", {})
        ]
        assert changed, trail.json()
        assert changed[0]["payload"]["changes"]["next_invoice_date"]["to"] == "2027-06-30"


async def test_clearing_the_renewal_date_works_it_out_again(client_for, cloudflare) -> None:
    """Explicit ``null`` resets rather than stops.

    "Never bill this domain" is ``invoiceable``'s job and already has a field; a blank billing
    date that quietly meant the same would be one decision spelled two ways. Emptying a date
    whose whole job is to be derived means *forget my number* — so the register answers again.
    """
    cloudflare.add_registration("terug.nl", expires_at="2027-03-01T23:59:59Z")
    t = await make_tenant("ren-reset")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        await _cf_synced(c, headers)
        domain = await _domain(c, headers, "terug.nl", company, next_invoice_date="2026-12-31")
        assert domain["next_invoice_date"] == "2026-12-31"

        reset = await c.patch(
            f"/api/v1/domains/{domain['id']}",
            json={"next_invoice_date": None},
            headers=headers,
        )
        assert reset.status_code == 200, reset.text
        assert reset.json()["next_invoice_date"] == "2027-03-01"


async def test_an_absent_renewal_date_leaves_an_edited_one_alone(client_for) -> None:
    """The ``exclude_unset`` split, which is what keeps "cleared" from meaning "not sent": a
    patch about something else must not reschedule a renewal."""
    t = await make_tenant("ren-absent")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        domain = await _domain(c, headers, "rust.nl", company, next_invoice_date="2027-04-01")

        patched = await c.patch(
            f"/api/v1/domains/{domain['id']}", json={"status": "parked"}, headers=headers
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["next_invoice_date"] == "2027-04-01"


# --------------------------------------------------------------------------------------- #
# In bulk, and in a spreadsheet
# --------------------------------------------------------------------------------------- #
async def test_a_selection_can_be_put_on_one_renewal_date(client_for) -> None:
    """The answer to "I onboarded forty domains on one Tuesday and they all renew on it"."""
    t = await make_tenant("ren-bulk")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        one = await _domain(c, headers, "een.nl", company)
        two = await _domain(c, headers, "twee.nl", company)

        result = await c.post(
            "/api/v1/bulk/domain/update",
            json={
                "ids": [one["id"], two["id"]],
                "values": {"next_invoice_date": "2027-09-15"},
            },
            headers=headers,
        )
        assert result.status_code == 200, result.text
        assert result.json() == {"succeeded": 2, "failed": []}

        for domain in (one, two):
            row = (await c.get(f"/api/v1/domains/{domain['id']}", headers=headers)).json()
            assert row["next_invoice_date"] == "2027-09-15"


async def test_a_selection_can_be_put_back_on_the_registers_date(
    client_for, cloudflare
) -> None:
    """The clearable half, which the import deliberately does not have: over rows somebody
    ticked one by one, "work these out again" is exactly the repair the control is for."""
    cloudflare.add_registration("terugbulk.nl", expires_at="2027-03-01T23:59:59Z")
    t = await make_tenant("ren-bulk-clear")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        await _cf_synced(c, headers)
        domain = await _domain(
            c, headers, "terugbulk.nl", company, next_invoice_date="2026-12-31"
        )

        result = await c.post(
            "/api/v1/bulk/domain/update",
            json={"ids": [domain["id"]], "values": {"next_invoice_date": None}},
            headers=headers,
        )
        assert result.status_code == 200, result.text

        row = (await c.get(f"/api/v1/domains/{domain['id']}", headers=headers)).json()
        assert row["next_invoice_date"] == "2027-03-01"


async def test_an_export_round_trips_the_renewal_date_and_carries_the_registrars(
    client_for, cloudflare
) -> None:
    """Both columns, and only one of them writable: importing what the registrar observed would
    be schakl telling itself, while the date it bills on is a real editable field."""
    cloudflare.add_registration("heenweer.nl", expires_at="2027-03-01T23:59:59Z")
    t = await make_tenant("ren-impex")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        await _cf_synced(c, headers)
        await _domain(c, headers, "heenweer.nl", company, next_invoice_date="2026-12-31")

        content = (await c.get("/api/v1/impex/domain/export", headers=headers)).content
        parsed = list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))
        header = parsed[0]
        row = dict(zip(header, parsed[1], strict=True))

        assert row["next_invoice_date"] == "2026-12-31"
        assert row["register_expires_on"] == "2027-03-01"

        report = await c.post(
            "/api/v1/impex/domain/import",
            params={"dry_run": "false"},
            files={"file": ("domains.csv", content, "text/csv")},
            headers=headers,
        )
        assert report.status_code == 200, report.text
        assert report.json()["error_count"] == 0

        after = (await c.get("/api/v1/domains", headers=headers)).json()["items"][0]
        # Unchanged: a round trip is the one thing an export must not move (§17).
        assert after["next_invoice_date"] == "2026-12-31"


async def test_an_imported_blank_never_reschedules_a_renewal(client_for) -> None:
    """The one place this column is *not* clearable, and the reason is the blank itself: a
    register somebody exported, edited two cells of and imported back has an empty column for
    every row they never looked at."""
    t = await make_tenant("ren-impex-blank")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers, "Klant BV")
        await _domain(c, headers, "leeg.nl", company, next_invoice_date="2027-04-01")

        csv_bytes = b"name,company,next_invoice_date\nleeg.nl,Klant BV,\n"
        report = await c.post(
            "/api/v1/impex/domain/import",
            params={"dry_run": "false"},
            files={"file": ("domains.csv", csv_bytes, "text/csv")},
            headers=headers,
        )
        assert report.status_code == 200, report.text
        assert report.json()["error_count"] == 0

        after = (await c.get("/api/v1/domains", headers=headers)).json()["items"][0]
        assert after["next_invoice_date"] == "2027-04-01"


# --------------------------------------------------------------------------------------- #
# Isolation
# --------------------------------------------------------------------------------------- #
async def test_another_tenants_register_never_dates_our_renewal(client_for, cloudflare) -> None:
    """Golden Rule 1, on the seam most exposed to it: both register clauses match **by name**
    as well as by link, precisely so a domain typed since the last sync is not dropped — which
    is also exactly the shape that would cross tenants if the ``org_id`` filter slipped."""
    cloudflare.add_registration("gedeeld.nl", expires_at="2027-03-01T23:59:59Z")
    a = await make_tenant("ren-iso-a")
    b = await make_tenant("ren-iso-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)
    today = org_today()

    async with client_for(a.host) as c:
        company = await _company(c, a_headers)
        await _cf_synced(c, a_headers)
        mine = await _domain(c, a_headers, "gedeeld.nl", company, start_date="2020-02-02")
        assert mine["next_invoice_date"] == "2027-03-01"

    async with client_for(b.host) as c:
        company = await _company(c, b_headers)
        theirs = await _domain(c, b_headers, "gedeeld.nl", company, start_date="2020-02-02")
        assert theirs["register_expires_on"] is None
        assert theirs["next_invoice_date"] == _anniversary(date(2020, 2, 2), today).isoformat()
