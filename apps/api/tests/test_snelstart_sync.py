"""snelstart (epic #377): the four syncs, end to end against the fake administration.

The property every test here defends is the one #31 calls a real-world incident: **an invoice is
never booked twice**. Four independent guards make that true and each of them is exercised on
its own, because a single "push it twice and see" test would pass on any one of them working and
tell you nothing about the other three:

1. the stored link,
2. a lookup by invoice number when there is no link,
3. SnelStart's own ``BOE-0021`` duplicate refusal,
4. and a write that got **no answer at all** — the one #31 singles out, because a blind retry
   there is how the incident actually happens.

The rest is the ordinary shape of a sync: what it reads, what it decides not to write, what it
refuses to guess, and what it reports when a row fails.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.integrations.snelstart import client as snelstart_client
from tests.conftest import Tenant, auth_cookie, make_tenant, org_today
from tests.snelstart_fake import FakeSnelstart

KOPPELSLEUTEL = "clpNemhxZWhOeHQ0TXVncVp1RC9WTXBx:QXBWNVVOU2FUV3VYTytZcVNSc2xrays"
SUBSCRIPTION_KEY = "40e32908b9d34996b145af4c8eed6d20"

#: The seeded revenue account every test books to. ``8200 Omzet hoog (diensten)`` is what a
#: Dutch agency actually uses, which is why the fake carries it by its real number.
LEDGER_HIGH = "8200"
LEDGER_LOW = "8210"


@pytest.fixture
def snelstart(monkeypatch) -> FakeSnelstart:
    from app.config import settings

    monkeypatch.setattr(settings, "snelstart_subscription_key", SUBSCRIPTION_KEY)
    fake = FakeSnelstart()
    snelstart_client.set_transport(fake.transport())
    yield fake
    snelstart_client.set_transport(None)


# --------------------------------------------------------------------------------------- #
# Fixtures for a tenant that can actually invoice
# --------------------------------------------------------------------------------------- #
async def _setup_org(client, headers) -> None:
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
                "coc_number": "12345678",
                "iban": "NL02ABNA0123456789",
                "email": "administratie@agency.nl",
            }
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text


async def _map_rates(client, headers) -> None:
    """Point each seeded tax rate at a SnelStart grootboek number.

    ``TaxRate.ledger_code`` already existed for exactly this (its docstring names SnelStart), so
    the mapping rides there rather than in a second table this module invents.
    """
    rates = (await client.get("/api/v1/invoicing/tax-rates", headers=headers)).json()
    for rate in rates:
        pct = Decimal(str(rate["rate"]))
        code = LEDGER_HIGH if pct >= 21 else (LEDGER_LOW if pct > 0 else LEDGER_HIGH)
        resp = await client.patch(
            f"/api/v1/invoicing/tax-rates/{rate['id']}",
            json={"ledger_code": code},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text


async def _company(client, headers, name: str = "Bakkerij Jansen", **extra) -> str:
    resp = await client.post(
        "/api/v1/companies",
        json={"name": name, "invoice_email": "boekhouding@jansen.nl", **extra},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _issued_invoice(client, headers, company_id: str, *, amount: str = "1000") -> dict:
    created = await client.post(
        "/api/v1/invoicing/invoices",
        json={
            "company_id": company_id,
            "lines": [{"description": "Onderhoud website", "quantity": "1", "unit_price": amount}],
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    issued = await client.post(
        f"/api/v1/invoicing/invoices/{created.json()['id']}/issue", json={}, headers=headers
    )
    assert issued.status_code == 200, issued.text
    return issued.json()


async def _connected(client, headers, **extra) -> dict:
    """An account that has verified and pulled its reference data — the real precondition.

    Written as a helper rather than a fixture because *forgetting* it is a real failure mode: a
    push with no cached ledgers cannot resolve "8200" to a uuid, and the test for that refusal
    needs the un-synced state.
    """
    created = await client.post(
        "/api/v1/snelstart/accounts",
        json={"name": "SnelStart", "client_key": KOPPELSLEUTEL, **extra},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    account = created.json()
    verified = await client.post(
        f"/api/v1/snelstart/accounts/{account['id']}/verify", headers=headers
    )
    assert verified.json()["ok"] is True, verified.text
    synced = await client.post(
        f"/api/v1/snelstart/accounts/{account['id']}/sync/reference", headers=headers
    )
    assert synced.status_code == 200, synced.text
    assert synced.json()["ok"] is True, synced.text
    patched = await client.patch(
        f"/api/v1/snelstart/accounts/{account['id']}",
        json={"default_ledger_code": LEDGER_HIGH},
        headers=headers,
    )
    assert patched.status_code == 200, patched.text
    return account


# --------------------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------------------- #
async def test_the_ledger_picker_offers_revenue_accounts_and_not_the_btw_account(
    client_for, snelstart
) -> None:
    """Offering all 233 accounts is offering a way to make an invoice that means nothing.

    Booking a sales line to *Btw af te dragen hoog* produces a boeking that balances, reconciles
    and is wrong — the kind of mistake nobody finds until an accountant does.
    """
    t: Tenant = await make_tenant("snel-ledgers")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _connected(c, headers)
        res = await c.get(
            f"/api/v1/snelstart/accounts/{account['id']}/ledgers", headers=headers
        )
        assert res.status_code == 200, res.text
        codes = {row["code"] for row in res.json()}
        assert codes == {"8200", "8210", "8250"}
        assert "1671" not in codes


# --------------------------------------------------------------------------------------- #
# Relations
# --------------------------------------------------------------------------------------- #
async def test_relations_are_matched_on_identifiers_and_only_proposed_on_a_name(
    client_for, snelstart
) -> None:
    """A KvK number identifies a legal entity; a name identifies nothing.

    *Jansen bv* and *Jansen Transport bv* are two companies and one substring, so a name match is
    offered on the review screen and never applied by a sync.
    """
    t: Tenant = await make_tenant("snel-match")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _connected(c, headers)
        await _company(c, headers, name="Bakkerij Jansen", coc_number="12345678")
        await _company(c, headers, name="Slagerij De Vries")

        snelstart.add_relatie(naam="Jansen bv", kvkNummer="12345678")
        snelstart.add_relatie(naam="Slagerij De Vries")
        snelstart.add_relatie(naam="Onbekende klant uit 2019")

        run = await c.post(
            f"/api/v1/snelstart/accounts/{account['id']}/sync/relations", headers=headers
        )
        assert run.status_code == 200, run.text
        counts = run.json()["counts"]
        # Only the KvK match is adopted. The name match and the stranger are left for a human.
        assert counts["linked"] == 1
        assert counts["unlinked"] == 2

        review = await c.get(
            f"/api/v1/snelstart/accounts/{account['id']}/relations", headers=headers
        )
        by_name = {row["name"]: row for row in review.json()}
        assert by_name["Jansen bv"]["linked"] is True
        assert by_name["Jansen bv"]["match_on"] == "linked"
        # Proposed, with the reason shown, so a reviewer knows which rows to actually read.
        assert by_name["Slagerij De Vries"]["match_on"] == "name"
        assert by_name["Slagerij De Vries"]["linked"] is False
        assert by_name["Onbekende klant uit 2019"]["match_on"] is None


async def test_snelstarts_own_fixtures_are_never_offered_for_review(
    client_for, snelstart
) -> None:
    """Every administration ships three rows that are not clients.

    The agency's own relation (still called *"<Vul hier uw bedrijfsnaam in>"* in a fresh one) and
    the reserved ``-1``/``-2`` placeholders. Left in, they are two thirds of a first review's
    "needs a decision" list — noise in the one place an admin has to read every row, and the
    list is only worth reading if every row on it is a real question.
    """
    t: Tenant = await make_tenant("snel-fixtures")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _connected(c, headers)
        snelstart.add_relatie(
            naam="<Vul hier uw bedrijfsnaam in>",
            relatiecode=1,
            relatiesoort=["Klant", "Leverancier", "Eigen"],
        )
        snelstart.add_relatie(naam="Klant onbekend", relatiecode=-2)
        snelstart.add_relatie(naam="Een echte klant bv", relatiecode=1005)

        review = await c.get(
            f"/api/v1/snelstart/accounts/{account['id']}/relations", headers=headers
        )
        assert [row["name"] for row in review.json()] == ["Een echte klant bv"], review.text

        run = await c.post(
            f"/api/v1/snelstart/accounts/{account['id']}/sync/relations", headers=headers
        )
        assert run.json()["counts"]["read"] == 1, run.text


async def test_an_ambiguous_identifier_matches_nothing_rather_than_the_first_row(
    client_for, snelstart
) -> None:
    """Two companies sharing a VAT number cannot be told apart by it.

    Picking whichever was loaded first is how an invoice goes to the wrong company with nothing
    on any screen to suggest it.
    """
    t: Tenant = await make_tenant("snel-ambig")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _connected(c, headers)
        await _company(c, headers, name="Holding A", vat_number="NL999999999B01")
        await _company(c, headers, name="Holding B", vat_number="NL999999999B01")
        snelstart.add_relatie(naam="Holding", btwNummer="NL999999999B01")

        await c.post(
            f"/api/v1/snelstart/accounts/{account['id']}/sync/relations", headers=headers
        )
        review = await c.get(
            f"/api/v1/snelstart/accounts/{account['id']}/relations", headers=headers
        )
        assert review.json()[0]["company_id"] is None
        assert review.json()[0]["match_on"] is None


async def test_pushing_a_relation_twice_writes_once(client_for, snelstart) -> None:
    """A nightly sync mostly finds nothing to say, and must cost nothing to say it."""
    t: Tenant = await make_tenant("snel-relpush")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _connected(c, headers)
        company_id = await _company(c, headers)
        await _map_rates(c, headers)
        await _issued_invoice(c, headers, company_id)

        first = await c.post(
            f"/api/v1/snelstart/accounts/{account['id']}/push/relations", headers=headers
        )
        assert first.json()["counts"]["created"] == 1, first.text

        writes_before = len([call for call in snelstart.calls if call[0] in ("POST", "PUT")])
        second = await c.post(
            f"/api/v1/snelstart/accounts/{account['id']}/push/relations", headers=headers
        )
        assert second.json()["counts"]["unchanged"] == 1, second.text
        writes_after = len([call for call in snelstart.calls if call[0] in ("POST", "PUT")])
        assert writes_after == writes_before, "an unchanged relation must not be rewritten"


async def test_a_relatiecode_already_taken_by_this_client_adopts_them(
    client_for, snelstart
) -> None:
    """schakl's client numbers and SnelStart's relatiecodes are two uncoordinated systems.

    The usual reason one is taken is that the bookkeeper entered this very client first, so
    ``REL-0008`` is not a failure — it is a pointer. Refusing the whole create over it (which is
    what this replaces) cost an agency a client record for a number nobody cares about, and left
    an admin with "SnelStart weigert dit verzoek" and nothing to do about it but renumber their
    CRM.
    """
    t: Tenant = await make_tenant("snel-code-adopt")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _connected(c, headers)
        await _map_rates(c, headers)
        company_id = await _company(c, headers, name="Bakkerij Jansen", coc_number="12345678")
        await _issued_invoice(c, headers, company_id)

        # The bookkeeper got there first, under the same number and the same KvK.
        existing = snelstart.add_relatie(
            naam="Bakkerij Jansen bv", relatiecode=1001, kvkNummer="12345678"
        )
        await c.patch(
            f"/api/v1/companies/{company_id}", json={"client_number": "1001"}, headers=headers
        )

        run = await c.post(
            f"/api/v1/snelstart/accounts/{account['id']}/push/relations", headers=headers
        )
        assert run.json()["ok"] is True, run.text
        assert run.json()["counts"]["adopted"] == 1, run.text
        assert len(snelstart.relaties) == 1, "the bookkeeper's relation was adopted, not doubled"
        assert existing["id"] in {row["id"] for row in snelstart.relaties.values()}


async def test_a_relatiecode_taken_by_somebody_else_creates_without_it(
    client_for, snelstart
) -> None:
    """The number belongs to a different company, so the client is created without one.

    The shared number is a convenience; the relation is the requirement. SnelStart allocates its
    own, and the link records the code it really got rather than the one we asked for — so the
    screen tells the truth.
    """
    t: Tenant = await make_tenant("snel-code-renumber")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _connected(c, headers)
        await _map_rates(c, headers)
        company_id = await _company(c, headers, name="Bakkerij Jansen", coc_number="12345678")
        await _issued_invoice(c, headers, company_id)

        snelstart.add_relatie(naam="Iemand anders bv", relatiecode=1001, kvkNummer="99999999")
        await c.patch(
            f"/api/v1/companies/{company_id}", json={"client_number": "1001"}, headers=headers
        )

        run = await c.post(
            f"/api/v1/snelstart/accounts/{account['id']}/push/relations", headers=headers
        )
        assert run.json()["ok"] is True, run.text
        assert run.json()["counts"]["created"] == 1, run.text
        assert len(snelstart.relaties) == 2
        mine = next(r for r in snelstart.relaties.values() if r["naam"] == "Bakkerij Jansen")
        assert mine["relatiecode"] != 1001, "SnelStart allocated its own"


# --------------------------------------------------------------------------------------- #
# Invoices — the four idempotency guards
# --------------------------------------------------------------------------------------- #
async def test_an_invoice_is_pushed_once_and_the_boeking_carries_its_btw(
    client_for, snelstart
) -> None:
    """The happy path, asserted on what SnelStart actually holds afterwards."""
    t: Tenant = await make_tenant("snel-push1")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _connected(c, headers)
        await _map_rates(c, headers)
        company_id = await _company(c, headers)
        invoice = await _issued_invoice(c, headers, company_id, amount="1000")

        run = await c.post(
            f"/api/v1/snelstart/accounts/{account['id']}/push/invoices", headers=headers
        )
        assert run.status_code == 200, run.text
        assert run.json()["ok"] is True, run.text
        assert run.json()["counts"]["created"] == 1

        assert len(snelstart.boekingen) == 1
        boeking = next(iter(snelstart.boekingen.values()))
        assert boeking["factuurnummer"] == invoice["number"]
        # Sent as a decimal string, stored as a number — the live round-trip exactly.
        assert Decimal(str(boeking["factuurbedrag"])) == Decimal("1210.00")
        assert [regel["btwSoort"] for regel in boeking["boekingsregels"]] == ["Hoog"]
        assert Decimal(str(boeking["boekingsregels"][0]["bedrag"])) == Decimal("1000.00")
        # The document-level vocabulary is a *different* one from the line's — swapping them
        # answers BOE-0082.
        assert len(boeking["btw"]) == 1
        assert boeking["btw"][0]["btwSoort"] == "VerkopenHoog"
        assert Decimal(str(boeking["btw"][0]["btwBedrag"])) == Decimal("210.00")
        # The PDF rode along, because a boeking without its document is what an accountant asks
        # for at year end.
        assert len(boeking["documents"]) == 1

        # And invoicing's own provider-independent record was written, so the seam's
        # ``/refs`` route — shipped for #31 before any provider existed — is not empty.
        refs = await c.get(
            f"/api/v1/invoicing/invoices/{invoice['id']}/refs", headers=headers
        )
        assert refs.status_code == 200, refs.text
        assert [row["provider"] for row in refs.json()] == ["snelstart"]


async def test_guard_one_the_stored_link_stops_a_second_push(client_for, snelstart) -> None:
    t: Tenant = await make_tenant("snel-idem1")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _connected(c, headers)
        await _map_rates(c, headers)
        company_id = await _company(c, headers)
        await _issued_invoice(c, headers, company_id)

        await c.post(
            f"/api/v1/snelstart/accounts/{account['id']}/push/invoices", headers=headers
        )
        second = await c.post(
            f"/api/v1/snelstart/accounts/{account['id']}/push/invoices", headers=headers
        )
        # Already linked, so the batch does not even pick it up again.
        assert second.json()["counts"]["read"] == 0
        assert len(snelstart.boekingen) == 1


async def test_guard_two_an_invoice_number_already_in_the_books_is_adopted(
    client_for, snelstart
) -> None:
    """An administration usually predates the integration.

    A number that is already booked — by a previous install, or by a bookkeeper typing it in —
    must be adopted, not duplicated. This is the case no stored link can cover, because there
    is no link.
    """
    t: Tenant = await make_tenant("snel-idem2")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _connected(c, headers)
        await _map_rates(c, headers)
        company_id = await _company(c, headers)
        invoice = await _issued_invoice(c, headers, company_id)

        # Somebody already booked this number, by hand, before we ever ran — **and for a
        # different amount**, which is what makes this the interesting case rather than a
        # re-install meeting its own work.
        relatie = snelstart.add_relatie(naam="Bakkerij Jansen")
        snelstart._verkoopboekingen  # noqa: B018 — documenting the resource under test
        import httpx

        request = httpx.Request(
            "POST",
            "https://b2bapi.snelstart.nl/v2/verkoopboekingen",
            json={
                "factuurnummer": invoice["number"],
                "factuurdatum": "2026-08-16T00:00:00",
                "klant": {"id": relatie["id"]},
                "factuurbedrag": 999.99,
                "boekingsregels": [
                    {"omschrijving": "handmatig", "grootboek": {"id": "x"}, "bedrag": 826.44}
                ],
            },
        )
        snelstart._handle(request)
        assert len(snelstart.boekingen) == 1

        run = await c.post(
            f"/api/v1/snelstart/accounts/{account['id']}/push/invoices", headers=headers
        )
        assert len(snelstart.boekingen) == 1, "the existing boeking was adopted, not duplicated"

        # …and because the hand-made boeking's amount is **not** the invoice's, the outcome is
        # `drift` rather than `adopted`, and it is on the run's own error list. Adopting
        # silently would leave two amounts disagreeing under one invoice number with nothing
        # anywhere saying so — the silent half of a silent overwrite.
        body = run.json()
        assert body["counts"]["drift"] == 1, run.text
        assert body["counts"]["adopted"] == 0, run.text
        assert body["ok"] is True, "drift is not a failure; nothing needs retrying"
        assert body["errors"][0]["key"] == "errors.snelstart.invoice_differs"
        assert "999.99" in body["errors"][0]["message"], run.text


async def test_an_adopted_boeking_that_agrees_is_not_reported_as_drift(
    client_for, snelstart
) -> None:
    """Drift has to mean something, which means it must not fire on a match.

    A re-installed schakl meeting its own previous pushes is the ordinary case, and reporting
    every one of them as "needs a human" would make the signal worthless within a week.
    """
    t: Tenant = await make_tenant("snel-adopt-agrees")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _connected(c, headers)
        await _map_rates(c, headers)
        company_id = await _company(c, headers)
        invoice = await _issued_invoice(c, headers, company_id)

        # Push, then forget the link entirely — a fresh install against the same books.
        await c.post(
            f"/api/v1/snelstart/accounts/{account['id']}/push/invoices", headers=headers
        )
        await _forget_links(t)

        run = await c.post(
            f"/api/v1/snelstart/accounts/{account['id']}/push/invoices",
            json={"invoice_ids": [invoice["id"]]},
            headers=headers,
        )
        assert run.json()["counts"]["adopted"] == 1, run.text
        assert run.json()["counts"]["drift"] == 0, run.text
        assert run.json()["errors"] == []


async def test_guard_three_boe_0021_is_an_answer_not_a_failure(client_for, snelstart) -> None:
    """SnelStart refusing a duplicate number is it telling us the document is already there.

    Reported as a failure it would look like a broken sync an admin should retry — which is the
    one thing that must never happen.
    """
    from app.integrations.snelstart.client import CODE_DUPLICATE_INVOICE_NUMBER

    assert CODE_DUPLICATE_INVOICE_NUMBER == "BOE-0021"

    t: Tenant = await make_tenant("snel-idem3")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _connected(c, headers)
        await _map_rates(c, headers)
        company_id = await _company(c, headers)
        invoice = await _issued_invoice(c, headers, company_id)

        # Push once, then wipe our link so the next push has no memory at all — and make the
        # lookup-by-number miss, so BOE-0021 is the *only* guard left standing.
        await c.post(
            f"/api/v1/snelstart/accounts/{account['id']}/push/invoices", headers=headers
        )
        await _forget_links(t)
        snelstart.facturen.clear()

        run = await c.post(
            f"/api/v1/snelstart/accounts/{account['id']}/push/invoices",
            json={"invoice_ids": [invoice["id"]]},
            headers=headers,
        )
        # The duplicate refusal sent us looking; the boeking count is what proves nothing
        # doubled, whichever way the lookup resolved.
        assert len(snelstart.boekingen) == 1, run.text
        assert run.json()["counts"].get("created", 0) == 0, run.text


async def test_guard_four_a_write_with_no_answer_is_looked_up_never_retried(
    client_for, snelstart, monkeypatch
) -> None:
    """#31's hard requirement: a timeout is **unknown**, not failed.

    A blind retry here is precisely how a client receives one invoice twice.
    """
    t: Tenant = await make_tenant("snel-idem4")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _connected(c, headers)
        await _map_rates(c, headers)
        company_id = await _company(c, headers)
        invoice = await _issued_invoice(c, headers, company_id)

        # The boeking is created, and then the gateway dies on the way back.
        real_handle = snelstart._handle
        state = {"swallowed": False}

        def flaky(request):
            response = real_handle(request)
            if (
                not state["swallowed"]
                and request.method == "POST"
                and request.url.path.endswith("/verkoopboekingen")
            ):
                state["swallowed"] = True
                import httpx

                raise httpx.ReadTimeout("gateway went away", request=request)
            return response

        monkeypatch.setattr(snelstart, "_handle", flaky)
        snelstart_client.set_transport(__import__("httpx").MockTransport(flaky))

        run = await c.post(
            f"/api/v1/snelstart/accounts/{account['id']}/push/invoices",
            json={"invoice_ids": [invoice["id"]]},
            headers=headers,
        )
        assert state["swallowed"] is True, "the timeout must actually have fired"
        assert len(snelstart.boekingen) == 1, run.text
        # It went and looked, found the boeking, and adopted it rather than writing again.
        assert run.json()["counts"].get("adopted") == 1, run.text


async def _forget_links(tenant: Tenant) -> None:
    """Drop this org's link rows, so a push has to fall back on an outside lookup."""
    from sqlalchemy import delete

    from app.db import async_session_maker, set_current_org
    from app.integrations.snelstart.models import SnelstartLink, SnelstartLinkKind

    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        await session.execute(
            delete(SnelstartLink).where(
                SnelstartLink.org_id == tenant.org.id,
                SnelstartLink.kind == SnelstartLinkKind.INVOICE.value,
            )
        )
        await session.commit()


