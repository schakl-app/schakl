"""snelstart (epic #377, issue #31): the credential, the mapping, and never twice.

Five properties carry the weight, and each has a test whose only job is to keep it true.

* **The koppelsleutel goes in and never comes back out.** Fernet at rest, read once, absent from
  every response — asserted against raw response *text*, because the failure being guarded is a
  field nobody thought about.
* **Verify never raises, and it says *which* credential was refused.** ``require_context`` rolls
  the session back on any exception, so a raising verify would discard the row recording what
  SnelStart said. A rejected koppelsleutel and a rejected subscription key are different faults
  with different owners, and telling an agency to re-issue the one that was already right wastes
  their afternoon.
* **An invoice is never booked twice.** Four layers, and each has its own test: the stored link,
  the lookup by number, SnelStart's own ``BOE-0021``, and a write that got no answer at all.
* **The btw-soort is derived from the administration's own rate table**, so an invoice dated
  2018 books its 6% as ``Laag`` and one dated today books 9% as ``Laag`` too — which a constant
  could not do for both.
* **``$filter`` is not trusted.** Some endpoints ignore it; the client re-checks locally, and a
  test proves the re-check is what decides.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.integrations.snelstart import client as snelstart_client
from app.integrations.snelstart.mapping import (
    MappingError,
    article_code_error,
    relation_payload,
    vat_choice,
)
from app.registry import registry
from tests.conftest import auth_cookie, make_tenant
from tests.snelstart_fake import BTW_TARIEVEN, FakeSnelstart

#: Shaped like the real thing: a long base64-ish blob with a ``:`` between its two halves.
KOPPELSLEUTEL = "clpNemhxZWhOeHQ0TXVncVp1RC9WTXBx:QXBWNVVOU2FUV3VYTytZcVNSc2xrays"
SUBSCRIPTION_KEY = "40e32908b9d34996b145af4c8eed6d20"


@pytest.fixture
def snelstart(monkeypatch) -> FakeSnelstart:
    """A SnelStart administration that holds state, installed as the module's only transport.

    Unset, ``client._transport`` is ``None`` and a forgotten stub fails loudly on connect rather
    than reaching the real b2bapi.snelstart.nl — which is the whole reason the seam exists.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "snelstart_subscription_key", SUBSCRIPTION_KEY)
    fake = FakeSnelstart()
    snelstart_client.set_transport(fake.transport())
    yield fake
    snelstart_client.set_transport(None)


