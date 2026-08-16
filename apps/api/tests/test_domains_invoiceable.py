"""Does this domain get invoiced? (#298) — the three-state flag and the register behind it.

The rule under test is the ``NULL`` leg, and it is the only one worth this much file: an agency's
domain list mixes names it renews for the client with names the client registered themselves and
merely asked us to point somewhere, and the registrar register is the only thing that knows which
is which. Two properties carry the feature and each has a test whose whole job is to keep it true:

* **A credential is not an authority.** A token stored this morning, or one scoped to DNS and
  nothing else, may not narrow what schakl invoices. Until a register has *answered*, every
  undecided domain bills exactly as it did before the column existed — which is also what makes
  this safe to ship into an instance that already invoices domains.
* **A zone is not a registration.** Cloudflare runs DNS for plenty of names a client pays for
  themselves, and its Registrar list reports domains held at other registrars too. Reading mere
  membership as "we hold this" would invoice a client for a domain we only serve DNS for.

Everything else here is the consequences: the cron skips but still advances, the picker labels
rather than omits, the export carries the *stored decision* rather than the resolved answer, and
resolving a page costs a fixed number of queries however many domains are on it.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db import async_session_maker, set_current_org
from app.integrations.cloudflare import client as cf_client
from app.integrations.cloudflare.models import CloudflareRegistrarDomain
from app.integrations.oxxa import client as oxxa_client
from app.modules.domains.models import Domain
from app.modules.domains.service import add_months
from tests.cloudflare_fake import FakeCloudflare
from tests.conftest import add_membership, auth_cookie, make_tenant
from tests.oxxa_fake import FakeOxxa

CF_TOKEN = "cf-token-0123456789abcdef"
OXXA_USER = "breik-reseller"
OXXA_PASSWORD = "Sup3rGeheim!wachtwoord"


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


# --------------------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------------------- #
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


async def _cf_synced(c, headers, fake: FakeCloudflare) -> dict:
    """A Cloudflare account whose Registrar list has been read — an *authority*."""
    res = await c.post(
        "/api/v1/cloudflare/accounts",
        json={"name": "Agency", "api_token": CF_TOKEN},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    account = res.json()
    await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/verify", headers=headers)
    synced = await c.post(
        f"/api/v1/cloudflare/accounts/{account['id']}/sync", headers=headers
    )
    assert synced.status_code == 200, synced.text
    return synced.json()


async def _by_name(c, headers, **params) -> dict[str, dict]:
    res = await c.get("/api/v1/domains", headers=headers, params=params)
    assert res.status_code == 200, res.text
    return {row["name"]: row for row in res.json()["items"]}


async def _force_next_invoice(org_id, domain_id: str, day) -> None:
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        domain = (
            await session.execute(select(Domain).where(Domain.id == uuid.UUID(domain_id)))
        ).scalar_one()
        domain.next_invoice_date = day
        await session.commit()


# --------------------------------------------------------------------------------------- #
# The default: no register has answered, so nothing is narrowed
# --------------------------------------------------------------------------------------- #
async def test_with_no_register_connected_every_undecided_domain_still_invoices(
    client_for,
) -> None:
    """The upgrade guarantee. An instance that invoiced domains yesterday invoices exactly the
    same ones today — the column is NULL everywhere and NULL is *follow the register*, of which
    there is none."""
    t = await make_tenant("inv-none")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        await _domain(c, headers, "onbeslist.nl", company)
        await _domain(c, headers, "nooit.nl", company, invoiceable=False)
        await _domain(c, headers, "altijd.nl", company, invoiceable=True)

        rows = await _by_name(c, headers)

        assert rows["onbeslist.nl"]["invoiceable"] is None
        assert rows["onbeslist.nl"]["invoiceable_effective"] is True
        assert rows["onbeslist.nl"]["invoiceable_source"] == "default"
        assert rows["onbeslist.nl"]["registers"] == []

        # An explicit decision never consults a register, so it reads the same either way.
        assert rows["nooit.nl"]["invoiceable_effective"] is False
        assert rows["nooit.nl"]["invoiceable_source"] == "explicit"
        assert rows["altijd.nl"]["invoiceable_effective"] is True
        assert rows["altijd.nl"]["invoiceable_source"] == "explicit"


async def test_a_stored_credential_that_has_never_synced_narrows_nothing(
    client_for, cloudflare
) -> None:
    """**A credential is not an authority.** The account exists and the token verifies; the
    register has not answered, so an undecided domain still bills. Getting this wrong would stop
    invoicing an agency's whole register the moment they pasted a token."""
    cloudflare.add_registration("vanons.nl")
    t = await make_tenant("inv-unsynced")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        await _domain(c, headers, "vanons.nl", company)
        await _domain(c, headers, "vanhen.nl", company)
        res = await c.post(
            "/api/v1/cloudflare/accounts",
            json={"name": "Agency", "api_token": CF_TOKEN},
            headers=headers,
        )
        await c.post(
            f"/api/v1/cloudflare/accounts/{res.json()['id']}/verify", headers=headers
        )

        rows = await _by_name(c, headers)
        for name in ("vanons.nl", "vanhen.nl"):
            assert rows[name]["invoiceable_effective"] is True, name
            assert rows[name]["invoiceable_source"] == "default", name