async def test_a_push_refuses_rather_than_guessing_which_account_revenue_lands_in(
    client_for, snelstart
) -> None:
    """A wrong grootboek is quiet: it balances, it reconciles, and it is found at year end."""
    t: Tenant = await make_tenant("snel-noledger")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        # Connected and reference-synced, but with **no** default ledger and no rate mapping.
        created = await c.post(
            "/api/v1/snelstart/accounts",
            json={"name": "SnelStart", "client_key": KOPPELSLEUTEL},
            headers=headers,
        )
        account = created.json()
        await c.post(f"/api/v1/snelstart/accounts/{account['id']}/verify", headers=headers)
        await c.post(
            f"/api/v1/snelstart/accounts/{account['id']}/sync/reference", headers=headers
        )
        company_id = await _company(c, headers)
        await _issued_invoice(c, headers, company_id)

        run = await c.post(
            f"/api/v1/snelstart/accounts/{account['id']}/push/invoices", headers=headers
        )
        assert run.json()["ok"] is False, run.text
        assert run.json()["counts"]["failed"] == 1
        assert run.json()["errors"][0]["key"] == "errors.snelstart.ledger_unmapped"
        assert snelstart.boekingen == {}


async def test_a_draft_is_never_pushed(client_for, snelstart) -> None:
    """It has no number, and ``factuurnummer`` is required (BOE-0058) — but the real reason is
    that a draft is not a document anybody has agreed to yet."""
    t: Tenant = await make_tenant("snel-draft")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _connected(c, headers)
        await _map_rates(c, headers)
        company_id = await _company(c, headers)
        draft = await c.post(
            "/api/v1/invoicing/invoices",
            json={
                "company_id": company_id,
                "lines": [{"description": "W", "quantity": "1", "unit_price": "10"}],
            },
            headers=headers,
        )
        res = await c.post(
            f"/api/v1/snelstart/accounts/{account['id']}/push/invoices/"
            f"{draft.json()['id']}",
            headers=headers,
        )
        assert res.status_code == 409, res.text
        assert res.json()["error"]["message"] == "errors.snelstart.invoice_not_issued"


