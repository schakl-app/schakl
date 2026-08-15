"""oxxa module (issue #296): credentials, the register, the delegation push, isolation.

Three things carry more weight here than anywhere else in the suite, and each has a test whose
only job is to keep it true:

* **The credential is in the query string.** So nothing — not a stored ``last_error``, not an
  error envelope, not even the test fake's own call log — may ever carry it.
* **A nameserver group is shared.** ``nsgroup_upd`` repoints *every* domain using the group; this
  module must therefore find-or-create and never update one. ``test_a_push_never_updates_a_shared
  _nameserver_group`` is the single most important assertion in this file.
* **The split into ``(sld, tld)`` is the registrar's own TLD list talking, not string surgery.**
  Guessing it addresses the wrong object at the registrar, which is unrecoverable from here.

And one that is about *this* codebase rather than about OXXA: a registrar refusal is **reported,
not raised**. ``require_context`` rolls the session back on any exception, so a raising write path
throws away the record of its own failed attempt. ``test_a_refused_push_is_reported_and_survives
_the_request`` asserts the persistence through a second request, because that is the only way to
tell a row that was written from a row that was written and rolled back.
"""

from __future__ import annotations

import uuid

import pytest

from app.db import async_session_maker, set_current_org
from app.integrations.oxxa import client as oxxa_client
from app.integrations.oxxa.client import nsgroup_alias, redact
from app.registry import registry
from tests.conftest import add_membership, auth_cookie, make_tenant
from tests.oxxa_fake import FakeOxxa

API_USER = "breik-reseller"
API_PASSWORD = "Sup3rGeheim!wachtwoord"

CF_NAMESERVERS = ["ana.ns.cloudflare.com", "bob.ns.cloudflare.com"]


@pytest.fixture
def oxxa() -> FakeOxxa:
    """An OXXA that holds state, installed as the module's only transport.

    Unset, ``client._transport`` is ``None`` and a forgotten stub fails loudly on connect rather
    than reaching the real api.oxxa.com — which is the whole reason the seam exists.
    """
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


async def _domain(c, headers, name: str, company_id: str) -> dict:
    res = await c.post(
        "/api/v1/domains", json={"name": name, "company_id": company_id}, headers=headers
    )
    assert res.status_code == 201, res.text
    return res.json()