# --------------------------------------------------------------------------------------- #
# The register answers
# --------------------------------------------------------------------------------------- #
async def test_a_read_register_decides_and_a_foreign_registration_is_not_ours(
    client_for, cloudflare
) -> None:
    """**A zone is not a registration**, and the Registrar list is not a list of what we hold.

    ``elders.nl`` is the case the whole feature exists for: Cloudflare knows about it and answers
    its DNS, but the client registered it at their own registrar and pays for it themselves.
    """
    cloudflare.add_registration("vanons.nl", registrar="Cloudflare")
    cloudflare.add_registration("elders.nl", registrar="GoDaddy.com, LLC")
    cloudflare.add_zone("elders.nl")
    t = await make_tenant("inv-register")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        await _domain(c, headers, "vanons.nl", company)
        await _domain(c, headers, "elders.nl", company)
        await _domain(c, headers, "onbekend.nl", company)

        result = await _cf_synced(c, headers, cloudflare)
        assert result["registrar_read"] is True
        assert result["registrar_domains_synced"] == 2
        assert result["registrar_domains_at_cloudflare"] == 1
        assert result["registrar_domains_matched"] == 1

        rows = await _by_name(c, headers)
        # Held at Cloudflare Registrar → invoiced, and the screen can name who said so.
        assert rows["vanons.nl"]["invoiceable_effective"] is True
        assert rows["vanons.nl"]["invoiceable_source"] == "register"
        assert rows["vanons.nl"]["registers"] == ["cloudflare"]
        # In the list, but somebody else holds it. Never invoiced.
        assert rows["elders.nl"]["invoiceable_effective"] is False
        assert rows["elders.nl"]["invoiceable_source"] == "register"
        assert rows["elders.nl"]["registers"] == []
        # In no register at all — same answer, once some register has spoken.
        assert rows["onbekend.nl"]["invoiceable_effective"] is False
        assert rows["onbekend.nl"]["invoiceable_source"] == "register"


async def test_an_explicit_decision_beats_the_register_in_both_directions(
    client_for, cloudflare
) -> None:
    """The register is the *default*, never the authority over a person. An agency that agreed
    to bill a domain it does not hold, or to absorb one it does, must be able to say so."""
    cloudflare.add_registration("vanons.nl")
    t = await make_tenant("inv-explicit")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        held = await _domain(c, headers, "vanons.nl", company)
        foreign = await _domain(c, headers, "elders.nl", company)
        await _cf_synced(c, headers, cloudflare)

        await c.patch(
            f"/api/v1/domains/{held['id']}", json={"invoiceable": False}, headers=headers
        )
        await c.patch(
            f"/api/v1/domains/{foreign['id']}", json={"invoiceable": True}, headers=headers
        )

        rows = await _by_name(c, headers)
        assert rows["vanons.nl"]["invoiceable_effective"] is False
        assert rows["vanons.nl"]["invoiceable_source"] == "explicit"
        assert rows["elders.nl"]["invoiceable_effective"] is True
        assert rows["elders.nl"]["invoiceable_source"] == "explicit"

        # Explicit null hands the decision back to the register — the third state, not a "no".
        await c.patch(
            f"/api/v1/domains/{held['id']}", json={"invoiceable": None}, headers=headers
        )
        rows = await _by_name(c, headers)
        assert rows["vanons.nl"]["invoiceable"] is None
        assert rows["vanons.nl"]["invoiceable_effective"] is True
        assert rows["vanons.nl"]["invoiceable_source"] == "register"