# --------------------------------------------------------------------------------------- #
# Payments — the one thing that flows back
# --------------------------------------------------------------------------------------- #
async def test_a_settled_invoice_comes_back_as_an_ordinary_payment(
    client_for, snelstart
) -> None:
    """SnelStart knows the bank statement was matched; schakl learns it as an ``InvoicePayment``.

    Deliberately not a status flipped directly: everything downstream — settling, ``invoice.paid``,
    the dunning cron, the client portal — then behaves exactly as it does for a payment typed in
    by hand, because as far as it can tell that is what it is.
    """
    t: Tenant = await make_tenant("snel-paid")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _connected(c, headers)
        await _map_rates(c, headers)
        company_id = await _company(c, headers)
        invoice = await _issued_invoice(c, headers, company_id, amount="1000")
        await c.post(
            f"/api/v1/snelstart/accounts/{account['id']}/push/invoices", headers=headers
        )

        snelstart.pay(invoice["number"])
        run = await c.post(
            f"/api/v1/snelstart/accounts/{account['id']}/sync/payments", headers=headers
        )
        assert run.status_code == 200, run.text
        assert run.json()["ok"] is True, run.text
        assert run.json()["counts"]["booked"] == 1

        after = await c.get(f"/api/v1/invoicing/invoices/{invoice['id']}", headers=headers)
        body = after.json()
        assert body["status"] == "paid", after.text
        assert Decimal(body["paid_total"]) == Decimal("1210.00")
        assert len(body["payments"]) == 1
        assert body["payments"][0]["method"] == "bank"
        assert body["payments"][0]["paid_on"] == org_today().isoformat()


