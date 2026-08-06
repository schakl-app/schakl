"""cloudflare module (epic #278): accounts, zones, redirects, Pages, status, isolation.

The reconciliation cases carry their weight here. "It already redirects" and "there are two
Cloudflare accounts" are not exotic — they are what an agency taking over a client's existing
setup hits on day one — so each has a test that puts Cloudflare into that state first and then
asserts on what the module *reports* rather than on what it overwrites.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core.auth.models import User
from app.db import async_session_maker, set_current_org
from app.modules.cloudflare import client as cf_client
from app.modules.cloudflare import redirects as rules
from app.registry import registry
from tests.cloudflare_fake import FakeCloudflare
from tests.conftest import add_membership, auth_cookie, make_tenant

TOKEN = "cf-token-0123456789abcdef"


@pytest.fixture
def cloudflare() -> FakeCloudflare:
    """A Cloudflare that holds state, installed as the module's only transport."""
    fake = FakeCloudflare()
    cf_client.set_transport(fake.transport())
    yield fake
    cf_client.set_transport(None)


async def _account(c, headers, name: str = "Agency", **extra) -> dict:
    payload = {"name": name, "api_token": TOKEN, **extra}
    res = await c.post("/api/v1/cloudflare/accounts", json=payload, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


async def _domain(c, headers, name: str, company_id: str) -> dict:
    res = await c.post(
        "/api/v1/domains", json={"name": name, "company_id": company_id}, headers=headers
    )
    assert res.status_code == 201, res.text
    return res.json()


async def _company(c, headers, name: str = "Klant BV") -> str:
    return (await c.post("/api/v1/companies", json={"name": name}, headers=headers)).json()["id"]


async def _connected(c, headers, fake: FakeCloudflare, *, apex: str = "klant.nl"):
    """An account, a domain, a zone at Cloudflare, and the two connected."""
    company = await _company(c, headers)
    account = await _account(c, headers)
    fake.add_zone(apex)
    domain = await _domain(c, headers, apex, company)
    assert (
        await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/verify", headers=headers)
    ).status_code == 200
    connect = await c.post(
        f"/api/v1/cloudflare/domains/{domain['id']}/connect", json={}, headers=headers
    )
    assert connect.status_code == 200, connect.text
    return account, domain, connect.json()


# --------------------------------------------------------------------------------------- #
# Module wiring
# --------------------------------------------------------------------------------------- #
def test_cloudflare_module_is_licensed() -> None:
    """The whole commercial boundary is the sku on the descriptor (issue #137, #278 §5)."""
    module = registry.get("cloudflare")
    assert module is not None and module.sku == "cloudflare"
    assert {p.key for p in module.permissions} == {
        "cloudflare.settings.manage",
        "cloudflare.dns.read",
        "cloudflare.zone.manage",
    }
    # None of them reach the client role — these mutate live DNS (#278 §4).
    assert all("client" not in p.default_roles for p in module.permissions)


# --------------------------------------------------------------------------------------- #
# Accounts + credentials
# --------------------------------------------------------------------------------------- #
async def test_token_is_write_only_and_capabilities_are_probed(client_for, cloudflare) -> None:
    t = await make_tenant("cf-token")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _account(c, headers)
        assert account["token_configured"] is True
        listed = await c.get("/api/v1/cloudflare/accounts", headers=headers)
        assert listed.status_code == 200, listed.text
        assert TOKEN not in listed.text  # the token never leaves the server

        verified = await c.post(
            f"/api/v1/cloudflare/accounts/{account['id']}/verify", headers=headers
        )
        assert verified.status_code == 200, verified.text
        body = verified.json()
        assert body["ok"] is True
        assert body["capabilities"]["token_valid"] is True
        assert body["capabilities"]["zones_read"] is True
        assert body["cf_account_id"] == "acct-1"


async def test_a_zone_scoped_token_is_degraded_not_broken(client_for, cloudflare) -> None:
    """A token that cannot list accounts still reads zones. Reporting that as a failure would
    push an admin to mint a wider token than they need (#278 §4's least-privilege intent)."""
    cloudflare.deny.add("/accounts")
    t = await make_tenant("cf-scoped")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _account(c, headers)
        body = (
            await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/verify", headers=headers)
        ).json()
        assert body["ok"] is True
        assert body["capabilities"] == {
            "token_valid": True,
            "accounts_read": False,
            "zones_read": True,
            "pages_read": False,
            # Unknowable without an account to ask about, like Pages — not a failure.
            "registrar_read": False,
        }
        assert body["cf_account_id"] is None


async def test_an_account_owned_token_verifies_at_its_own_account(client_for, cloudflare) -> None:
    """Cloudflare has two kinds of token and they verify at two different URLs.

    An **account-owned** token — what an agency mints so the integration does not leave with the
    person who made it — is refused at ``/user/tokens/verify`` with the same 401/1000 a dead
    token gets, while working for every zone call it is scoped for. Asking only the user
    endpoint made a perfectly good credential read *"Token problem: Invalid API Token"* on a
    screen whose zone list was filling in beside it.
    """
    cloudflare.account_owned_token = True
    t = await make_tenant("cf-acct-token")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _account(c, headers, cf_account_id="acct-1")
        body = (
            await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/verify", headers=headers)
        ).json()
        assert body["ok"] is True
        assert body["capabilities"]["token_valid"] is True
        assert ("GET", "/accounts/acct-1/tokens/verify") in cloudflare.calls
        row = (await c.get("/api/v1/cloudflare/accounts", headers=headers)).json()[0]
        assert row["status"] == "active" and row["last_error"] is None


async def test_an_account_owned_token_is_verified_via_a_discovered_account(
    client_for, cloudflare
) -> None:
    """No pinned ``cf_account_id`` yet — the usual state right after pasting a token. The probe
    reads ``/accounts`` first precisely so it has an id to address the verify call with."""
    cloudflare.account_owned_token = True
    t = await make_tenant("cf-acct-discover")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _account(c, headers)
        body = (
            await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/verify", headers=headers)
        ).json()
        assert body["ok"] is True
        assert body["capabilities"]["token_valid"] is True
        assert body["cf_account_id"] == "acct-1"


async def test_a_token_that_reads_zones_is_valid_even_if_no_verify_endpoint_answers(
    client_for, cloudflare
) -> None:
    """The worst case of the pair: account-owned *and* not allowed to list accounts, so neither
    verify endpoint is reachable — one is the wrong kind, the other has no id to address.

    Cloudflare is still answering this token's zone list, which is the call the module actually
    makes. A read that succeeds is better evidence than a verify that refuses, so no single
    probe may be the gate.
    """
    cloudflare.account_owned_token = True
    cloudflare.deny.add("/accounts")
    t = await make_tenant("cf-acct-blind")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _account(c, headers)
        body = (
            await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/verify", headers=headers)
        ).json()
        assert body["ok"] is True
        assert body["capabilities"]["token_valid"] is True
        assert body["capabilities"]["zones_read"] is True
        assert body["capabilities"]["accounts_read"] is False
        row = (await c.get("/api/v1/cloudflare/accounts", headers=headers)).json()[0]
        assert row["status"] == "active"


async def test_a_working_sync_clears_a_token_error_nobody_else_would(
    client_for, cloudflare
) -> None:
    """The flag was one-way: ``_flag_account`` set ``error`` and nothing but a manual re-verify
    took it off again, so a row kept its red line through every sync that plainly worked."""
    t = await make_tenant("cf-recover")
    headers = await auth_cookie(t.user)
    cloudflare.add_zone("klant.nl")
    async with client_for(t.host) as c:
        account = await _account(c, headers, cf_account_id="acct-1")
        cloudflare.revoked = True
        await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/verify", headers=headers)
        row = (await c.get("/api/v1/cloudflare/accounts", headers=headers)).json()[0]
        assert row["status"] == "error" and row["last_error"]

        # The admin re-enables the token at Cloudflare. Nothing in schakl changes.
        cloudflare.revoked = False
        synced = await c.post(
            f"/api/v1/cloudflare/accounts/{account['id']}/sync", headers=headers
        )
        assert synced.status_code == 200, synced.text
        row = (await c.get("/api/v1/cloudflare/accounts", headers=headers)).json()[0]
        assert row["status"] == "active" and row["last_error"] is None


async def test_a_failed_sync_records_why_despite_rolling_back(client_for, cloudflare) -> None:
    """``require_context`` rolls the request transaction back on any exception, so writing
    ``last_error`` and *then* raising recorded nothing: the row read healthy while the admin was
    looking at a red toast, and the settings screen — whose whole job is to say what is wrong
    with a credential — was the one place that never found out."""
    t = await make_tenant("cf-syncfail")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _account(c, headers, cf_account_id="acct-1")
        cloudflare.revoked = True
        failed = await c.post(
            f"/api/v1/cloudflare/accounts/{account['id']}/sync", headers=headers
        )
        assert failed.status_code == 409, failed.text
        assert failed.json()["error"]["code"] == "cloudflare_token_rejected"

        cloudflare.revoked = False  # only so the *read* below can happen
        row = (await c.get("/api/v1/cloudflare/accounts", headers=headers)).json()[0]
        assert row["status"] == "error"
        assert row["last_error"] and "Invalid API Token" in row["last_error"]


async def test_a_sync_fills_in_an_account_id_so_pages_is_not_skipped(
    client_for, cloudflare
) -> None:
    """Zones need no account id; Pages and Registrar are addressed by one.

    That asymmetry is what makes a half-configured row look healthy — zones arrive and fill the
    screen while the two halves that need an id are skipped, and skipped as a *zero*, which
    reads exactly like "this account has no Pages projects". Only ``verify_account`` ever filled
    the id in, so any tenant whose verify had failed kept a blank Pages panel over a Cloudflare
    account that was serving their sites.
    """
    t = await make_tenant("cf-fillid")
    headers = await auth_cookie(t.user)
    cloudflare.add_zone("klant.nl")
    cloudflare.pages["acct-1"] = [
        {"name": "klant-site", "subdomain": "klant-site.pages.dev", "production_branch": "main"}
    ]
    cloudflare.add_pages_domain("klant-site", "klant.nl")
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        await _domain(c, headers, "klant.nl", company)
        account = await _account(c, headers)  # no cf_account_id — never verified
        synced = await c.post(
            f"/api/v1/cloudflare/accounts/{account['id']}/sync", headers=headers
        )
        assert synced.status_code == 200, synced.text
        body = synced.json()
        assert "no_account_id" not in body["warnings"]
        assert body["pages_projects_synced"] == 1
        # The hostname was already attached at Cloudflare, so it arrives as an adopted link
        # rather than needing anyone to press a button that re-registers what exists.
        assert body["pages_links_adopted"] == 1
        row = (await c.get("/api/v1/cloudflare/accounts", headers=headers)).json()[0]
        assert row["cf_account_id"] == "acct-1"


async def test_a_sync_that_cannot_name_an_account_says_pages_was_not_read(
    client_for, cloudflare
) -> None:
    """"Not asked" and "nothing found" are different answers and only the warning tells them
    apart (§17's no-silent-caps rule). Two accounts behind one token is the never-guess case:
    picking one would point every later Pages call at the wrong client's account."""
    cloudflare.accounts = [{"id": "acct-1", "name": "Agency"}, {"id": "acct-2", "name": "Klant"}]
    t = await make_tenant("cf-ambig-id")
    headers = await auth_cookie(t.user)
    cloudflare.add_zone("klant.nl")
    async with client_for(t.host) as c:
        account = await _account(c, headers)
        body = (
            await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/sync", headers=headers)
        ).json()
        assert "no_account_id" in body["warnings"]
        assert body["pages_projects_synced"] == 0
        row = (await c.get("/api/v1/cloudflare/accounts", headers=headers)).json()[0]
        assert row["cf_account_id"] is None


async def test_an_invalid_token_is_recorded_not_raised(client_for, cloudflare) -> None:
    t = await make_tenant("cf-badtoken")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _account(c, headers)
        assert (
            await c.patch(
                f"/api/v1/cloudflare/accounts/{account['id']}",
                json={"api_token": "bad-token-000000000000"},
                headers=headers,
            )
        ).status_code == 200
        # A rotated token drops the old token's capability answers: they described a different
        # credential, and a stale "may create zones" is worse than none.
        after_rotate = (await c.get("/api/v1/cloudflare/accounts", headers=headers)).json()[0]
        assert after_rotate["capabilities"] == {}
        assert after_rotate["last_verified_at"] is None

        body = (
            await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/verify", headers=headers)
        ).json()
        assert body["ok"] is False and body["error"]
        row = (await c.get("/api/v1/cloudflare/accounts", headers=headers)).json()[0]
        assert row["status"] == "error" and row["last_error"]


async def test_a_malformed_token_names_the_token_not_cloudflare(client_for, cloudflare) -> None:
    """Cloudflare rejects a malformed credential with **400/6003**, before it looks the token
    up at all. On the generic key that reads as "Cloudflare refused this request", which points
    the admin at Cloudflare; the thing to fix is the token they just pasted (seen live)."""
    t = await make_tenant("cf-malformed")
    headers = await auth_cookie(t.user)
    cloudflare.malformed_token = True
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        account = await _account(c, headers)
        domain = await _domain(c, headers, "klant.nl", company)
        await c.patch(
            f"/api/v1/cloudflare/accounts/{account['id']}",
            json={"cf_account_id": "acct-1"},
            headers=headers,
        )
        refused = await c.post(
            f"/api/v1/cloudflare/domains/{domain['id']}/connect", json={}, headers=headers
        )
        assert refused.status_code == 409
        assert refused.json()["error"]["code"] == "cloudflare_token_rejected"


async def test_accounts_are_tenant_isolated(client_for, cloudflare) -> None:
    """Golden Rule 1: another tenant's credential is not readable, not even by id."""
    a = await make_tenant("cf-iso-a")
    b = await make_tenant("cf-iso-b")
    a_headers, b_headers = await auth_cookie(a.user), await auth_cookie(b.user)
    async with client_for(a.host) as ca:
        account = await _account(ca, a_headers)
    async with client_for(b.host) as cb:
        assert (await cb.get("/api/v1/cloudflare/accounts", headers=b_headers)).json() == []
        assert (
            await cb.patch(
                f"/api/v1/cloudflare/accounts/{account['id']}",
                json={"name": "gestolen"},
                headers=b_headers,
            )
        ).status_code == 404


async def test_deleting_an_account_never_touches_cloudflare(client_for, cloudflare) -> None:
    t = await make_tenant("cf-del")
    headers = await auth_cookie(t.user)
    cloudflare.add_zone("klant.nl")
    async with client_for(t.host) as c:
        account = await _account(c, headers)
        await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/verify", headers=headers)
        await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/sync", headers=headers)
        assert (await c.get("/api/v1/cloudflare/zones", headers=headers)).json()["total"] == 1

        assert (
            await c.delete(f"/api/v1/cloudflare/accounts/{account['id']}", headers=headers)
        ).status_code == 204
        # Local rows cascade; the zone still exists at Cloudflare.
        assert (await c.get("/api/v1/cloudflare/zones", headers=headers)).json()["total"] == 0
        assert len(cloudflare.zones) == 1
        assert not [call for call in cloudflare.calls if call[0] == "DELETE"]


# --------------------------------------------------------------------------------------- #
# Sync + matching
# --------------------------------------------------------------------------------------- #
async def test_sync_matches_zones_to_domains_by_apex(client_for, cloudflare) -> None:
    t = await make_tenant("cf-sync")
    headers = await auth_cookie(t.user)
    cloudflare.add_zone("klant.nl")
    cloudflare.add_zone("onbekend.nl")
    cloudflare.pages["acct-1"] = [
        {"name": "klant-site", "subdomain": "klant-site.pages.dev", "production_branch": "main"}
    ]
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        await _domain(c, headers, "klant.nl", company)
        account = await _account(c, headers)
        await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/verify", headers=headers)
        result = await c.post(
            f"/api/v1/cloudflare/accounts/{account['id']}/sync", headers=headers
        )
        assert result.status_code == 200, result.text
        assert result.json()["zones_synced"] == 2
        assert result.json()["zones_matched"] == 1
        assert result.json()["pages_projects_synced"] == 1

        # An unmatched zone is listed, never hidden: an unknown zone in a client's account is
        # exactly what an agency wants to see.
        orphans = await c.get("/api/v1/cloudflare/zones?linked=false", headers=headers)
        assert [z["name"] for z in orphans.json()["items"]] == ["onbekend.nl"]

        # Syncing twice is idempotent — no duplicate zone rows.
        await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/sync", headers=headers)
        assert (await c.get("/api/v1/cloudflare/zones", headers=headers)).json()["total"] == 2


async def test_zone_list_does_not_n_plus_one(client_for, cloudflare, count_queries) -> None:
    """A row's account and domain labels are batched. Invisible in the JSON, fatal at scale
    (docs/PERFORMANCE.md), so it is pinned by counting statements."""
    t = await make_tenant("cf-perf")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        account = await _account(c, headers)
        for i in range(6):
            cloudflare.add_zone(f"klant{i}.nl")
            await _domain(c, headers, f"klant{i}.nl", company)
        await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/verify", headers=headers)
        await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/sync", headers=headers)

        with count_queries() as counter:
            listed = await c.get("/api/v1/cloudflare/zones", headers=headers)
        assert listed.json()["total"] == 6
        # rows + count + account names + domain names, plus the request's own context lookups.
        assert len(counter.matching("cloudflare_zones")) <= 2
        assert len(counter.matching("FROM domains")) <= 1


# --------------------------------------------------------------------------------------- #
# Connect: adopt before create, and never guess the account
# --------------------------------------------------------------------------------------- #
async def test_connect_adopts_an_existing_zone_instead_of_creating_one(
    client_for, cloudflare
) -> None:
    t = await make_tenant("cf-adopt")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, domain, zone = await _connected(c, headers, cloudflare)
        assert zone["name"] == "klant.nl"
        assert zone["domain_id"] == domain["id"]
        # Cloudflare kept its one zone: nothing was created.
        assert len(cloudflare.zones) == 1
        assert ("POST", "/zones") not in cloudflare.calls
        # The nameservers the registrar half will need are stored and surfaced.
        assert zone["name_servers"] == ["ana.ns.cloudflare.com", "bob.ns.cloudflare.com"]


async def test_connect_creates_a_zone_when_there_is_none(client_for, cloudflare) -> None:
    t = await make_tenant("cf-create")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        account = await _account(c, headers)
        await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/verify", headers=headers)
        domain = await _domain(c, headers, "nieuw.nl", company)
        zone = await c.post(
            f"/api/v1/cloudflare/domains/{domain['id']}/connect", json={}, headers=headers
        )
        assert zone.status_code == 200, zone.text
        assert zone.json()["status"] == "pending"
        assert ("POST", "/zones") in cloudflare.calls

        # Adopt-only refuses rather than creating.
        other = await _domain(c, headers, "tweede.nl", company)
        refused = await c.post(
            f"/api/v1/cloudflare/domains/{other['id']}/connect",
            json={"create_if_missing": False},
            headers=headers,
        )
        assert refused.status_code == 409
        assert refused.json()["error"]["code"] == "cloudflare_zone_not_found"


async def test_connect_refuses_to_guess_between_two_accounts(client_for, cloudflare) -> None:
    """The same apex in two of the tenant's accounts. Cloudflare only makes *activation*
    exclusive, so this state is legal — and a zone created in the wrong account cannot be
    moved, only deleted and rebuilt. So we ask."""
    t = await make_tenant("cf-two")
    headers = await auth_cookie(t.user)
    cloudflare.add_zone("klant.nl", account="acct-1", status="active")
    cloudflare.add_zone("klant.nl", account="acct-2", status="pending")
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        first = await _account(c, headers, "Agency", cf_account_id="acct-1")
        second = await _account(c, headers, "Klant eigen account", cf_account_id="acct-2")
        # Zones are synced before the domain record exists — the ordinary sequence when an
        # agency connects the Cloudflare accounts first and enters domains afterwards.
        for account in (first, second):
            await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/sync", headers=headers)
        domain = await _domain(c, headers, "klant.nl", company)

        ambiguous = await c.post(
            f"/api/v1/cloudflare/domains/{domain['id']}/connect", json={}, headers=headers
        )
        assert ambiguous.status_code == 409
        assert ambiguous.json()["error"]["code"] == "cloudflare_zone_ambiguous"

        # The status report names both candidates so the admin can choose.
        status = (
            await c.get(f"/api/v1/cloudflare/domains/{domain['id']}/status", headers=headers)
        ).json()
        assert {c_["account_name"] for c_ in status["candidates"]} == {
            "Agency",
            "Klant eigen account",
        }
        assert "duplicate_zone" in status["issues"]

        # Naming the account resolves it.
        chosen = await c.post(
            f"/api/v1/cloudflare/domains/{domain['id']}/connect",
            json={"account_id": first["id"]},
            headers=headers,
        )
        assert chosen.status_code == 200, chosen.text
        assert chosen.json()["account_name"] == "Agency"


async def test_sync_never_matches_a_second_zone_onto_a_claimed_domain(
    client_for, cloudflare
) -> None:
    """Two accounts holding the same apex must not both link to one domain: "where does this
    domain live" would then have two answers and nothing downstream could pick. The duplicate
    stays unlinked and is reported."""
    t = await make_tenant("cf-claimed")
    headers = await auth_cookie(t.user)
    cloudflare.add_zone("klant.nl", account="acct-1")
    cloudflare.add_zone("klant.nl", account="acct-2")
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        domain = await _domain(c, headers, "klant.nl", company)
        first = await _account(c, headers, "Agency", cf_account_id="acct-1")
        second = await _account(c, headers, "Klant", cf_account_id="acct-2")
        for account in (first, second):
            await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/sync", headers=headers)

        zones = (await c.get("/api/v1/cloudflare/zones", headers=headers)).json()["items"]
        assert len(zones) == 2
        assert len([z for z in zones if z["domain_id"] == domain["id"]]) == 1

        status = (
            await c.get(f"/api/v1/cloudflare/domains/{domain['id']}/status", headers=headers)
        ).json()
        assert "duplicate_zone" in status["issues"]
        assert len(status["candidates"]) == 2


async def test_a_zone_scoped_token_cannot_create_and_says_so(client_for, cloudflare) -> None:
    cloudflare.deny.add("/accounts")
    t = await make_tenant("cf-nocreate")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        account = await _account(c, headers)
        await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/verify", headers=headers)
        domain = await _domain(c, headers, "nieuw.nl", company)
        refused = await c.post(
            f"/api/v1/cloudflare/domains/{domain['id']}/connect", json={}, headers=headers
        )
        assert refused.status_code == 409
        assert refused.json()["error"]["code"] == "cloudflare_cannot_create_zone"


# --------------------------------------------------------------------------------------- #
# Redirects
# --------------------------------------------------------------------------------------- #
async def test_setting_a_redirect_appends_and_never_wipes_the_tenants_rules(
    client_for, cloudflare
) -> None:
    """A blind PUT of the entrypoint ruleset would delete redirect rules the tenant wrote by
    hand. The rule is appended, and the tenant's rule is reported as a conflict instead."""
    t = await make_tenant("cf-redir")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, domain, zone = await _connected(c, headers, cloudflare)
        zone_id = next(iter(cloudflare.zones))
        theirs = cloudflare.add_redirect_rule(
            zone_id,
            {
                "action": "redirect",
                "description": "eigen regel van de klant",
                "expression": '(http.request.uri.path eq "/oud")',
            },
        )

        saved = await c.put(
            f"/api/v1/cloudflare/domains/{domain['id']}/redirect",
            json={"target_url": "https://nieuw.nl", "status_code": 301},
            headers=headers,
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["last_status"] == "active"

        rules_now = cloudflare.rulesets[zone_id]["rules"]
        assert [r["id"] for r in rules_now][0] == theirs["id"]  # theirs still first, still there
        assert len(rules_now) == 2

        # The check names their rule as a conflict rather than resolving it: Cloudflare
        # evaluates top-down and we cannot evaluate their expression.
        checked = (
            await c.post(f"/api/v1/cloudflare/domains/{domain['id']}/check", headers=headers)
        ).json()
        assert "redirect_conflict" in checked["issues"]
        assert [c_["description"] for c_ in checked["conflicts"]] == ["eigen regel van de klant"]


async def test_a_redirect_sets_the_domains_own_status(client_for, cloudflare) -> None:
    """Setting a domain-wide redirect *is* the domain redirecting. Leaving ``Domain.status``
    on "active" would put two screens in disagreement about the same fact."""
    t = await make_tenant("cf-status")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, domain, _ = await _connected(c, headers, cloudflare)
        await c.put(
            f"/api/v1/cloudflare/domains/{domain['id']}/redirect",
            json={"target_url": "https://nieuw.nl"},
            headers=headers,
        )
        after = (await c.get(f"/api/v1/domains/{domain['id']}", headers=headers)).json()
        assert after["status"] == "redirect"
        assert after["redirect_url"] == "https://nieuw.nl"

        assert (
            await c.delete(
                f"/api/v1/cloudflare/domains/{domain['id']}/redirect", headers=headers
            )
        ).status_code == 204
        back = (await c.get(f"/api/v1/domains/{domain['id']}", headers=headers)).json()
        assert back["status"] == "active" and back["redirect_url"] is None

        # And the whole thing is on the domain's trail (§16).
        trail = await c.get(
            f"/api/v1/activity?entity_type=domain&entity_id={domain['id']}", headers=headers
        )
        actions = {item["action"] for item in trail.json()}
        assert "cloudflare.redirect_set" in actions
        assert "cloudflare.redirect_removed" in actions


async def test_a_redirect_pointing_at_itself_is_refused(client_for, cloudflare) -> None:
    t = await make_tenant("cf-loop")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, domain, _ = await _connected(c, headers, cloudflare)
        looped = await c.put(
            f"/api/v1/cloudflare/domains/{domain['id']}/redirect",
            json={"target_url": "https://www.klant.nl"},
            headers=headers,
        )
        assert looped.status_code == 422
        assert looped.json()["error"]["code"] == "cloudflare_redirect_loop"

        # With subdomains excluded the same target is fine — www is then outside the match set.
        fine = await c.put(
            f"/api/v1/cloudflare/domains/{domain['id']}/redirect",
            json={"target_url": "https://www.klant.nl", "include_subdomains": False},
            headers=headers,
        )
        assert fine.status_code == 200, fine.text

        relative = await c.put(
            f"/api/v1/cloudflare/domains/{domain['id']}/redirect",
            json={"target_url": "nieuw.nl"},
            headers=headers,
        )
        assert relative.status_code == 422


async def test_check_reports_drift_and_a_deleted_rule(client_for, cloudflare) -> None:
    t = await make_tenant("cf-drift")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, domain, _ = await _connected(c, headers, cloudflare)
        await c.put(
            f"/api/v1/cloudflare/domains/{domain['id']}/redirect",
            json={"target_url": "https://nieuw.nl", "status_code": 301},
            headers=headers,
        )
        zone_id = next(iter(cloudflare.zones))
        ours = cloudflare.rulesets[zone_id]["rules"][0]

        # Somebody edits it in the Cloudflare dashboard.
        ours["action_parameters"]["from_value"]["status_code"] = 302
        checked = (
            await c.post(f"/api/v1/cloudflare/domains/{domain['id']}/check", headers=headers)
        ).json()
        assert "redirect_drift" in checked["issues"]
        assert checked["redirect_live"]["differences"] == ["status_code"]
        assert checked["redirect"]["last_status"] == "drift"

        # …and then deletes it outright.
        cloudflare.rulesets[zone_id]["rules"].clear()
        gone = (
            await c.post(f"/api/v1/cloudflare/domains/{domain['id']}/check", headers=headers)
        ).json()
        assert "redirect_missing" in gone["issues"]
        assert gone["redirect_live"]["present"] is False

        # Saving again restores it, rather than needing a delete-and-recreate dance.
        again = await c.put(
            f"/api/v1/cloudflare/domains/{domain['id']}/redirect",
            json={"target_url": "https://nieuw.nl", "status_code": 301},
            headers=headers,
        )
        assert again.status_code == 200, again.text
        assert len(cloudflare.rulesets[zone_id]["rules"]) == 1


async def test_check_reports_a_domain_that_already_redirects_outside_schakl(
    client_for, cloudflare
) -> None:
    """The case #278 asks about: the zone already redirects, but not through us. A forwarding
    Page Rule is invisible from the Redirect Rules screen, and the domain record claiming
    ``redirect`` with nothing behind it is how #96's external flow shows up."""
    t = await make_tenant("cf-already")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, domain, _ = await _connected(c, headers, cloudflare)
        zone_id = next(iter(cloudflare.zones))
        cloudflare.pagerules[zone_id] = [
            {
                "id": "pr-1",
                "status": "active",
                "targets": [
                    {"target": "url", "constraint": {"operator": "matches", "value": "klant.nl/*"}}
                ],
                "actions": [
                    {
                        "id": "forwarding_url",
                        "value": {"url": "https://elders.nl", "status_code": 301},
                    }
                ],
            }
        ]
        await c.patch(
            f"/api/v1/domains/{domain['id']}",
            json={"status": "redirect", "redirect_url": "https://elders.nl"},
            headers=headers,
        )

        checked = (
            await c.post(f"/api/v1/cloudflare/domains/{domain['id']}/check", headers=headers)
        ).json()
        assert "redirect_conflict" in checked["issues"]
        assert "domain_says_redirect" in checked["issues"]
        assert checked["conflicts"][0]["kind"] == "page_rule"
        assert checked["conflicts"][0]["description"] == "klant.nl/*"


async def test_a_rule_with_no_proxied_record_is_reported_and_repaired(
    client_for, cloudflare
) -> None:
    """A Redirect Rule only acts on traffic that reaches Cloudflare. Without a proxied record
    the rule saves, looks active, and does nothing — the module's worst failure mode, so it is
    both prevented on write and named on check."""
    t = await make_tenant("cf-origin")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, domain, _ = await _connected(c, headers, cloudflare)
        zone_id = next(iter(cloudflare.zones))

        await c.put(
            f"/api/v1/cloudflare/domains/{domain['id']}/redirect",
            json={"target_url": "https://nieuw.nl", "ensure_origin": True},
            headers=headers,
        )
        placeholders = {
            r["name"]: r for r in cloudflare.dns[zone_id] if r["type"] == "AAAA"
        }
        assert set(placeholders) == {"klant.nl", "www.klant.nl"}
        assert all(r["proxied"] and r["content"] == "100::" for r in placeholders.values())

        checked = (
            await c.post(f"/api/v1/cloudflare/domains/{domain['id']}/check", headers=headers)
        ).json()
        assert checked["origin"]["apex_proxied"] is True
        assert "origin_missing" not in checked["issues"]

        # Somebody greys the cloud: the rule is now inert, and the report says exactly that.
        for record in cloudflare.dns[zone_id]:
            record["proxied"] = False
        greyed = (
            await c.post(f"/api/v1/cloudflare/domains/{domain['id']}/check", headers=headers)
        ).json()
        assert "origin_missing" in greyed["issues"]


async def test_a_greyed_www_is_reported_even_when_the_apex_is_fine(client_for, cloudflare) -> None:
    """The apex answering is not the whole answer.

    `www` fails on its own: the rule matches it, nothing proxied serves it, and every other
    signal on the page reads healthy — which is the state a domain redirect exists to avoid.
    Its own key, because `origin_missing`'s "no traffic reaches the redirect" is false here.
    """
    t = await make_tenant("cf-origin-www")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, domain, _ = await _connected(c, headers, cloudflare)
        zone_id = next(iter(cloudflare.zones))
        await c.put(
            f"/api/v1/cloudflare/domains/{domain['id']}/redirect",
            json={"target_url": "https://nieuw.nl", "ensure_origin": True},
            headers=headers,
        )
        for record in cloudflare.dns[zone_id]:
            if record["name"] == "www.klant.nl":
                record["proxied"] = False

        checked = (
            await c.post(f"/api/v1/cloudflare/domains/{domain['id']}/check", headers=headers)
        ).json()
        assert checked["origin"]["apex_proxied"] is True
        assert checked["origin"]["www_proxied"] is False
        assert "origin_www_missing" in checked["issues"]
        assert "origin_missing" not in checked["issues"]


async def test_www_is_not_expected_when_the_rule_excludes_subdomains(
    client_for, cloudflare
) -> None:
    """With `include_subdomains` off the rule never matches `www`, so an unproxied one is the
    configured state rather than a finding."""
    t = await make_tenant("cf-origin-apex-only")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, domain, _ = await _connected(c, headers, cloudflare)
        zone_id = next(iter(cloudflare.zones))
        await c.put(
            f"/api/v1/cloudflare/domains/{domain['id']}/redirect",
            json={
                "target_url": "https://nieuw.nl",
                "include_subdomains": False,
                "ensure_origin": True,
            },
            headers=headers,
        )
        assert {r["name"] for r in cloudflare.dns[zone_id] if r["type"] == "AAAA"} == {"klant.nl"}

        checked = (
            await c.post(f"/api/v1/cloudflare/domains/{domain['id']}/check", headers=headers)
        ).json()
        assert checked["origin"]["www_proxied"] is False
        assert "origin_www_missing" not in checked["issues"]
        assert "origin_missing" not in checked["issues"]


async def test_ensure_origin_leaves_an_existing_record_alone(client_for, cloudflare) -> None:
    """Never replace what is already answering on that hostname."""
    t = await make_tenant("cf-origin-keep")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, domain, _ = await _connected(c, headers, cloudflare)
        zone_id = next(iter(cloudflare.zones))
        cloudflare.add_record(
            zone_id, type="A", name="klant.nl", content="203.0.113.10", proxied=True
        )
        await c.put(
            f"/api/v1/cloudflare/domains/{domain['id']}/redirect",
            json={"target_url": "https://nieuw.nl", "include_subdomains": False},
            headers=headers,
        )
        apex = [r for r in cloudflare.dns[zone_id] if r["name"] == "klant.nl"]
        assert len(apex) == 1 and apex[0]["content"] == "203.0.113.10"


async def test_removing_a_redirect_only_deletes_our_own_rule(client_for, cloudflare) -> None:
    t = await make_tenant("cf-remove")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, domain, _ = await _connected(c, headers, cloudflare)
        zone_id = next(iter(cloudflare.zones))
        theirs = cloudflare.add_redirect_rule(
            zone_id, {"action": "redirect", "description": "van de klant", "expression": "true"}
        )
        await c.put(
            f"/api/v1/cloudflare/domains/{domain['id']}/redirect",
            json={"target_url": "https://nieuw.nl"},
            headers=headers,
        )
        assert (
            await c.delete(
                f"/api/v1/cloudflare/domains/{domain['id']}/redirect", headers=headers
            )
        ).status_code == 204
        remaining = [r["id"] for r in cloudflare.rulesets[zone_id]["rules"]]
        assert remaining == [theirs["id"]]


def test_the_rule_expression_escapes_and_scopes_correctly() -> None:
    """Pure-logic assertions on the one string that decides which requests are caught. A
    ``contains`` would match ``nietklant.nl``; an unescaped quote would break the save."""
    assert rules.host_expression("klant.nl", include_subdomains=False) == (
        '(http.host eq "klant.nl")'
    )
    assert rules.host_expression("klant.nl", include_subdomains=True) == (
        '(http.host eq "klant.nl" or ends_with(http.host, ".klant.nl"))'
    )
    rule = rules.build_rule(
        apex="klant.nl",
        target_url='https://nieuw.nl/pad"met-quote/',
        status_code=308,
        preserve_path=True,
        preserve_query=False,
        include_subdomains=True,
    )
    target = rule["action_parameters"]["from_value"]["target_url"]["expression"]
    assert target == 'concat("https://nieuw.nl/pad\\"met-quote", http.request.uri.path)'
    assert rule["action_parameters"]["from_value"]["preserve_query_string"] is False
    # A trailing slash on the target would otherwise produce "//pad".
    assert "//pad" not in target.replace("https://", "")


# --------------------------------------------------------------------------------------- #
# DNS
# --------------------------------------------------------------------------------------- #
async def test_dns_read_write_and_export(client_for, cloudflare) -> None:
    t = await make_tenant("cf-dns")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, domain, zone = await _connected(c, headers, cloudflare)
        created = await c.post(
            f"/api/v1/cloudflare/zones/{zone['id']}/dns",
            json={"type": "A", "name": "klant.nl", "content": "203.0.113.4", "proxied": True},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        record_id = created.json()["id"]

        # Cloudflare refusing a duplicate gets its own message, not a generic 502.
        duplicate = await c.post(
            f"/api/v1/cloudflare/zones/{zone['id']}/dns",
            json={"type": "A", "name": "klant.nl", "content": "203.0.113.5"},
            headers=headers,
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "cloudflare_record_exists"

        listed = await c.get(f"/api/v1/cloudflare/zones/{zone['id']}/dns", headers=headers)
        assert listed.status_code == 200, listed.text
        assert listed.json()["records"][0]["content"] == "203.0.113.4"

        csv_export = await c.get(
            f"/api/v1/cloudflare/zones/{zone['id']}/dns/export?format=csv", headers=headers
        )
        assert csv_export.status_code == 200, csv_export.text
        assert csv_export.json()["filename"] == "klant.nl-dns.csv"
        assert "203.0.113.4" in csv_export.json()["content"]

        bind = await c.get(
            f"/api/v1/cloudflare/zones/{zone['id']}/dns/export?format=bind", headers=headers
        )
        assert bind.json()["filename"] == "klant.nl.zone"
        assert "IN A 203.0.113.4" in bind.json()["content"]

        assert (
            await c.delete(
                f"/api/v1/cloudflare/zones/{zone['id']}/dns/{record_id}", headers=headers
            )
        ).status_code == 204
        assert cloudflare.dns[next(iter(cloudflare.zones))] == []


# --------------------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------------------- #
async def test_pages_link_registers_the_hostname_and_points_dns(client_for, cloudflare) -> None:
    t = await make_tenant("cf-pages")
    headers = await auth_cookie(t.user)
    cloudflare.pages["acct-1"] = [
        {"name": "klant-site", "subdomain": "klant-site.pages.dev", "production_branch": "main"}
    ]
    async with client_for(t.host) as c:
        account, domain, _ = await _connected(c, headers, cloudflare)
        await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/sync", headers=headers)
        projects = (await c.get("/api/v1/cloudflare/pages/projects", headers=headers)).json()
        assert [p["name"] for p in projects] == ["klant-site"]

        linked = await c.post(
            f"/api/v1/cloudflare/domains/{domain['id']}/pages",
            json={"project_id": projects[0]["id"], "hostname": "www.klant.nl"},
            headers=headers,
        )
        assert linked.status_code == 201, linked.text
        assert linked.json()["hostname"] == "www.klant.nl"
        assert cloudflare.pages_domains["acct-1"]["klant-site"][0]["name"] == "www.klant.nl"
        zone_id = next(iter(cloudflare.zones))
        cname = [r for r in cloudflare.dns[zone_id] if r["type"] == "CNAME"]
        assert cname and cname[0]["content"] == "klant-site.pages.dev"

        # A hostname that is not part of this domain would file the link under the wrong client.
        wrong = await c.post(
            f"/api/v1/cloudflare/domains/{domain['id']}/pages",
            json={"project_id": projects[0]["id"], "hostname": "www.andereklant.nl"},
            headers=headers,
        )
        assert wrong.status_code == 422

        link_id = linked.json()["id"]
        assert (
            await c.delete(f"/api/v1/cloudflare/pages/links/{link_id}", headers=headers)
        ).status_code == 204
        assert cloudflare.pages_domains["acct-1"]["klant-site"] == []
        # The DNS record is deliberately left alone: it may since have been repointed.
        assert [r for r in cloudflare.dns[zone_id] if r["type"] == "CNAME"]


async def test_pages_links_a_domain_that_has_no_zone_here(client_for, cloudflare) -> None:
    """A Pages hostname hangs off the *project's* account, not off this domain's zone.

    The panel used to draw the whole Pages surface inside the connected branch, which read as
    "this domain cannot be served from Pages" for every client whose DNS lives elsewhere. The
    API never had that limit: the hostname is registered and only the CNAME step is skipped —
    Cloudflare keeps the hostname pending until the DNS provider points it.
    """
    t = await make_tenant("cf-pages-nozone")
    headers = await auth_cookie(t.user)
    cloudflare.pages["acct-1"] = [
        {"name": "klant-site", "subdomain": "klant-site.pages.dev", "production_branch": "main"}
    ]
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        account = await _account(c, headers)
        domain = await _domain(c, headers, "elders.nl", company)
        await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/verify", headers=headers)
        await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/sync", headers=headers)
        projects = (await c.get("/api/v1/cloudflare/pages/projects", headers=headers)).json()
        # What the picker labels a project by once a tenant holds more than one account.
        assert projects[0]["account_name"] == "Agency"

        linked = await c.post(
            f"/api/v1/cloudflare/domains/{domain['id']}/pages",
            json={"project_id": projects[0]["id"]},  # no hostname: the domain itself
            headers=headers,
        )
        assert linked.status_code == 201, linked.text
        assert linked.json()["hostname"] == "elders.nl"
        assert cloudflare.pages_domains["acct-1"]["klant-site"][0]["name"] == "elders.nl"

        # And the panel can still find it: the status read is stored rows, zone or no zone.
        status = (
            await c.get(f"/api/v1/cloudflare/domains/{domain['id']}/status", headers=headers)
        ).json()
        assert status["zone"] is None
        assert [link["hostname"] for link in status["pages_links"]] == ["elders.nl"]


async def test_the_status_says_how_old_the_answer_it_is_giving_is(client_for, cloudflare) -> None:
    """The panel draws stored rows and no Cloudflare call, which is what makes a domain page
    fast and keeps it working while Cloudflare is not (docs/PERFORMANCE.md). The cost of that
    is that "geen conflicten" from a check that ran in March and one that ran a minute ago are
    the same sentence, and the panel had no date to tell them apart — the two message keys for
    it were written and never wired to anything.

    Taken from the rows the report is *built* from, so it is the age of the answer rather than
    the time the page was drawn, and a stored read repeats it instead of resetting it.
    """
    t = await make_tenant("cf-checked-at")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        # Nothing observed: no zone, no links, nothing ever asked. The panel says "never".
        alone = await _domain(c, headers, "nooitgekeken.nl", company)
        never = (
            await c.get(f"/api/v1/cloudflare/domains/{alone['id']}/status", headers=headers)
        ).json()
        assert never["checked_at"] is None

        _, domain, _ = await _connected(c, headers, cloudflare)
        checked = (
            await c.post(f"/api/v1/cloudflare/domains/{domain['id']}/check", headers=headers)
        ).json()
        assert checked["checked_at"] is not None

        stored = (
            await c.get(f"/api/v1/cloudflare/domains/{domain['id']}/status", headers=headers)
        ).json()
        assert stored["live"] is False
        assert stored["checked_at"] == checked["checked_at"]


async def test_a_check_that_could_read_nothing_does_not_claim_to_be_fresh(
    client_for, cloudflare
) -> None:
    """Every probe fails softly and separately, so a check can come back having observed
    nothing at all. Stamping "gecontroleerd zojuist" on that report would be the one thing it
    does not know — hence the timestamp comes off the rows a probe actually wrote, never off
    the clock at the end of the request.
    """
    t = await make_tenant("cf-checked-blind")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, domain, _ = await _connected(c, headers, cloudflare)
        before = (
            await c.get(f"/api/v1/cloudflare/domains/{domain['id']}/status", headers=headers)
        ).json()["checked_at"]

        # The token stops answering for everything the report is assembled from.
        cloudflare.deny.add("/zones")
        blind = (
            await c.post(f"/api/v1/cloudflare/domains/{domain['id']}/check", headers=headers)
        ).json()
        assert "zone" in blind["unavailable"]
        assert blind["checked_at"] == before


async def test_sync_adopts_hostnames_already_attached_at_cloudflare(
    client_for, cloudflare
) -> None:
    """The case an agency actually arrives in: the domain is already on a placeholder project.

    Somebody parked it in Cloudflare's own dashboard long before schakl saw the account, so
    there is nothing to press "Aan project koppelen" for — the link has to come *back* from
    Cloudflare or the CRM never learns about it.
    """
    t = await make_tenant("cf-pages-adopt")
    headers = await auth_cookie(t.user)
    cloudflare.pages["acct-1"] = [
        {"name": "placeholder", "subdomain": "placeholder.pages.dev", "production_branch": "main"}
    ]
    cloudflare.add_pages_domain("placeholder", "klant.nl")
    cloudflare.add_pages_domain("placeholder", "www.klant.nl", status="pending")
    # A hostname belonging to no domain record here. Counted, never invented into one.
    cloudflare.add_pages_domain("placeholder", "iemandanders.nl")
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        domain = await _domain(c, headers, "klant.nl", company)
        account = await _account(c, headers)
        await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/verify", headers=headers)

        result = await c.post(
            f"/api/v1/cloudflare/accounts/{account['id']}/sync", headers=headers
        )
        assert result.status_code == 200, result.text
        assert result.json()["pages_domains_synced"] == 3
        assert result.json()["pages_links_adopted"] == 2
        assert result.json()["pages_links_matched"] == 2

        status = (
            await c.get(f"/api/v1/cloudflare/domains/{domain['id']}/status", headers=headers)
        ).json()
        assert sorted(link["hostname"] for link in status["pages_links"]) == [
            "klant.nl",
            "www.klant.nl",
        ]
        assert all(link["discovered_at"] for link in status["pages_links"])

        # Adoption records what is already true; it registers nothing and writes no DNS.
        assert not [call for call in cloudflare.calls if call[0] in {"POST", "PUT", "DELETE"}]
        # And it is idempotent — a second sync adopts nothing and duplicates no row.
        again = await c.post(
            f"/api/v1/cloudflare/accounts/{account['id']}/sync", headers=headers
        )
        assert again.json()["pages_links_adopted"] == 0
        assert again.json()["pages_links_matched"] == 2
        status = (
            await c.get(f"/api/v1/cloudflare/domains/{domain['id']}/status", headers=headers)
        ).json()
        assert len(status["pages_links"]) == 2


async def test_sync_files_a_hostname_under_the_most_specific_domain(
    client_for, cloudflare
) -> None:
    """A tenant holding both ``klant.nl`` and ``shop.klant.nl`` must not get the link filed
    under the parent — that would put a hostname on the wrong client's page."""
    t = await make_tenant("cf-pages-specific")
    headers = await auth_cookie(t.user)
    cloudflare.pages["acct-1"] = [{"name": "webshop", "subdomain": "webshop.pages.dev"}]
    cloudflare.add_pages_domain("webshop", "www.shop.klant.nl")
    async with client_for(t.host) as c:
        parent_co = await _company(c, headers)
        child_co = await _company(c, headers, "Webshop BV")
        await _domain(c, headers, "klant.nl", parent_co)
        child = await _domain(c, headers, "shop.klant.nl", child_co)
        account = await _account(c, headers)
        await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/verify", headers=headers)
        await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/sync", headers=headers)

        status = (
            await c.get(f"/api/v1/cloudflare/domains/{child['id']}/status", headers=headers)
        ).json()
        assert [link["hostname"] for link in status["pages_links"]] == ["www.shop.klant.nl"]


async def test_sync_falls_back_when_the_project_payload_omits_its_domains(
    client_for, cloudflare
) -> None:
    """The embedded ``domains`` array is what makes discovery free. A payload without it must
    still discover, one call per project — the shape is not documented as stable."""
    t = await make_tenant("cf-pages-fallback")
    headers = await auth_cookie(t.user)
    cloudflare.pages_projects_omit_domains = True
    cloudflare.pages["acct-1"] = [{"name": "placeholder", "subdomain": "placeholder.pages.dev"}]
    cloudflare.add_pages_domain("placeholder", "klant.nl")
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        domain = await _domain(c, headers, "klant.nl", company)
        account = await _account(c, headers)
        await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/verify", headers=headers)
        result = await c.post(
            f"/api/v1/cloudflare/accounts/{account['id']}/sync", headers=headers
        )
        assert result.json()["pages_links_adopted"] == 1
        assert ("GET", "/accounts/acct-1/pages/projects/placeholder/domains") in cloudflare.calls

        status = (
            await c.get(f"/api/v1/cloudflare/domains/{domain['id']}/status", headers=headers)
        ).json()
        assert [link["hostname"] for link in status["pages_links"]] == ["klant.nl"]


async def test_check_refreshes_a_link_and_reports_it_gone_without_deleting_it(
    client_for, cloudflare
) -> None:
    """``status`` used to be frozen at whatever Cloudflare said the second the link was made.

    So a hostname that finished provisioning read *pending* forever and one deleted in
    Cloudflare's dashboard read as linked. Both are observations, and both are *reported*: the
    row survives being called missing, exactly as a drifted redirect rule does.
    """
    t = await make_tenant("cf-pages-check")
    headers = await auth_cookie(t.user)
    cloudflare.pages["acct-1"] = [{"name": "klant-site", "subdomain": "klant-site.pages.dev"}]
    async with client_for(t.host) as c:
        account, domain, _ = await _connected(c, headers, cloudflare)
        await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/sync", headers=headers)
        projects = (await c.get("/api/v1/cloudflare/pages/projects", headers=headers)).json()
        linked = await c.post(
            f"/api/v1/cloudflare/domains/{domain['id']}/pages",
            json={"project_id": projects[0]["id"]},
            headers=headers,
        )
        assert linked.json()["status"] == "pending"

        # Cloudflare finishes provisioning, and somebody adds ``www`` in its own dashboard.
        hosts = cloudflare.pages_domains["acct-1"]["klant-site"]
        hosts[0]["status"] = "active"
        cloudflare.add_pages_domain("klant-site", "www.klant.nl", status="pending")

        report = await c.post(
            f"/api/v1/cloudflare/domains/{domain['id']}/check", headers=headers
        )
        assert report.status_code == 200, report.text
        links = {link["hostname"]: link for link in report.json()["pages_links"]}
        assert links["klant.nl"]["status"] == "active"
        assert links["klant.nl"]["last_checked_at"]
        # The sibling hostname is adopted: it is this domain's, and the project already ours.
        assert links["www.klant.nl"]["status"] == "pending"
        assert "pages_pending" in report.json()["issues"]

        # Now it disappears from Cloudflare entirely.
        cloudflare.pages_domains["acct-1"]["klant-site"] = []
        report = await c.post(
            f"/api/v1/cloudflare/domains/{domain['id']}/check", headers=headers
        )
        links = {link["hostname"]: link for link in report.json()["pages_links"]}
        assert len(links) == 2, "a missing link is reported, never deleted"
        assert all(link["missing_at"] for link in links.values())
        assert "pages_missing" in report.json()["issues"]
        first_seen = links["klant.nl"]["missing_at"]

        # "Since when" survives a second check — restamping it would answer "just now" forever.
        report = await c.post(
            f"/api/v1/cloudflare/domains/{domain['id']}/check", headers=headers
        )
        links = {link["hostname"]: link for link in report.json()["pages_links"]}
        assert links["klant.nl"]["missing_at"] == first_seen

        # Re-linking is how the drift is resolved, so the flag clears.
        relinked = await c.post(
            f"/api/v1/cloudflare/domains/{domain['id']}/pages",
            json={"project_id": projects[0]["id"]},
            headers=headers,
        )
        assert relinked.json()["missing_at"] is None


async def test_check_never_adopts_a_hostname_that_belongs_to_another_domain(
    client_for, cloudflare
) -> None:
    """A suffix of this domain's name is not proof the hostname is *this domain's*.

    One project can serve both clients. Filing ``www.shop.klant.nl`` under ``klant.nl`` because
    it ends in it would put a hostname on the wrong client's page — the mistake the sync's
    longest-suffix match exists to prevent, so the check must resolve it the same way.
    """
    t = await make_tenant("cf-pages-sibling")
    headers = await auth_cookie(t.user)
    cloudflare.pages["acct-1"] = [{"name": "klant-site", "subdomain": "klant-site.pages.dev"}]
    async with client_for(t.host) as c:
        account, domain, _ = await _connected(c, headers, cloudflare)
        shop_co = await _company(c, headers, "Webshop BV")
        shop = await _domain(c, headers, "shop.klant.nl", shop_co)
        await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/sync", headers=headers)
        projects = (await c.get("/api/v1/cloudflare/pages/projects", headers=headers)).json()
        await c.post(
            f"/api/v1/cloudflare/domains/{domain['id']}/pages",
            json={"project_id": projects[0]["id"]},
            headers=headers,
        )
        cloudflare.add_pages_domain("klant-site", "www.shop.klant.nl")

        report = await c.post(
            f"/api/v1/cloudflare/domains/{domain['id']}/check", headers=headers
        )
        assert [link["hostname"] for link in report.json()["pages_links"]] == ["klant.nl"]

        # Left for the sync, which files it under the record it actually belongs to.
        await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/sync", headers=headers)
        status = (
            await c.get(f"/api/v1/cloudflare/domains/{shop['id']}/status", headers=headers)
        ).json()
        assert [link["hostname"] for link in status["pages_links"]] == ["www.shop.klant.nl"]


async def test_check_leaves_links_alone_when_pages_cannot_be_read(client_for, cloudflare) -> None:
    """"We did not look" and "it is gone" are different answers, and a token scoped away from
    Pages must never produce the second one."""
    t = await make_tenant("cf-pages-denied")
    headers = await auth_cookie(t.user)
    cloudflare.pages["acct-1"] = [{"name": "klant-site", "subdomain": "klant-site.pages.dev"}]
    async with client_for(t.host) as c:
        account, domain, _ = await _connected(c, headers, cloudflare)
        await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/sync", headers=headers)
        projects = (await c.get("/api/v1/cloudflare/pages/projects", headers=headers)).json()
        await c.post(
            f"/api/v1/cloudflare/domains/{domain['id']}/pages",
            json={"project_id": projects[0]["id"]},
            headers=headers,
        )

        cloudflare.deny.add("/pages/")
        report = await c.post(
            f"/api/v1/cloudflare/domains/{domain['id']}/check", headers=headers
        )
        assert report.status_code == 200, report.text
        assert "pages" in report.json()["unavailable"]
        links = report.json()["pages_links"]
        assert [link["hostname"] for link in links] == ["klant.nl"]
        assert links[0]["missing_at"] is None


async def test_pages_refresh_is_one_call_per_project(client_for, cloudflare) -> None:
    """Three hostnames of one domain on one project is one Cloudflare call, not three
    (docs/PERFORMANCE.md — invisible in the JSON, fatal at scale)."""
    t = await make_tenant("cf-pages-batch")
    headers = await auth_cookie(t.user)
    cloudflare.pages["acct-1"] = [{"name": "klant-site", "subdomain": "klant-site.pages.dev"}]
    for host in ("klant.nl", "www.klant.nl", "acc.klant.nl"):
        cloudflare.add_pages_domain("klant-site", host)
    async with client_for(t.host) as c:
        account, domain, _ = await _connected(c, headers, cloudflare)
        await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/sync", headers=headers)

        cloudflare.calls.clear()
        report = await c.post(
            f"/api/v1/cloudflare/domains/{domain['id']}/check", headers=headers
        )
        assert len(report.json()["pages_links"]) == 3
        domain_calls = [call for call in cloudflare.calls if call[1].endswith("/domains")]
        assert len(domain_calls) == 1, domain_calls


# --------------------------------------------------------------------------------------- #
# Authorization + horizon
# --------------------------------------------------------------------------------------- #
async def test_a_plain_member_holds_none_of_it(client_for, cloudflare) -> None:
    """All three keys are admin-only by default (#278 §4) — widening
    ``domains.domain.write`` must never hand out live DNS."""
    t = await make_tenant("cf-member")
    member = await make_tenant("cf-member-m", email="cf-member-user@example.com")
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
            ("get", "/api/v1/cloudflare/accounts"),
            ("get", "/api/v1/cloudflare/zones"),
            ("get", f"/api/v1/cloudflare/domains/{domain['id']}/status"),
        ):
            res = await getattr(c, method)(path, headers=member_headers)
            assert res.status_code == 403, f"{path}: {res.status_code}"
        assert (
            await c.post(
                f"/api/v1/cloudflare/domains/{domain['id']}/connect",
                json={},
                headers=member_headers,
            )
        ).status_code == 403


async def test_the_company_horizon_reaches_zones_and_the_status_report(
    client_for, cloudflare
) -> None:
    """None of these tables carries ``company_id``: a zone belongs to its *domain's* client
    (#285 failure mode 1). Without the horizon clause the repository would filter nothing at
    all, and a scoped membership would read every client's DNS."""
    t = await make_tenant("cf-horizon")
    member = await make_tenant("cf-horizon-m", email="cf-horizon-member@example.com")
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        membership = await add_membership(session, t.org.id, member.user.id, role="admin")
        membership_id = membership.id
        await session.commit()
    owner_headers = await auth_cookie(t.user)
    # ``member`` was conjured with its own tenant, so it holds two memberships; the session
    # under test is the one in ``t`` (a session names its org — CLAUDE.md §5).
    member_headers = await auth_cookie(member.user, org_id=t.org.id)

    cloudflare.add_zone("alpha.nl")
    cloudflare.add_zone("beta.nl")
    async with client_for(t.host) as c:
        alpha = await _company(c, owner_headers, "Alpha")
        beta = await _company(c, owner_headers, "Beta")
        await _domain(c, owner_headers, "alpha.nl", alpha)
        beta_domain = await _domain(c, owner_headers, "beta.nl", beta)
        account = await _account(c, owner_headers)
        await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/verify", headers=owner_headers)
        await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/sync", headers=owner_headers)

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

        # Grant the scoped admin the cloudflare read keys, so a leak cannot hide behind a 403.
        roles = (await c.get("/api/v1/roles", headers=owner_headers)).json()
        admin_role = next(r for r in roles if r["key"] == "admin")
        await c.patch(
            f"/api/v1/roles/{admin_role['id']}",
            json={
                "permissions": sorted(
                    set(admin_role["permissions"])
                    | {"cloudflare.dns.read", "cloudflare.zone.manage"}
                )
            },
            headers=owner_headers,
        )

        zones = await c.get("/api/v1/cloudflare/zones", headers=member_headers)
        assert zones.status_code == 200, zones.text
        assert [z["name"] for z in zones.json()["items"]] == ["alpha.nl"]
        # The total must count exactly what the list could return (#285 failure mode 2).
        assert zones.json()["total"] == 1

        # Beta's status report answers 404 — never 403, which would leak that it exists (§15).
        blocked = await c.get(
            f"/api/v1/cloudflare/domains/{beta_domain['id']}/status", headers=member_headers
        )
        assert blocked.status_code == 404

        # The owner still sees both.
        assert (await c.get("/api/v1/cloudflare/zones", headers=owner_headers)).json()["total"] == 2


async def test_zones_are_tenant_isolated(client_for, cloudflare) -> None:
    a = await make_tenant("cf-ziso-a")
    b = await make_tenant("cf-ziso-b")
    a_headers, b_headers = await auth_cookie(a.user), await auth_cookie(b.user)
    cloudflare.add_zone("klant.nl")
    async with client_for(a.host) as ca:
        _, _, zone = await _connected(ca, a_headers, cloudflare)
    async with client_for(b.host) as cb:
        assert (await cb.get("/api/v1/cloudflare/zones", headers=b_headers)).json()["total"] == 0
        assert (
            await cb.get(f"/api/v1/cloudflare/zones/{zone['id']}/dns", headers=b_headers)
        ).status_code == 404


async def test_a_client_login_reaches_none_of_it(client_for, cloudflare) -> None:
    """Belt and braces on #278 §4: even after an admin hands the client role every cloudflare
    key by mistake, the horizon floor leaves it with nothing to read."""
    t = await make_tenant("cf-client")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _connected(c, headers, cloudflare)
        await c.post(
            "/api/v1/members/invite",
            json={"email": "extern-cf@example.com", "role": "client"},
            headers=headers,
        )
        roles = (await c.get("/api/v1/roles", headers=headers)).json()
        client_role = next(r for r in roles if r["key"] == "client")
        await c.patch(
            f"/api/v1/roles/{client_role['id']}",
            json={
                "permissions": sorted(
                    set(client_role["permissions"])
                    | {"cloudflare.dns.read", "cloudflare.zone.manage"}
                )
            },
            headers=headers,
        )
        async with async_session_maker() as session:
            client_user = await session.scalar(
                select(User).where(User.email == "extern-cf@example.com")
            )
        client_headers = await auth_cookie(client_user)

        zones = await c.get("/api/v1/cloudflare/zones", headers=client_headers)
        assert zones.status_code == 200, zones.text
        assert zones.json()["items"] == [] and zones.json()["total"] == 0


async def test_status_is_cheap_and_check_is_the_one_that_calls_cloudflare(
    client_for, cloudflare
) -> None:
    """A domain page must not depend on Cloudflare being up (docs/PERFORMANCE.md)."""
    t = await make_tenant("cf-cheap")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, domain, _ = await _connected(c, headers, cloudflare)
        cloudflare.calls.clear()
        stored = await c.get(
            f"/api/v1/cloudflare/domains/{domain['id']}/status", headers=headers
        )
        assert stored.status_code == 200, stored.text
        assert stored.json()["live"] is False
        assert cloudflare.calls == []

        live = await c.post(f"/api/v1/cloudflare/domains/{domain['id']}/check", headers=headers)
        assert live.json()["live"] is True
        assert cloudflare.calls


async def test_a_partly_scoped_token_still_produces_a_report(client_for, cloudflare) -> None:
    """Losing the whole screen because one optional probe 403'd would push an admin to mint a
    wider token than they need. What could not be read is named instead."""
    t = await make_tenant("cf-partial")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, domain, _ = await _connected(c, headers, cloudflare)
        cloudflare.deny.add("/pagerules")
        checked = await c.post(
            f"/api/v1/cloudflare/domains/{domain['id']}/check", headers=headers
        )
        assert checked.status_code == 200, checked.text
        assert checked.json()["unavailable"] == ["page_rules"]
        assert "token_error" in checked.json()["issues"]
        # The rest of the report still ran.
        assert checked.json()["origin"] is not None


async def test_linking_a_zone_by_hand_refuses_a_second_zone_for_one_domain(
    client_for, cloudflare
) -> None:
    t = await make_tenant("cf-link")
    headers = await auth_cookie(t.user)
    cloudflare.add_zone("anders-genoemd.nl")
    async with client_for(t.host) as c:
        _, domain, zone = await _connected(c, headers, cloudflare)
        account = (await c.get("/api/v1/cloudflare/accounts", headers=headers)).json()[0]
        await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/sync", headers=headers)
        orphan = next(
            z
            for z in (await c.get("/api/v1/cloudflare/zones", headers=headers)).json()["items"]
            if z["domain_id"] is None
        )
        clash = await c.post(
            f"/api/v1/cloudflare/zones/{orphan['id']}/link",
            json={"domain_id": domain["id"]},
            headers=headers,
        )
        assert clash.status_code == 409
        assert clash.json()["error"]["code"] == "cloudflare_domain_already_linked"

        # Unlink the first, then the manual link is allowed — the apexes may differ.
        assert (
            await c.delete(f"/api/v1/cloudflare/zones/{zone['id']}/link", headers=headers)
        ).status_code == 200
        linked = await c.post(
            f"/api/v1/cloudflare/zones/{orphan['id']}/link",
            json={"domain_id": domain["id"]},
            headers=headers,
        )
        assert linked.status_code == 200, linked.text
        assert linked.json()["domain_name"] == "klant.nl"


async def test_an_unknown_domain_is_404_not_500(client_for, cloudflare) -> None:
    t = await make_tenant("cf-404")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        missing = uuid.uuid4()
        assert (
            await c.get(f"/api/v1/cloudflare/domains/{missing}/status", headers=headers)
        ).status_code == 404
        assert (
            await c.post(
                f"/api/v1/cloudflare/domains/{missing}/connect", json={}, headers=headers
            )
        ).status_code == 404