async def test_the_oxxa_register_answers_for_a_domain_added_after_the_sync(
    client_for, oxxa
) -> None:
    """Matched by the sync's link **or** by name, because the two orders an agency works in
    disagree. "It stopped invoicing because I added it on a Tuesday" is not an answer."""
    oxxa.add_domain("vanons.nl")
    t = await make_tenant("inv-oxxa")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        res = await c.post(
            "/api/v1/oxxa/accounts",
            json={
                "name": "Breik reseller",
                "api_user": OXXA_USER,
                "api_password": OXXA_PASSWORD,
            },
            headers=headers,
        )
        account = res.json()
        await c.post(f"/api/v1/oxxa/accounts/{account['id']}/verify", headers=headers)
        synced = await c.post(f"/api/v1/oxxa/accounts/{account['id']}/sync", headers=headers)
        assert synced.status_code == 200, synced.text

        # Typed *after* the register was read, so nothing linked it: the name has to carry it.
        await _domain(c, headers, "vanons.nl", company)
        await _domain(c, headers, "vanhen.nl", company)

        rows = await _by_name(c, headers)
        assert rows["vanons.nl"]["invoiceable_effective"] is True
        assert rows["vanons.nl"]["registers"] == ["oxxa"]
        assert rows["vanhen.nl"]["invoiceable_effective"] is False


async def test_a_registration_transferred_away_stops_claiming_to_be_ours(
    client_for, cloudflare
) -> None:
    """The row survives the transfer — "it moved to GoDaddy last month" is something an agency
    reads rather than infers — but it stops being evidence that we renew the name."""
    cloudflare.add_registration("vertrokken.nl")
    t = await make_tenant("inv-transfer")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        await _domain(c, headers, "vertrokken.nl", company)
        await _cf_synced(c, headers, cloudflare)
        assert (await _by_name(c, headers))["vertrokken.nl"]["invoiceable_effective"] is True

        cloudflare.registrar["acct-1"] = []
        account = (await c.get("/api/v1/cloudflare/accounts", headers=headers)).json()[0]
        await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/sync", headers=headers)

        rows = await _by_name(c, headers)
        assert rows["vertrokken.nl"]["invoiceable_effective"] is False
        assert rows["vertrokken.nl"]["registers"] == []


async def test_a_row_whose_name_cannot_be_read_is_skipped_not_guessed(
    client_for, cloudflare
) -> None:
    """This endpoint has never been exercised against a live Registrar account, so every field is
    read defensively: a registration attributed to the wrong name is worse than one nobody
    counted (``docs/CLOUDFLARE.md`` §Registrar)."""
    cloudflare.registrar["acct-1"] = [
        {"id": "reg-x", "current_registrar": "Cloudflare"},  # no name field at all
        {"name": "vanons.nl", "current_registrar": "Cloudflare", "expires_at": "niet-een-datum"},
    ]
    t = await make_tenant("inv-defensive")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        await _domain(c, headers, "vanons.nl", company)
        result = await _cf_synced(c, headers, cloudflare)

        assert result["registrar_read"] is True
        assert result["registrar_domains_synced"] == 1  # the nameless row was skipped
        # A malformed expiry does not fail the sync that otherwise worked.
        assert (await _by_name(c, headers))["vanons.nl"]["invoiceable_effective"] is True


# --------------------------------------------------------------------------------------- #
# What the rest of the product does with the answer
# --------------------------------------------------------------------------------------- #
async def test_the_renewal_cron_skips_a_non_invoiced_domain_but_still_advances_it(
    client_for, cloudflare
) -> None:
    """Skipping and *freezing* are different things. A frozen date is a silent debt: switching
    the flag back on a year later would fire one missed year per cron run until it caught up."""
    from app.core import events
    from app.modules.domains.jobs import advance_domain_renewals

    cloudflare.add_registration("vanons.nl")
    today = datetime.now(UTC).date()
    t = await make_tenant("inv-cron")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        await c.post(
            "/api/v1/domains/tld-prices",
            json={
                "tld": "nl",
                "amount": "12.50",
                "valid_from": (today - timedelta(days=1)).isoformat(),
            },
            headers=headers,
        )
        held = await _domain(c, headers, "vanons.nl", company)
        foreign = await _domain(c, headers, "elders.nl", company)
        await _cf_synced(c, headers, cloudflare)

    for row in (held, foreign):
        await _force_next_invoice(t.org.id, row["id"], today)

    fired: list[dict] = []

    async def listener(ctx, payload) -> None:
        fired.append(payload)

    events.subscribe("domain.due", listener)
    try:
        await advance_domain_renewals({})
    finally:
        events._handlers["domain.due"].remove(listener)

    assert [p["name"] for p in fired] == ["vanons.nl"]

    async with client_for(t.host) as c:
        rows = await _by_name(c, headers)
        # Both cycles rolled forward; only one of them drafted anything.
        assert rows["vanons.nl"]["next_invoice_date"] == add_months(today, 12).isoformat()
        assert rows["elders.nl"]["next_invoice_date"] == add_months(today, 12).isoformat()