async def test_a_partial_payment_is_booked_as_a_partial_payment(client_for, snelstart) -> None:
    """A client who paid half is not a client who paid.

    An integration that only recognised "fully settled" would leave the invoice looking
    untouched, which is worse than not syncing at all — it looks like nothing arrived.
    """
    t: Tenant = await make_tenant("snel-partial")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _connected(c, headers)
        await _map_rates(c, headers)
        company_id = await _company(c, headers)
        invoice = await _issued_invoice(c, headers, company_id, amount="1000")
        await c.post(
            f"/api/v1/snelstart/accounts/{account['id']}/push/invoices", headers=headers
        )

        snelstart.pay(invoice["number"], amount=500.0)
        await c.post(
            f"/api/v1/snelstart/accounts/{account['id']}/sync/payments", headers=headers
        )
        body = (
            await c.get(f"/api/v1/invoicing/invoices/{invoice['id']}", headers=headers)
        ).json()
        assert body["status"] == "open"
        assert Decimal(body["paid_total"]) == Decimal("500.00")
        assert Decimal(body["outstanding"]) == Decimal("710.00")


async def test_a_second_reconcile_books_nothing_further(client_for, snelstart) -> None:
    """The cron runs nightly. It must not add a payment every night."""
    t: Tenant = await make_tenant("snel-repeat")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _connected(c, headers)
        await _map_rates(c, headers)
        company_id = await _company(c, headers)
        invoice = await _issued_invoice(c, headers, company_id, amount="1000")
        await c.post(
            f"/api/v1/snelstart/accounts/{account['id']}/push/invoices", headers=headers
        )
        snelstart.pay(invoice["number"], amount=500.0)

        first = await c.post(
            f"/api/v1/snelstart/accounts/{account['id']}/sync/payments", headers=headers
        )
        assert first.json()["counts"]["booked"] == 1
        second = await c.post(
            f"/api/v1/snelstart/accounts/{account['id']}/sync/payments", headers=headers
        )
        assert second.json()["counts"]["booked"] == 0, second.text
        body = (
            await c.get(f"/api/v1/invoicing/invoices/{invoice['id']}", headers=headers)
        ).json()
        assert len(body["payments"]) == 1