async def _account(c, headers, name: str = "Breik reseller", **extra) -> dict:
    payload = {"name": name, "api_user": API_USER, "api_password": API_PASSWORD, **extra}
    res = await c.post("/api/v1/oxxa/accounts", json=payload, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


async def _verified_account(c, headers, name: str = "Breik reseller") -> dict:
    """A credential the module has probed, so it holds a TLD list and can split a name."""
    account = await _account(c, headers, name)
    res = await c.post(f"/api/v1/oxxa/accounts/{account['id']}/verify", headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["ok"] is True, res.text
    return account


async def _push(c, headers, domain_id: str, nameservers: list[str], **extra):
    return await c.post(
        f"/api/v1/oxxa/domains/{domain_id}/nameservers",
        json={"nameservers": nameservers, **extra},
        headers=headers,
    )


# --------------------------------------------------------------------------------------- #
# Module wiring
# --------------------------------------------------------------------------------------- #
def test_oxxa_module_is_licensed_and_never_reaches_a_client() -> None:
    """The commercial boundary is the sku on the descriptor (issue #137); the safety boundary is
    that none of these three keys is ever a client's by default (#296, CLAUDE.md §15)."""
    module = registry.get("oxxa")
    assert module is not None and module.sku == "oxxa"
    assert {p.key for p in module.permissions} == {
        "oxxa.settings.manage",
        "oxxa.registrar.sync",
        "oxxa.registrar.manage",
    }
    assert all("client" not in p.default_roles for p in module.permissions)
    assert all("client" not in p.default_own_roles for p in module.permissions)


# --------------------------------------------------------------------------------------- #
# The credential
# --------------------------------------------------------------------------------------- #
async def test_the_api_password_is_write_only_and_survives_an_unrelated_patch(
    client_for, oxxa
) -> None:
    """It goes in and never comes back out — and a PATCH that does not name it keeps it."""
    oxxa.require_credentials(API_USER, API_PASSWORD)
    t = await make_tenant("oxxa-secret")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        created = await c.post(
            "/api/v1/oxxa/accounts",
            json={"name": "Breik reseller", "api_user": API_USER, "api_password": API_PASSWORD},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        assert API_PASSWORD not in created.text
        assert "password" not in {k for k in created.json() if k != "password_configured"}
        assert created.json()["password_configured"] is True
        account_id = created.json()["id"]

        listed = await c.get("/api/v1/oxxa/accounts", headers=headers)
        assert listed.status_code == 200, listed.text
        assert API_PASSWORD not in listed.text

        verified = await c.post(f"/api/v1/oxxa/accounts/{account_id}/verify", headers=headers)
        assert verified.json()["ok"] is True, verified.text
        assert verified.json()["tld_count"] == len(oxxa.tlds)

        # A rename must not blank the credential — the stored one still authenticates.
        renamed = await c.patch(
            f"/api/v1/oxxa/accounts/{account_id}", json={"name": "Hernoemd"}, headers=headers
        )
        assert renamed.status_code == 200, renamed.text
        again = await c.post(f"/api/v1/oxxa/accounts/{account_id}/verify", headers=headers)
        assert again.json()["ok"] is True, again.text

        # Rotating it drops everything the old credential vouched for, and the new one fails.
        rotated = await c.patch(
            f"/api/v1/oxxa/accounts/{account_id}",
            json={"api_password": "verkeerd-wachtwoord"},
            headers=headers,
        )
        assert rotated.status_code == 200, rotated.text
        row = (await c.get("/api/v1/oxxa/accounts", headers=headers)).json()[0]
        assert row["tld_count"] == 0 and row["last_verified_at"] is None
        refused = await c.post(f"/api/v1/oxxa/accounts/{account_id}/verify", headers=headers)
        assert refused.json()["ok"] is False and refused.json()["error"]


def test_redact_blanks_the_password_anywhere_it_appears() -> None:
    """The one function standing between OXXA's auth design and the activity log."""
    url = f"https://api.oxxa.com/command.php?apiuser=x&apipassword={API_PASSWORD}&command=funds_get"
    assert redact(url) == (
        "https://api.oxxa.com/command.php?apiuser=x&apipassword=***&command=funds_get"
    )
    assert API_PASSWORD not in redact(url)
    # Case-insensitive, and a value that runs to the end of the string is still caught.
    assert redact("APIPASSWORD=geheim") == "APIPASSWORD=***"
    assert redact("no credential here") == "no credential here"


async def test_a_provider_error_carrying_a_url_is_redacted_before_it_is_stored(
    client_for, oxxa
) -> None:
    """OXXA's own prose is stored verbatim on the row (it is untranslatable, §9) — so it is the
    one place a password could reach the database, and it is redacted on the way in."""
    leaky = (
        "aanroep van https://api.oxxa.com/command.php?apiuser=breik-reseller"
        f"&apipassword={API_PASSWORD}&command=funds_get is mislukt"
    )
    oxxa.fail_command("funds_get", leaky)
    t = await make_tenant("oxxa-redact")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _account(c, headers)
        verified = await c.post(
            f"/api/v1/oxxa/accounts/{account['id']}/verify", headers=headers
        )
        assert verified.status_code == 200, verified.text
        assert verified.json()["ok"] is False
        assert API_PASSWORD not in verified.text
        assert "apipassword=***" in verified.json()["error"]

        stored = (await c.get("/api/v1/oxxa/accounts", headers=headers)).json()[0]
        assert stored["status"] == "error"
        assert API_PASSWORD not in stored["last_error"]
        assert "apipassword=***" in stored["last_error"]


async def test_the_fake_never_records_the_credential(client_for, oxxa) -> None:
    """A harness that logged the request URL would put the tenant's password in every failure
    output — the leak ``redact`` exists to prevent, reintroduced one layer down."""
    t = await make_tenant("oxxa-nolog")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _verified_account(c, headers)

    assert oxxa.calls, "the verify should have called OXXA"
    assert API_PASSWORD not in repr(oxxa.calls)
    assert API_USER not in repr(oxxa.calls)
    for command, params in oxxa.calls:
        assert "://" not in command  # a command name, never a URL
        assert not {"apiuser", "apipassword"} & set(params)


# --------------------------------------------------------------------------------------- #
# Tenant isolation (CLAUDE.md §9 — required per module)
# --------------------------------------------------------------------------------------- #
async def test_accounts_are_tenant_isolated(client_for, oxxa) -> None:
    """Golden Rule 1: another tenant's registrar credential is not readable, not by id, and not
    deletable — the highest-blast-radius row this module owns."""
    a = await make_tenant("oxxa-iso-a")
    b = await make_tenant("oxxa-iso-b")
    a_headers, b_headers = await auth_cookie(a.user), await auth_cookie(b.user)
    async with client_for(a.host) as ca:
        account = await _account(ca, a_headers)
    async with client_for(b.host) as cb:
        assert (await cb.get("/api/v1/oxxa/accounts", headers=b_headers)).json() == []
        assert (await cb.get("/api/v1/oxxa/accounts/options", headers=b_headers)).json() == []
        assert (
            await cb.patch(
                f"/api/v1/oxxa/accounts/{account['id']}",
                json={"name": "gestolen"},
                headers=b_headers,
            )
        ).status_code == 404
        assert (
            await cb.delete(f"/api/v1/oxxa/accounts/{account['id']}", headers=b_headers)
        ).status_code == 404
        assert (
            await cb.post(f"/api/v1/oxxa/accounts/{account['id']}/verify", headers=b_headers)
        ).status_code == 404


async def test_the_register_and_the_push_are_tenant_isolated(client_for, oxxa) -> None:
    """Nothing another tenant registered is readable, and — the one that would be unrecoverable
    — no other tenant's domain can have its live delegation repointed from here."""
    a = await make_tenant("oxxa-riso-a")
    b = await make_tenant("oxxa-riso-b")
    a_headers, b_headers = await auth_cookie(a.user), await auth_cookie(b.user)
    oxxa.add_domain("klant.nl")
    async with client_for(a.host) as ca:
        company = await _company(ca, a_headers)
        domain = await _domain(ca, a_headers, "klant.nl", company)
        account = await _verified_account(ca, a_headers)
        synced = await ca.post(
            f"/api/v1/oxxa/accounts/{account['id']}/sync", headers=a_headers
        )
        assert synced.status_code == 200 and synced.json()["found"] == 1, synced.text

    async with client_for(b.host) as cb:
        register = await cb.get("/api/v1/oxxa/domains", headers=b_headers)
        assert register.status_code == 200, register.text
        assert register.json()["items"] == [] and register.json()["total"] == 0

        for method, path in (
            ("get", f"/api/v1/oxxa/domains/{domain['id']}/status"),
            ("post", f"/api/v1/oxxa/domains/{domain['id']}/refresh"),
        ):
            res = await getattr(cb, method)(path, headers=b_headers)
            assert res.status_code == 404, f"{path}: {res.status_code} {res.text}"

        oxxa.calls.clear()
        pushed = await _push(cb, b_headers, domain["id"], CF_NAMESERVERS)
        assert pushed.status_code == 404, pushed.text
        # Refused before anything reached the registrar.
        assert oxxa.calls == []


async def test_a_plain_member_holds_none_of_it(client_for, oxxa) -> None:
    """All three keys are admin-only by default: widening "may edit a domain" must never
    silently hand out "may move this client's nameservers"."""
    t = await make_tenant("oxxa-member")
    member = await make_tenant("oxxa-member-m", email="oxxa-member-user@example.com")
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        await add_membership(session, t.org.id, member.user.id, role="member")
        await session.commit()
    owner_headers = await auth_cookie(t.user)
    # ``member`` was conjured with its own tenant, so it holds two memberships; the session
    # under test is the one in ``t`` (a session names its org — CLAUDE.md §5).
    member_headers = await auth_cookie(member.user, org_id=t.org.id)

    async with client_for(t.host) as c:
        company = await _company(c, owner_headers)
        domain = await _domain(c, owner_headers, "klant.nl", company)
        for method, path in (
            ("get", "/api/v1/oxxa/accounts"),
            ("get", "/api/v1/oxxa/accounts/options"),
            ("get", "/api/v1/oxxa/domains"),
            ("get", f"/api/v1/oxxa/domains/{domain['id']}/status"),
        ):
            res = await getattr(c, method)(path, headers=member_headers)
            assert res.status_code == 403, f"{path}: {res.status_code}"
        assert (
            await _push(c, member_headers, domain["id"], CF_NAMESERVERS)
        ).status_code == 403


# --------------------------------------------------------------------------------------- #
# The sld/tld split — refused, never guessed
# --------------------------------------------------------------------------------------- #
async def test_an_unverified_account_refuses_to_split_a_name(client_for, oxxa) -> None:
    """No TLD list means no authority for the split, and a guess addresses the wrong object at
    the registrar. Refused *before* a single request goes out."""
    t = await make_tenant("oxxa-unverified")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        domain = await _domain(c, headers, "klant.nl", company)
        await _account(c, headers)  # created, never verified
        oxxa.calls.clear()

        refused = await _push(c, headers, domain["id"], CF_NAMESERVERS)
        assert refused.status_code == 409, refused.text
        assert refused.json()["error"]["code"] == "oxxa_not_verified"
        assert oxxa.calls == []


async def test_an_unknown_tld_and_a_subdomain_are_both_refused(client_for, oxxa) -> None:
    """``klant.xyz`` is not in the credential's TLD list; ``shop.klant.nl`` is a hostname inside
    a zone. Splitting either would silently operate on a different domain."""
    t = await make_tenant("oxxa-split")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        await _verified_account(c, headers)
        unknown = await _domain(c, headers, "klant.xyz", company)
        inside = await _domain(c, headers, "shop.klant.nl", company)

        oxxa.calls.clear()
        for domain in (unknown, inside):
            refused = await _push(c, headers, domain["id"], CF_NAMESERVERS)
            assert refused.status_code == 409, refused.text
            assert refused.json()["error"]["code"] == "oxxa_unknown_tld", refused.text
        assert oxxa.calls == []

        # And the longest suffix wins where one does match: ``co.uk``, not ``uk``.
        multi = await _domain(c, headers, "klant.co.uk", company)
        oxxa.add_domain("klant.co.uk")
        pushed = await _push(c, headers, multi["id"], CF_NAMESERVERS)
        assert pushed.status_code == 200, pushed.text
        assert oxxa.params_for("domain_ns_upd")[0]["sld"] == "klant"
        assert oxxa.params_for("domain_ns_upd")[0]["tld"] == "co.uk"


# --------------------------------------------------------------------------------------- #
# The write path — find-or-create, never update, idempotent
# --------------------------------------------------------------------------------------- #
async def test_a_push_never_updates_a_shared_nameserver_group(client_for, oxxa) -> None:
    """**The most important assertion in this file.** An OXXA nameserver group is a shared
    object: updating one repoints every domain that uses it. So the module finds-or-creates and
    never calls ``nsgroup_upd`` — and pushing a delegation a domain already has writes nothing at
    all, which is what makes a retry free."""
    t = await make_tenant("oxxa-push")
    headers = await auth_cookie(t.user)
    old = oxxa.add_nsgroup("klant-eigen-groep", ["ns1.oud.nl", "ns2.oud.nl"])
    oxxa.add_domain("klant.nl", nsgroup=old)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        domain = await _domain(c, headers, "klant.nl", company)
        await _verified_account(c, headers)

        first = await _push(c, headers, domain["id"], CF_NAMESERVERS)
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["ok"] is True and body["changed"] is True
        assert body["nameservers"] == CF_NAMESERVERS
        # A new group was created — named after its own contents — and the domain repointed.
        assert "nsgroup_add" in oxxa.commands
        assert oxxa.nsgroups[body["nsgroup_ref"]]["alias"] == nsgroup_alias(CF_NAMESERVERS)
        assert oxxa.domains["klant.nl"]["nsgroup"] == body["nsgroup_ref"]
        # The tenant's own group is untouched: this is a create, not an edit.
        assert oxxa.nsgroups[old]["nameservers"] == ["ns1.oud.nl", "ns2.oud.nl"]

        # Pushing the same delegation again: no group is created, nothing is written. The log is
        # cleared so the two assertions below can say "in *this* push" — but the first push is
        # the one that creates a group, so its commands are kept for the final assertion rather
        # than thrown away with it.
        creating_push = list(oxxa.commands)
        oxxa.calls.clear()
        second = await _push(c, headers, domain["id"], CF_NAMESERVERS)
        assert second.status_code == 200, second.text
        assert second.json()["changed"] is False
        assert second.json()["nsgroup_ref"] == body["nsgroup_ref"]
        assert "domain_ns_upd" not in oxxa.commands
        assert "nsgroup_add" not in oxxa.commands
        idempotent_push = list(oxxa.commands)

    # Across the **whole** exchange — the push that created a group and the one that found it —
    # the one command that would repoint somebody else's domains was never sent. If this ever
    # fires, a client's live delegation is at stake. (``FakeOxxa`` refuses ``nsgroup_upd``
    # outright as well, so a regression cannot even reach a tidy reported failure.)
    assert "nsgroup_upd" not in creating_push + idempotent_push


async def test_a_group_edited_by_hand_at_the_registrar_is_refused(client_for, oxxa) -> None:
    """Our alias, somebody else's members: a human edited it in the OXXA portal. Conflicts are
    reported, never resolved (docs/CLOUDFLARE.md §5) — repointing it would move every domain
    that group serves."""
    t = await make_tenant("oxxa-conflict")
    headers = await auth_cookie(t.user)
    # A group carrying *our* deterministic alias but different nameservers.
    theirs = oxxa.add_nsgroup(nsgroup_alias(CF_NAMESERVERS), ["ns9.anders.nl", "ns8.anders.nl"])
    oxxa.add_domain("klant.nl")
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        domain = await _domain(c, headers, "klant.nl", company)
        await _verified_account(c, headers)
        oxxa.calls.clear()

        refused = await _push(c, headers, domain["id"], CF_NAMESERVERS)
        # Reported, not raised: like every other registrar refusal the push persists the attempt
        # and answers 200 with ``ok=false``, so the panel can say what happened instead of
        # rolling the row back and handing the user an empty form (see
        # ``test_a_refused_push_is_reported_and_survives_the_request``).
        assert refused.status_code == 200, refused.text
        assert refused.json()["ok"] is False
        # Its **own** key, not ``oxxa_unreachable``: a group somebody edited by hand is not a
        # blip, and "try again in a moment" is advice that can never work.
        assert refused.json()["error"] == "errors.oxxa_nsgroup_conflict"
        # Nothing was written, and the tenant's group still holds what they put in it.
        assert "domain_ns_upd" not in oxxa.commands
        assert "nsgroup_add" not in oxxa.commands
        assert "nsgroup_upd" not in oxxa.commands
        assert oxxa.domains["klant.nl"]["nsgroup"] is None
        assert oxxa.nsgroups[theirs]["nameservers"] == ["ns9.anders.nl", "ns8.anders.nl"]

        # And the refusal is on the record: an admin has to go and resolve it at OXXA, so the
        # domain page must still be saying so tomorrow.
        status = await c.get(f"/api/v1/oxxa/domains/{domain['id']}/status", headers=headers)
        assert status.status_code == 200, status.text
        assert "push_error" in status.json()["issues"]
        assert status.json()["registrar"]["ns_push_status"] == "error"


async def test_a_refused_push_is_reported_and_survives_the_request(client_for, oxxa) -> None:
    """A push OXXA refuses must come back as a **reported failure**, not an exception.

    ``require_context`` rolls the session back on any exception, so raising here would discard
    the row the failure branch just wrote: the panel would reload with an empty form, no record
    of the attempt and no reason for it, and the user would retype nameservers they already
    typed. So the write path answers the way ``verify`` and ``sync`` do — HTTP 200, ``ok=false``,
    an i18n key — and the attempt is on the record **after the request has committed**, which is
    what the second half of this test proves by asking again through fresh requests.
    """
    t = await make_tenant("oxxa-push-refused")
    headers = await auth_cookie(t.user)
    oxxa.add_domain("klant.nl")
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        domain = await _domain(c, headers, "klant.nl", company)
        await _verified_account(c, headers)
        # The group is created fine; it is repointing the domain at it that the registrar
        # refuses — the failure that leaves the most half-done state behind.
        oxxa.fail_command("domain_ns_upd", "Het register weigerde deze wijziging")

        refused = await _push(c, headers, domain["id"], CF_NAMESERVERS)
        assert refused.status_code == 200, refused.text
        body = refused.json()
        assert body["ok"] is False
        assert body["changed"] is False
        assert body["nameservers"] == CF_NAMESERVERS
        assert body["error"] == "errors.oxxa_request_failed"
        # Nothing moved at the registrar.
        assert oxxa.domains["klant.nl"]["nsgroup"] is None

        # --- a fresh request: did the row survive the one that wrote it? ---
        listed = (await c.get("/api/v1/oxxa/domains", headers=headers)).json()
        assert listed["total"] == 1, listed
        row = listed["items"][0]
        assert row["ns_push_status"] == "error"
        assert row["ns_desired"] == CF_NAMESERVERS
        assert row["last_error"], "the registrar's own words are what the panel shows"
        assert row["ns_pushed_at"] is None  # nothing was applied, so nothing was "sent"

        status = await c.get(f"/api/v1/oxxa/domains/{domain['id']}/status", headers=headers)
        assert status.status_code == 200, status.text
        assert "push_error" in status.json()["issues"]
        assert status.json()["registrar"]["ns_desired"] == CF_NAMESERVERS

        # And retrying is the whole point of keeping it: once OXXA stops refusing, the same
        # request succeeds and the error clears.
        oxxa.failures.pop("domain_ns_upd")
        retried = await _push(c, headers, domain["id"], CF_NAMESERVERS)
        assert retried.status_code == 200, retried.text
        assert retried.json()["ok"] is True and retried.json()["changed"] is True
        again = (await c.get("/api/v1/oxxa/domains", headers=headers)).json()["items"][0]
        assert again["ns_push_status"] == "active" and again["last_error"] is None
        # Only ever one register row for the domain: the failed attempt created it, the retry
        # found it.
        assert (await c.get("/api/v1/oxxa/domains", headers=headers)).json()["total"] == 1


async def test_a_failed_push_never_stores_the_password_it_was_sent_with(
    client_for, oxxa
) -> None:
    """The refusal is persisted verbatim (OXXA's prose is untranslatable, §9) — and OXXA puts the
    credential in the query string, so provider text quoting the failed call is the one place a
    password could reach the database through the *write* path."""
    leaky = (
        "aanroep van https://api.oxxa.com/command.php?apiuser=breik-reseller"
        f"&apipassword={API_PASSWORD}&command=domain_ns_upd is mislukt"
    )
    t = await make_tenant("oxxa-push-redact")
    headers = await auth_cookie(t.user)
    oxxa.add_domain("klant.nl")
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        domain = await _domain(c, headers, "klant.nl", company)
        await _verified_account(c, headers)
        oxxa.fail_command("domain_ns_upd", leaky)

        refused = await _push(c, headers, domain["id"], CF_NAMESERVERS)
        assert refused.status_code == 200, refused.text
        assert refused.json()["ok"] is False and refused.json()["error"]
        # The envelope carries an i18n key, never the provider's sentence (§9).
        assert API_PASSWORD not in refused.text

        row = (await c.get("/api/v1/oxxa/domains", headers=headers)).json()["items"][0]
        assert row["ns_push_status"] == "error"
        assert API_PASSWORD not in row["last_error"]
        assert "apipassword=***" in row["last_error"]


# --------------------------------------------------------------------------------------- #
# Sync + drift
# --------------------------------------------------------------------------------------- #
async def test_sync_matches_by_name_keeps_the_unmatched_and_reports_drift_only_where_we_pushed(
    client_for, oxxa
) -> None:
    """The three things a register sync is for, in one flow: match what we know, surface what we
    do not, and disagree out loud with the registrar only where we actually asked for something.
    A domain we never pushed is not "drifted" — it is simply somebody else's delegation."""
    t = await make_tenant("oxxa-sync")
    headers = await auth_cookie(t.user)
    ours = oxxa.add_nsgroup("groep-a", ["ns1.a.nl", "ns2.a.nl"])
    theirs = oxxa.add_nsgroup("groep-b", ["ns1.b.nl", "ns2.b.nl"])
    oxxa.add_domain("klant.nl", nsgroup=ours)
    oxxa.add_domain("onbekend.nl", nsgroup=theirs)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        domain = await _domain(c, headers, "klant.nl", company)
        account = await _verified_account(c, headers)

        first = await c.post(f"/api/v1/oxxa/accounts/{account['id']}/sync", headers=headers)
        assert first.status_code == 200, first.text
        assert first.json() == {
            "ok": True,
            "found": 2,
            "matched": 1,
            "unmatched": 1,
            "drifted": 0,
            "error": None,
        }

        # The unmatched row is listed, never hidden: a domain the agency renews and nobody bills.
        orphans = await c.get("/api/v1/oxxa/domains?linked=false", headers=headers)
        assert [row["name"] for row in orphans.json()["items"]] == ["onbekend.nl"]
        assert orphans.json()["total"] == 1

        # Ask for a delegation, then let somebody move it back in the OXXA portal.
        assert (await _push(c, headers, domain["id"], CF_NAMESERVERS)).status_code == 200
        oxxa.domains["klant.nl"]["nsgroup"] = ours

        second = await c.post(f"/api/v1/oxxa/accounts/{account['id']}/sync", headers=headers)
        assert second.status_code == 200, second.text
        assert second.json()["drifted"] == 1
        assert second.json()["found"] == 2
        # Syncing twice creates no duplicate rows.
        assert (await c.get("/api/v1/oxxa/domains", headers=headers)).json()["total"] == 2

        listed = {
            row["name"]: row
            for row in (await c.get("/api/v1/oxxa/domains", headers=headers)).json()["items"]
        }
        assert listed["klant.nl"]["ns_push_status"] == "drift"
        assert listed["klant.nl"]["ns_observed"] == ["ns1.a.nl", "ns2.a.nl"]
        assert listed["klant.nl"]["ns_desired"] == CF_NAMESERVERS
        # The one nobody pushed keeps its "we never asked" status, not a drift.
        assert listed["onbekend.nl"]["ns_push_status"] == "pending"
        assert listed["onbekend.nl"]["domain_id"] is None

        status = await c.get(f"/api/v1/oxxa/domains/{domain['id']}/status", headers=headers)
        assert "nameserver_drift" in status.json()["issues"]


async def test_refresh_reads_what_a_register_wide_sync_cannot_afford(client_for, oxxa) -> None:
    """DNSSEC and the registrant's name only come from the per-domain commands, which is exactly
    why the refresh is a button and not a cron."""
    t = await make_tenant("oxxa-refresh")
    headers = await auth_cookie(t.user)
    group = oxxa.add_nsgroup("groep-a", ["ns1.a.nl", "ns2.a.nl"])
    oxxa.add_domain("klant.nl", nsgroup=group, dnssec=True, registrant="OXXA-12345")
    oxxa.add_identity(
        "OXXA-12345",
        company_name="Klant BV",
        firstname="Jan",
        lastname="Jansen",
        email="info@klant.nl",
        city="Amsterdam",
        country="NL",
    )
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        domain = await _domain(c, headers, "klant.nl", company)
        await _verified_account(c, headers)

        refreshed = await c.post(
            f"/api/v1/oxxa/domains/{domain['id']}/refresh", headers=headers
        )
        assert refreshed.status_code == 200, refreshed.text
        registrar = refreshed.json()["registrar"]
        assert registrar["dnssec"] is True
        assert registrar["registrant_name"] == "Klant BV"
        assert registrar["registrant"]["email"] == "info@klant.nl"
        assert registrar["ns_observed"] == ["ns1.a.nl", "ns2.a.nl"]
        assert registrar["sld"] == "klant" and registrar["tld"] == "nl"
        assert registrar["transfer_lock"] is True
        assert "domain_inf" in oxxa.commands and "identity_get" in oxxa.commands


async def test_status_answers_from_stored_rows_and_never_calls_the_registrar(
    client_for, oxxa
) -> None:
    """A domain page must render at full speed and must still render when OXXA is down
    (docs/PERFORMANCE.md). ``refresh`` is the explicit "go look"."""
    t = await make_tenant("oxxa-cheap")
    headers = await auth_cookie(t.user)
    oxxa.add_domain("klant.nl")
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        domain = await _domain(c, headers, "klant.nl", company)
        account = await _verified_account(c, headers)
        await c.post(f"/api/v1/oxxa/accounts/{account['id']}/sync", headers=headers)

        oxxa.calls.clear()
        status = await c.get(f"/api/v1/oxxa/domains/{domain['id']}/status", headers=headers)
        assert status.status_code == 200, status.text
        assert status.json()["configured"] is True
        assert status.json()["registrar"]["name"] == "klant.nl"
        assert oxxa.calls == []


# --------------------------------------------------------------------------------------- #
# Company horizon (#285)
# --------------------------------------------------------------------------------------- #
async def test_the_company_horizon_reaches_the_register(client_for, oxxa) -> None:
    """``oxxa_domains`` carries no ``company_id`` — a register row's client is its *domain's*
    (#285 failure mode 1). Without ``__company_horizon_clause__`` the repository's column match
    would filter nothing at all and a scoped membership would read every client's register."""
    t = await make_tenant("oxxa-horizon")
    member = await make_tenant("oxxa-horizon-m", email="oxxa-horizon-member@example.com")
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        membership = await add_membership(session, t.org.id, member.user.id, role="admin")
        membership_id = membership.id
        await session.commit()
    owner_headers = await auth_cookie(t.user)
    # ``member`` was conjured with its own tenant, so it holds two memberships; the session
    # under test is the one in ``t`` (a session names its org — CLAUDE.md §5).
    member_headers = await auth_cookie(member.user, org_id=t.org.id)

    oxxa.add_domain("alpha.nl")
    oxxa.add_domain("beta.nl")
    async with client_for(t.host) as c:
        alpha = await _company(c, owner_headers, "Alpha")
        beta = await _company(c, owner_headers, "Beta")
        await _domain(c, owner_headers, "alpha.nl", alpha)
        beta_domain = await _domain(c, owner_headers, "beta.nl", beta)
        account = await _verified_account(c, owner_headers)
        synced = await c.post(
            f"/api/v1/oxxa/accounts/{account['id']}/sync", headers=owner_headers
        )
        assert synced.json()["matched"] == 2, synced.text

        group = (
            await c.post(
                "/api/v1/companies/groups", json={"name": "Portfolio"}, headers=owner_headers
            )
        ).json()
        assert (
            await c.put(
                f"/api/v1/companies/groups/{group['id']}/companies",
                json={"company_ids": [alpha]},
                headers=owner_headers,
            )
        ).status_code == 204
        assert (
            await c.put(
                f"/api/v1/companies/groups/{group['id']}/memberships",
                json={"membership_ids": [str(membership_id)]},
                headers=owner_headers,
            )
        ).status_code == 204

        # Grant the scoped admin the oxxa read key explicitly, so a leak cannot hide behind 403.
        roles = (await c.get("/api/v1/roles", headers=owner_headers)).json()
        admin_role = next(r for r in roles if r["key"] == "admin")
        assert (
            await c.patch(
                f"/api/v1/roles/{admin_role['id']}",
                json={
                    "permissions": sorted(
                        set(admin_role["permissions"]) | {"oxxa.registrar.sync"}
                    )
                },
                headers=owner_headers,
            )
        ).status_code == 200

        register = await c.get("/api/v1/oxxa/domains", headers=member_headers)
        assert register.status_code == 200, register.text
        assert [row["name"] for row in register.json()["items"]] == ["alpha.nl"]
        # The total must count exactly what the list could return (#285 failure mode 2).
        assert register.json()["total"] == 1

        # Beta's status report answers 404 — never 403, which would leak that it exists (§15).
        blocked = await c.get(
            f"/api/v1/oxxa/domains/{beta_domain['id']}/status", headers=member_headers
        )
        assert blocked.status_code == 404

        # The owner still sees both.
        assert (
            await c.get("/api/v1/oxxa/domains", headers=owner_headers)
        ).json()["total"] == 2


async def test_an_unknown_domain_is_404_not_500(client_for, oxxa) -> None:
    t = await make_tenant("oxxa-404")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _verified_account(c, headers)
        missing = uuid.uuid4()
        assert (
            await c.get(f"/api/v1/oxxa/domains/{missing}/status", headers=headers)
        ).status_code == 404
        assert (
            await c.post(f"/api/v1/oxxa/domains/{missing}/refresh", headers=headers)
        ).status_code == 404
        assert (await _push(c, headers, str(missing), CF_NAMESERVERS)).status_code == 404