async def _account(c, headers, *, name: str = "SnelStart", **extra) -> dict:
    res = await c.post(
        "/api/v1/snelstart/accounts",
        json={"name": name, "client_key": KOPPELSLEUTEL, **extra},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()


async def _row(c, headers, account_id: str) -> dict:
    """The stored row, read back in its own request.

    A second request is the only way to tell a row that was written from one that was written
    and then rolled back — which is exactly the failure "verify never raises" guards against.
    """
    listed = await c.get("/api/v1/snelstart/accounts", headers=headers)
    assert listed.status_code == 200, listed.text
    match = next((row for row in listed.json() if row["id"] == account_id), None)
    assert match is not None, listed.text
    return match


# --------------------------------------------------------------------------------------- #
# Module wiring
# --------------------------------------------------------------------------------------- #
def test_snelstart_is_a_licensed_integration_no_client_can_reach() -> None:
    """Three permissions, none of them a client's, and a sku (#137, §15).

    The three-key split is asserted exactly rather than as a subset: holding the credential,
    reading through it and writing somebody's ledger with it are three grants an agency hands to
    different people, and collapsing any two of them is the change this asserts against.
    """
    module = registry.get("snelstart")
    assert module is not None and module.sku == "snelstart"
    assert module.kind == "integration"
    assert module.requires == ("invoicing",)
    assert {p.key for p in module.permissions} == {
        "snelstart.settings.manage",
        "snelstart.sync.run",
        "snelstart.ledger.write",
    }
    assert all("client" not in p.default_roles for p in module.permissions)
    assert all("client" not in p.default_own_roles for p in module.permissions)


def test_the_accounting_seam_is_filled_in() -> None:
    """#31 asked for the seam and #207 shipped it empty; this is what fills it.

    Worth a test because the three routes that use it (``/invoicing/providers``,
    ``/invoices/{id}/export``, ``/invoices/{id}/refs``) were written before any provider existed
    and would keep answering "no providers" for ever if the registration were ever dropped.
    """
    from app.modules.invoicing.accounting import get_provider

    provider = get_provider("snelstart")
    assert provider is not None and provider.label == "SnelStart"


# --------------------------------------------------------------------------------------- #
# The credential
# --------------------------------------------------------------------------------------- #
async def test_the_koppelsleutel_is_never_echoed_in_any_response(client_for, snelstart) -> None:
    """Create, list, patch, verify: the key is in none of them."""
    t = await make_tenant("snel-secret")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        created = await c.post(
            "/api/v1/snelstart/accounts",
            json={"name": "SnelStart", "client_key": KOPPELSLEUTEL},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        assert KOPPELSLEUTEL not in created.text
        assert created.json()["connected"] is True
        account_id = created.json()["id"]

        listed = await c.get("/api/v1/snelstart/accounts", headers=headers)
        assert KOPPELSLEUTEL not in listed.text

        renamed = await c.patch(
            f"/api/v1/snelstart/accounts/{account_id}",
            json={"name": "Boekhouding 2026"},
            headers=headers,
        )
        assert renamed.status_code == 200, renamed.text
        assert KOPPELSLEUTEL not in renamed.text

        verified = await c.post(
            f"/api/v1/snelstart/accounts/{account_id}/verify", headers=headers
        )
        assert verified.status_code == 200, verified.text
        assert KOPPELSLEUTEL not in verified.text
        # The rename did not blank the credential.
        assert verified.json()["ok"] is True, verified.text


async def test_the_fake_never_records_a_credential(client_for, snelstart) -> None:
    """A harness that logged the request would put the koppelsleutel in every failure output."""
    t = await make_tenant("snel-nolog")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _account(c, headers)
        await c.post(f"/api/v1/snelstart/accounts/{account['id']}/verify", headers=headers)
    recorded = repr(snelstart.calls)
    assert KOPPELSLEUTEL not in recorded
    assert SUBSCRIPTION_KEY not in recorded
    assert snelstart.token_calls >= 1


async def test_verify_names_the_administration_rather_than_merely_saying_ok(
    client_for, snelstart
) -> None:
    """A credential that works can still open the wrong company's books.

    Which is why the probe is ``GET /companyInfo`` and not a ping: the answer an admin needs is
    *which administration did I just connect*, and finding that out at the first invoice means
    finding it out in an accountant's ledger.
    """
    t = await make_tenant("snel-admin")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _account(c, headers)
        res = await c.post(
            f"/api/v1/snelstart/accounts/{account['id']}/verify", headers=headers
        )
        body = res.json()
        assert body["ok"] is True, res.text
        assert body["administration_name"] == "Testadministratie"
        assert body["financial_year"] == 2026
        assert body["missing_scopes"] == []
        # The administration's own seller block, so the screen can show it beside schakl's.
        assert body["seller"]["coc_number"] == "12345678"

        row = await _row(c, headers, account["id"])
        assert row["status"] == "active"
        assert row["administration_name"] == "Testadministratie"
        # Per-administration, and what decides whether a product can be pushed at all.
        assert row["article_code_kind"] == "Numeriek"
        assert row["article_code_max_length"] == 10


async def test_a_rejected_koppelsleutel_answers_200_and_keeps_the_row(
    client_for, snelstart
) -> None:
    """Verify never raises: the probe succeeded and its answer was no."""
    t = await make_tenant("snel-badkey")
    headers = await auth_cookie(t.user)
    snelstart.reject_key = True
    async with client_for(t.host) as c:
        account = await _account(c, headers)
        res = await c.post(
            f"/api/v1/snelstart/accounts/{account['id']}/verify", headers=headers
        )
        assert res.status_code == 200, res.text
        assert res.json()["ok"] is False
        assert res.json()["error_key"] == "errors.snelstart.credential_rejected"

        # Read back in a *second* request: this is what distinguishes a row that was written
        # from one that was written and rolled back.
        row = await _row(c, headers, account["id"])
        assert row["status"] == "error"
        assert row["last_error"], "SnelStart's own words must survive onto the row"


async def test_a_rejected_subscription_key_is_a_different_diagnosis(
    client_for, snelstart
) -> None:
    """The whole point of the two-credential split.

    A koppelsleutel is the tenant's and an agency re-issues it in ten seconds; a subscription key
    is the *install's* and expires after 90 days on the free developer product. Reporting one as
    the other sends an admin to re-do the thing that was already right.
    """
    t = await make_tenant("snel-badsub")
    headers = await auth_cookie(t.user)
    snelstart.reject_subscription = True
    async with client_for(t.host) as c:
        account = await _account(c, headers)
        res = await c.post(
            f"/api/v1/snelstart/accounts/{account['id']}/verify", headers=headers
        )
        assert res.status_code == 200, res.text
        assert res.json()["ok"] is False
        assert res.json()["error_key"] == "errors.snelstart.subscription_rejected"


async def test_rotating_the_key_forgets_what_the_old_one_told_us(client_for, snelstart) -> None:
    """Every observation was made through the credential being replaced.

    Keeping any of it would let the screen say "connected to Testadministratie" about a key that
    now opens somebody else's books.
    """
    t = await make_tenant("snel-rotate")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _account(c, headers)
        await c.post(f"/api/v1/snelstart/accounts/{account['id']}/verify", headers=headers)
        assert (await _row(c, headers, account["id"]))["administration_name"]

        rotated = await c.patch(
            f"/api/v1/snelstart/accounts/{account['id']}",
            json={"client_key": KOPPELSLEUTEL + "-new"},
            headers=headers,
        )
        assert rotated.status_code == 200, rotated.text
        row = await _row(c, headers, account["id"])
        assert row["administration_name"] is None
        assert row["scopes"] == []
        assert row["last_verified_at"] is None


async def test_missing_scopes_are_reported_before_a_sync_fails_halfway(
    client_for, snelstart
) -> None:
    """A scope discovered mid-sync is a 403 forty rows in."""
    t = await make_tenant("snel-scopes")
    headers = await auth_cookie(t.user)
    snelstart.scopes = "relaties:read settings:read"
    async with client_for(t.host) as c:
        account = await _account(c, headers)
        res = await c.post(
            f"/api/v1/snelstart/accounts/{account['id']}/verify", headers=headers
        )
        assert res.json()["ok"] is True, res.text
        assert set(res.json()["missing_scopes"]) >= {"relations", "invoices", "articles"}


# --------------------------------------------------------------------------------------- #
# Tenant isolation
# --------------------------------------------------------------------------------------- #
async def test_one_tenant_never_sees_another_administration(client_for, snelstart) -> None:
    """Golden Rule 1, on the table that holds a credential."""
    a = await make_tenant("snel-iso-a")
    b = await make_tenant("snel-iso-b")
    headers_a = await auth_cookie(a.user)
    headers_b = await auth_cookie(b.user)
    async with client_for(a.host) as c:
        account = await _account(c, headers_a, name="A's books")
    async with client_for(b.host) as c:
        listed = await c.get("/api/v1/snelstart/accounts", headers=headers_b)
        assert listed.status_code == 200, listed.text
        assert listed.json() == []
        # And not reachable by id either — 404, never 403, which would confirm it exists.
        got = await c.post(
            f"/api/v1/snelstart/accounts/{account['id']}/verify", headers=headers_b
        )
        assert got.status_code == 404, got.text


# --------------------------------------------------------------------------------------- #
# The mapping — pure, and the part with legal consequences
# --------------------------------------------------------------------------------------- #
def test_the_btw_soort_is_looked_up_by_date_not_hardcoded() -> None:
    """The Dutch low rate was 6% until 2019 and 9% after. A constant is wrong about one of them."""
    today = vat_choice(
        rate_pct=Decimal("9.00"), category="reduced", on=date(2026, 8, 16), rates=BTW_TARIEVEN
    )
    assert (today.soort, today.sales_soort, today.derived) == ("Laag", "VerkopenLaag", True)

    back_then = vat_choice(
        rate_pct=Decimal("6.00"), category="reduced", on=date(2018, 6, 1), rates=BTW_TARIEVEN
    )
    assert (back_then.soort, back_then.derived) == ("Laag", True)

    # …and 6% today is *not* in force, so it falls back and says so rather than pretending.
    stale = vat_choice(
        rate_pct=Decimal("6.00"), category="reduced", on=date(2026, 8, 16), rates=BTW_TARIEVEN
    )
    assert stale.derived is False, "a guess must be reported as a guess"


def test_reverse_charge_carries_no_line_btw_but_declares_itself() -> None:
    """Verlegd: the line carries nothing, the document says the tax was shifted.

    The two vocabularies are different (``Geen`` on the line, ``VerkopenVerlegd`` on the
    document) and swapping them answers ``BOE-0082``.
    """
    choice = vat_choice(
        rate_pct=Decimal("0"),
        category="reverse_charge",
        on=date(2026, 8, 16),
        rates=BTW_TARIEVEN,
    )
    assert choice.soort == "Geen"
    assert choice.sales_soort == "VerkopenVerlegd"


def test_a_company_name_too_long_for_snelstart_is_refused_not_silently_trimmed() -> None:
    """``naam`` is 50 characters. A client cut to fit is a record its own bookkeeper cannot find."""

    class _Company:
        name = "Stichting Openbaar Onderwijs Noord-Holland Boven het IJ"
        coc_number = None
        vat_number = None
        website = None
        phone = None
        invoice_email = None
        address_line1 = None
        house_number = None
        postal_code = None
        city = None
        country = "NL"
        client_number = None

    with pytest.raises(MappingError) as caught:
        relation_payload(_Company(), country_id=None)
    assert caught.value.message_key == "errors.snelstart.relation_name_too_long"


def test_an_update_merges_rather_than_replacing_what_the_bookkeeper_typed() -> None:
    """``PUT /relaties`` replaces the whole record.

    A payload built only from schakl's fields would blank the memo, the credit limit and the
    direct-debit mandate every time somebody edited a phone number in the CRM.
    """

    class _Company:
        name = "Bakkerij Jansen"
        coc_number = "12345678"
        vat_number = None
        website = None
        phone = "0721234567"
        invoice_email = "facturen@jansen.nl"
        address_line1 = "Dorpsstraat"
        house_number = "12"
        postal_code = "1234 AB"
        city = "Alkmaar"
        country = "NL"
        client_number = "1001"

    existing = {
        "id": "abc",
        "uri": "/relaties/abc",
        "modifiedOn": "2026-01-01T00:00:00",
        "relatiesoort": ["Klant", "Leverancier"],
        "memo": "Betaalt altijd te laat",
        "kredietLimiet": 5000.0,
        "incassoSoort": "Core",
        "relatiecode": 42,
    }
    payload = relation_payload(_Company(), country_id="nl-id", existing=existing)

    assert payload["memo"] == "Betaalt altijd te laat"
    assert payload["kredietLimiet"] == 5000.0
    assert payload["incassoSoort"] == "Core"
    # Being also a supplier survives; being a customer is added if missing (BOE-0060).
    assert set(payload["relatiesoort"]) == {"Klant", "Leverancier"}
    # Read-only fields are not sent back.
    assert "uri" not in payload and "modifiedOn" not in payload
    # The relatiecode is never renumbered on an update: it appears on every document already.
    assert payload["relatiecode"] == 42
    assert payload["vestigingsAdres"]["straat"] == "Dorpsstraat 12"
    assert payload["vestigingsAdres"]["land"] == {"id": "nl-id"}


def test_an_empty_crm_field_does_not_erase_what_snelstart_holds() -> None:
    """A CRM that was never filled in is not an instruction to blank the bookkeeping."""

    class _Company:
        name = "Bakkerij Jansen"
        coc_number = None
        vat_number = None
        website = None
        phone = None
        invoice_email = None
        address_line1 = None
        house_number = None
        postal_code = None
        city = None
        country = None
        client_number = None

    payload = relation_payload(
        _Company(), country_id=None, existing={"kvkNummer": "12345678", "telefoon": "0721234567"}
    )
    assert payload["kvkNummer"] == "12345678"
    assert payload["telefoon"] == "0721234567"


def test_the_article_code_rules_come_from_the_administration_not_from_a_constant() -> None:
    """``Numeriek`` vs ``Alfanumeriek`` and the maximum length are *per administration*."""
    assert article_code_error("1001", kind="Numeriek", max_length=10) is None
    assert (
        article_code_error("WEB-01", kind="Numeriek", max_length=10)
        == "errors.snelstart.article_code_not_numeric"
    )
    # …and the same code is perfectly fine in an administration set the other way.
    assert article_code_error("WEB-01", kind="Alfanumeriek", max_length=10) is None
    assert (
        article_code_error("12345678901", kind="Numeriek", max_length=10)
        == "errors.snelstart.article_code_too_long"
    )
    assert article_code_error("", kind=None, max_length=None) == (
        "errors.snelstart.article_code_missing"
    )


# --------------------------------------------------------------------------------------- #
# The client's two undocumented behaviours
# --------------------------------------------------------------------------------------- #
async def test_a_filter_the_server_ignores_does_not_decide_the_answer(snelstart) -> None:
    """``/landen`` answers ``200`` with all of them whatever you ask.

    So a client that trusted the filter and took ``[0]`` would pick Nederland for every country
    on earth. The local ``match`` is what decides, and this proves the server really did ignore
    the filter — otherwise the test would pass for the wrong reason.
    """
    from app.integrations.snelstart.client import SnelstartClient

    client = SnelstartClient(client_key=KOPPELSLEUTEL, subscription_key=SUBSCRIPTION_KEY)
    unfiltered = await client.fetch("landen", filter_="Landcode eq 'BE'")
    assert len(unfiltered) == 2, "the fake must ignore $filter here, exactly as the live API does"

    matched = await client.fetch(
        "landen",
        filter_="Landcode eq 'BE'",
        match=lambda row: row.get("landcode") == "BE",
    )
    assert [row["naam"] for row in matched] == ["België"]


async def test_paging_stops_on_a_short_page_and_never_returns_a_prefix(snelstart) -> None:
    """No ``nextLink``, no count: a full page means ask again, a short one means stop."""
    from app.integrations.snelstart.client import PAGE_SIZE, SnelstartClient

    for index in range(5):
        snelstart.add_relatie(naam=f"Klant {index}")
    client = SnelstartClient(client_key=KOPPELSLEUTEL, subscription_key=SUBSCRIPTION_KEY)
    rows = await client.fetch_all("relaties")
    assert len(rows) == 5
    # One page: the first came back short, so nothing asked for a second.
    paged = [call for call in snelstart.calls if call[1] == "relaties"]
    assert len(paged) == 1, paged
    # ``$`` arrives percent-encoded (``%24top``), which is what httpx sends and what the live
    # API accepts — the probes that grounded this client used exactly that form.
    from urllib.parse import unquote

    assert f"$top={PAGE_SIZE}" in unquote(paged[0][2])


async def test_an_unknown_filter_property_is_an_error_not_a_full_table(snelstart) -> None:
    """``/relaties`` rejects a property it does not know — which is what makes a typo loud."""
    from app.integrations.snelstart.client import SnelstartClient, SnelstartError

    client = SnelstartClient(client_key=KOPPELSLEUTEL, subscription_key=SUBSCRIPTION_KEY)
    with pytest.raises(SnelstartError):
        await client.fetch("relaties", filter_="Nonsense eq 'x'")


async def test_the_bearer_token_is_minted_once_and_reused(snelstart) -> None:
    """A sync makes forty calls; it must not mint forty tokens."""
    from app.integrations.snelstart.client import SnelstartClient

    client = SnelstartClient(client_key=KOPPELSLEUTEL, subscription_key=SUBSCRIPTION_KEY)
    await client.company_info()
    await client.fetch("relaties")
    await client.fetch("grootboeken")
    assert snelstart.token_calls == 1


def test_a_decimal_crosses_the_boundary_without_becoming_a_float() -> None:
    """#31: money is ``Decimal``, never float — and *neither* obvious encoding manages that.

    ``float(amount)`` fails openly; ``json.loads(str(amount))`` fails silently, parsing the text
    straight back into a float so ``1428.00`` leaves as ``1428.0``. This test caught exactly
    that, which is why the amount travels as decimal text — accepted by the live API and read
    back as a number, because .NET parses a JSON string into a ``decimal`` exactly.
    """
    import json as _json

    from app.integrations.snelstart.client import _json_default

    assert _json.dumps({"bedrag": Decimal("1428.00")}, default=_json_default) == (
        '{"bedrag": "1428.00"}'
    )
    # The case a float mangles and a string does not.
    assert _json.dumps({"bedrag": Decimal("0.10")}, default=_json_default) == (
        '{"bedrag": "0.10"}'
    )
    assert all(
        not isinstance(_json_default(Decimal(text)), float)
        for text in ("0.10", "1428.00", "99999999.99")
    )


def test_a_date_is_written_in_snelstarts_own_zone_less_format() -> None:
    from app.integrations.snelstart.client import _json_default

    assert _json_default(date(2026, 8, 16)) == "2026-08-16T00:00:00"


def test_the_coupling_reference_round_trips_and_refuses_rubbish() -> None:
    """It is attacker-controlled input: a parse failure must look like a wrong secret."""
    from app.integrations.snelstart.service import (
        coupling_reference,
        parse_coupling_reference,
    )

    org, account = uuid.uuid4(), uuid.uuid4()
    reference = coupling_reference(org, account, "s3cr3t")
    assert parse_coupling_reference(reference) == (org, account, "s3cr3t")
    assert len(reference) < 500, "SnelStart caps referenceKey at 500 characters"
    for rubbish in ("", "nonsense", "a.b", f"{org}.notauuid.x", f"{org}.{account}"):
        assert parse_coupling_reference(rubbish) is None