async def test_snelstart_never_writes_a_payment_off(client_for, snelstart) -> None:
    """The sync only ever books money **in**.

    If SnelStart says *more* is owed than schakl thinks, that is a human decision about somebody's
    books — an automatic reversal of a recorded payment is not a thing an unattended cron should
    be able to do.
    """
    t: Tenant = await make_tenant("snel-noreverse")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _connected(c, headers)
        await _map_rates(c, headers)
        company_id = await _company(c, headers)
        invoice = await _issued_invoice(c, headers, company_id, amount="1000")
        await c.post(
            f"/api/v1/snelstart/accounts/{account['id']}/push/invoices", headers=headers
        )
        # Recorded in schakl, not in SnelStart — the disagreement that must not self-correct.
        paid = await c.post(
            f"/api/v1/invoicing/invoices/{invoice['id']}/payments",
            json={"paid_on": org_today().isoformat(), "amount": "600.00", "method": "bank"},
            headers=headers,
        )
        assert paid.status_code in (200, 201), paid.text

        run = await c.post(
            f"/api/v1/snelstart/accounts/{account['id']}/sync/payments", headers=headers
        )
        assert run.json()["counts"]["booked"] == 0, run.text
        body = (
            await c.get(f"/api/v1/invoicing/invoices/{invoice['id']}", headers=headers)
        ).json()
        assert Decimal(body["paid_total"]) == Decimal("600.00")