async def test_the_list_filters_on_the_resolved_answer_not_the_stored_flag(
    client_for, cloudflare
) -> None:
    """"Show me what I am not billing" has to include the domains a *register* decided about,
    which are precisely the ones nobody has typed anything into."""
    cloudflare.add_registration("vanons.nl")
    t = await make_tenant("inv-filter")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        await _domain(c, headers, "vanons.nl", company)
        await _domain(c, headers, "elders.nl", company)
        await _domain(c, headers, "afgesproken.nl", company, invoiceable=True)
        await _cf_synced(c, headers, cloudflare)

        billed = await _by_name(c, headers, invoiceable="true")
        assert set(billed) == {"vanons.nl", "afgesproken.nl"}

        not_billed = await c.get(
            "/api/v1/domains", headers=headers, params={"invoiceable": "false"}
        )
        body = not_billed.json()
        assert {row["name"] for row in body["items"]} == {"elders.nl"}
        # The count is the filter's count, or the header lies about the list beneath it (#285).
        assert body["total"] == 1


async def test_the_outstanding_picker_labels_a_non_invoiced_domain_rather_than_hiding_it(
    client_for, cloudflare
) -> None:
    """Automation skipping a renewal and a human being forbidden to bill one are different
    things. "Why is klant.nl not on the invoice" is exactly the question the picker answers, and
    answering by omission is how the duplicate happens."""
    cloudflare.add_registration("vanons.nl")
    today = datetime.now(UTC).date()
    t = await make_tenant("inv-picker")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        await c.post(
            "/api/v1/domains/tld-prices",
            json={
                "tld": "nl",
                "amount": "12.50",
                "valid_from": (today - timedelta(days=365 * 3)).isoformat(),
            },
            headers=headers,
        )
        start = (today - timedelta(days=400)).isoformat()
        await _domain(c, headers, "vanons.nl", company, start_date=start)
        await _domain(c, headers, "elders.nl", company, start_date=start)
        await _cf_synced(c, headers, cloudflare)

        res = await c.get(
            "/api/v1/invoicing/outstanding", headers=headers, params={"company_id": company}
        )
        assert res.status_code == 200, res.text
        domains = {row["name"]: row for row in res.json()["domains"]}

        # Both are offered, with their periods; one of them says it is not billed automatically.
        assert set(domains) == {"vanons.nl", "elders.nl"}
        assert domains["vanons.nl"]["invoiceable"] is True
        assert domains["elders.nl"]["invoiceable"] is False
        assert domains["elders.nl"]["periods"], "the periods are listed, not withheld"


async def test_the_export_carries_the_stored_decision_and_re_imports_unchanged(
    client_for, cloudflare
) -> None:
    """§17's round-trip rule. Exporting the *resolved* answer would pin every domain to whatever
    the register happened to say that day — the file would look right and destroy the feature."""
    cloudflare.add_registration("vanons.nl")
    t = await make_tenant("inv-impex")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        await _domain(c, headers, "vanons.nl", company)
        await _domain(c, headers, "nooit.nl", company, invoiceable=False)
        await _cf_synced(c, headers, cloudflare)

        content = (await c.get("/api/v1/impex/domain/export", headers=headers)).content
        parsed = list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))
        header = parsed[0]
        rows = {
            row[header.index("name")]: dict(zip(header, row, strict=True))
            for row in parsed[1:]
        }

        # Undecided exports empty — the third state — while the resolved answer rides along
        # in its own read-only column.
        assert rows["vanons.nl"]["invoiceable"] == ""
        assert rows["vanons.nl"]["invoiceable_effective"].lower() in {"true", "yes", "1"}
        assert rows["nooit.nl"]["invoiceable"].lower() in {"false", "no", "0"}

        report = await c.post(
            "/api/v1/impex/domain/import",
            params={"dry_run": "false"},
            files={"file": ("domains.csv", content, "text/csv")},
            headers=headers,
        )
        assert report.status_code == 200, report.text
        assert report.json()["error_count"] == 0

        after = await _by_name(c, headers)
        assert after["vanons.nl"]["invoiceable"] is None
        assert after["vanons.nl"]["invoiceable_effective"] is True
        assert after["nooit.nl"]["invoiceable"] is False