# --------------------------------------------------------------------------------------- #
# Articles
# --------------------------------------------------------------------------------------- #
async def test_a_product_without_a_code_is_skipped_and_counted(client_for, snelstart) -> None:
    """Inventing an artikelcode would put a number in somebody's article file that schakl would
    have to keep guessing identically for ever. Skipped and counted is what tells an agency to go
    and fill them in."""
    t: Tenant = await make_tenant("snel-articles")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _connected(c, headers)
        for payload in (
            {"name": "Onderhoud", "code": "1001", "unit_price": "85"},
            {"name": "Naamloos", "unit_price": "10"},
            {"name": "Verkeerde code", "code": "WEB-01", "unit_price": "10"},
        ):
            created = await c.post(
                "/api/v1/invoicing/products", json=payload, headers=headers
            )
            assert created.status_code == 201, created.text

        run = await c.post(
            f"/api/v1/snelstart/accounts/{account['id']}/push/articles", headers=headers
        )
        counts = run.json()["counts"]
        assert counts["created"] == 1, run.text
        assert counts["skipped"] == 1
        # The administration is set to `Numeriek`, so WEB-01 is refused *before* the write,
        # named, and reported — rather than discovered as ART-0003 halfway through.
        assert counts["failed"] == 1
        assert run.json()["errors"][0]["key"] == "errors.snelstart.article_code_not_numeric"
        assert [row["artikelcode"] for row in snelstart.artikelen.values()] == ["1001"]


# --------------------------------------------------------------------------------------- #
# Failures are visible (#31)
# --------------------------------------------------------------------------------------- #
async def test_a_failed_run_is_recorded_where_a_human_can_read_it(
    client_for, snelstart
) -> None:
    """A finance sync whose last error lives only in a log line is one nobody trusts."""
    t: Tenant = await make_tenant("snel-runs")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _connected(c, headers)
        snelstart.reject_key = True
        failed = await c.post(
            f"/api/v1/snelstart/accounts/{account['id']}/sync/reference", headers=headers
        )
        assert failed.json()["ok"] is False, failed.text

        runs = await c.get(
            f"/api/v1/snelstart/accounts/{account['id']}/runs", headers=headers
        )
        assert runs.status_code == 200, runs.text
        latest = runs.json()[0]
        assert latest["kind"] == "reference"
        assert latest["ok"] is False
        assert latest["message"], "SnelStart's own words must be readable on the screen"
        # …and the account went red, because a *rejected credential* earns that.
        listed = await c.get("/api/v1/snelstart/accounts", headers=headers)
        assert listed.json()[0]["status"] == "error"


async def test_being_unreachable_does_not_flag_the_credential_as_broken(
    client_for, snelstart
) -> None:
    """``cloudflare``'s rule: a rejection earns the red status, an outage does not.

    SnelStart being away for ninety seconds is not a reason to tell an agency their connection is
    broken. The text is recorded either way; the status is what a screen shouts about.
    """
    t: Tenant = await make_tenant("snel-offline")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _connected(c, headers)
        snelstart.offline = True
        run = await c.post(
            f"/api/v1/snelstart/accounts/{account['id']}/sync/reference", headers=headers
        )
        assert run.json()["ok"] is False, run.text
        row = (await c.get("/api/v1/snelstart/accounts", headers=headers)).json()[0]
        assert row["status"] == "active", "an outage is not a broken credential"
        assert row["last_error"], "…but it is still recorded"