# --------------------------------------------------------------------------------------- #
# Isolation and cost
# --------------------------------------------------------------------------------------- #
async def test_a_register_never_answers_for_another_tenant(client_for, cloudflare) -> None:
    """Golden Rule 1, on the one table that decides money. Org A holding ``gedeeld.nl`` at
    Cloudflare must not make org B's identically-named domain invoiceable."""
    cloudflare.add_registration("gedeeld.nl", account="acct-1")
    a = await make_tenant("inv-iso-a")
    b = await make_tenant("inv-iso-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)

    async with client_for(a.host) as c:
        company = await _company(c, a_headers)
        await _domain(c, a_headers, "gedeeld.nl", company)
        await _cf_synced(c, a_headers, cloudflare)
        assert (await _by_name(c, a_headers))["gedeeld.nl"]["invoiceable_effective"] is True

    async with client_for(b.host) as c:
        company = await _company(c, b_headers)
        await _domain(c, b_headers, "gedeeld.nl", company)
        rows = await _by_name(c, b_headers)
        # B has connected nothing, so B's answer is B's default — not A's register.
        assert rows["gedeeld.nl"]["invoiceable_source"] == "default"
        assert rows["gedeeld.nl"]["registers"] == []

    async with async_session_maker() as session:
        await set_current_org(session, b.org.id)
        leaked = (
            await session.execute(select(CloudflareRegistrarDomain))
        ).scalars().all()
        assert leaked == []


async def test_resolving_a_page_of_domains_costs_the_same_at_three_as_at_thirty(
    client_for, cloudflare, count_queries
) -> None:
    """The shape this pins is invisible in the JSON: one query per domain and one query for all
    of them return identical bodies (docs/PERFORMANCE.md). The uncorrelated authority clause is
    evaluated once per statement, and ``holds`` rides the same select."""
    t = await make_tenant("inv-budget")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        for i in range(3):
            cloudflare.add_registration(f"klein{i}.nl")
            await _domain(c, headers, f"klein{i}.nl", company)
        await _cf_synced(c, headers, cloudflare)

        with count_queries() as small:
            assert (await c.get("/api/v1/domains", headers=headers)).status_code == 200

        for i in range(3, 30):
            cloudflare.add_registration(f"klein{i}.nl")
            await _domain(c, headers, f"klein{i}.nl", company)
        account = (await c.get("/api/v1/cloudflare/accounts", headers=headers)).json()[0]
        await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/sync", headers=headers)

        with count_queries() as large:
            res = await c.get("/api/v1/domains", headers=headers, params={"limit": "50"})
        assert len(res.json()["items"]) == 30

    assert len(large.statements) == len(small.statements), (
        f"{len(small.statements)} queries for 3 domains, {len(large.statements)} for 30 — "
        "something resolves per row"
    )


async def test_reading_the_billing_answer_needs_no_permission_the_list_did_not(
    client_for, cloudflare
) -> None:
    """The resolved answer is part of a domain row, not a second grant: a member who may read
    domains reads it, and a member who may not still gets nothing (§15 deny-by-default)."""
    cloudflare.add_registration("vanons.nl")
    t = await make_tenant("inv-perm")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        await _domain(c, headers, "vanons.nl", company)
        await _cf_synced(c, headers, cloudflare)

    member = await make_tenant("inv-perm-member", email="inv-perm-member@example.com")
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        await add_membership(session, t.org.id, member.user.id, role="member")
        await session.commit()
    # ``member`` was conjured with its own tenant, so it holds two memberships; the session
    # under test is the one in ``t`` (a session names its org — CLAUDE.md §5).
    member_headers = await auth_cookie(member.user, org_id=t.org.id)
    async with client_for(t.host) as c:
        rows = await _by_name(c, member_headers)
        assert rows["vanons.nl"]["invoiceable_effective"] is True
        assert rows["vanons.nl"]["registers"] == ["cloudflare"]