# --------------------------------------------------------------------------------------- #
# The nightly cron
# --------------------------------------------------------------------------------------- #
async def test_a_failed_nightly_sync_reaches_somebody_who_can_fix_it(
    client_for, snelstart
) -> None:
    """#31: failures are visible, retryable **and notified**.

    The notification's default audience is the *watchers* of its entity, and nobody watches a
    ``snelstart_account`` — there is no screen on which to start. So an emit with no recipient
    hint writes an event row nobody ever sees: the requirement satisfied on paper and by nobody
    in practice. This asserts the other thing — that it lands in the inbox of somebody holding
    ``snelstart.settings.manage``.
    """
    from app.integrations.snelstart.jobs import snelstart_nightly

    t: Tenant = await make_tenant("snel-nightly")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _connected(c, headers)
        before = await c.get("/api/v1/notifications?limit=50", headers=headers)
        assert before.status_code == 200, before.text
        seen_before = len(before.json().get("items", before.json()))

    # The credential stops working overnight, which is the ordinary way this fails.
    snelstart.reject_key = True
    await snelstart_nightly()

    async with client_for(t.host) as c:
        after = await c.get("/api/v1/notifications?limit=50", headers=headers)
        assert after.status_code == 200, after.text
        items = after.json().get("items", after.json())
        assert len(items) > seen_before, after.text
        assert any(row["event_type"] == "snelstart.sync.failed" for row in items), after.text


async def test_pairing_a_client_who_is_already_paired_is_refused_not_a_500(
    client_for, snelstart
) -> None:
    """The partial unique index is the guarantee; without a check it *enforces* it as a 500.

    Not a theoretical path either: a bookkeeper with the same client entered twice is the
    ordinary reason somebody opens the review screen, so the honest answer has to be "that client
    is already paired", not "server error".
    """
    t: Tenant = await make_tenant("snel-double-adopt")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _connected(c, headers)
        company_id = await _company(c, headers, name="Camping De Duinen", coc_number="11223344")
        snelstart.add_relatie(naam="Camping De Duinen", kvkNummer="11223344")
        snelstart.add_relatie(naam="Camping de Duinen bv")

        await c.post(
            f"/api/v1/snelstart/accounts/{account['id']}/sync/relations", headers=headers
        )
        review = await c.get(
            f"/api/v1/snelstart/accounts/{account['id']}/relations", headers=headers
        )
        loose = next(row for row in review.json() if not row["linked"])

        res = await c.post(
            f"/api/v1/snelstart/accounts/{account['id']}/links/{loose['link_id']}/adopt",
            json={"local_id": company_id},
            headers=headers,
        )
        assert res.status_code == 409, res.text
        assert res.json()["error"]["message"] == "errors.snelstart.already_linked"
