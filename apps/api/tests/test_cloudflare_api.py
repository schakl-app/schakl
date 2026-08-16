"""cloudflare module (epic #278): accounts, zones, redirects, Pages, status, isolation.

The reconciliation cases carry their weight here. "It already redirects" and "there are two
Cloudflare accounts" are not exotic — they are what an agency taking over a client's existing
setup hits on day one — so each has a test that puts Cloudflare into that state first and then
asserts on what the module *reports* rather than on what it overwrites.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select, text

from app.core.auth.models import User
from app.db import async_session_maker, set_current_org
from app.integrations.cloudflare import client as cf_client
from app.integrations.cloudflare import redirects as rules
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


async def test_an_ip_filtered_token_is_not_reported_as_an_invalid_one(
    client_for, cloudflare
) -> None:
    """403/9109 is a **valid** token refused from this address, and it must say so.

    Observed live: a Cloudflare token carrying a Client IP Address Filter answers
    ``Cannot use the access token from location: <ip>`` at every endpoint. Every probe fails,
    so the row is indistinguishable from a dead credential unless the code is read — and it
    used to be, because ``_translate`` matched ``CloudflareAuthError`` *before* the code map
    and returned "the token was rejected". That sends an admin to re-mint a working token; the
    fix is one line in Cloudflare's token screen, and no sentence about scopes or validity
    reaches it.
    """
    t = await make_tenant("cf-ipfilter")
    headers = await auth_cookie(t.user)
    cloudflare.ip_blocked = "77.60.220.1"
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        account = await _account(c, headers)
        domain = await _domain(c, headers, "klant.nl", company)

        # The write path: a named error, not the blanket "token rejected".
        refused = await c.post(
            f"/api/v1/cloudflare/domains/{domain['id']}/connect", json={}, headers=headers
        )
        assert refused.status_code == 409
        assert refused.json()["error"]["code"] == "cloudflare_token_ip_blocked"

        # The sync path: same conclusion, and it commits the note before it raises.
        assert (
            await c.post(
                f"/api/v1/cloudflare/accounts/{account['id']}/sync", headers=headers
            )
        ).json()["error"]["code"] == "cloudflare_token_ip_blocked"

        # And the row goes red, carrying the address the admin has to allow. A 403 is normally
        # "not scoped for this call" — degraded, not broken — so this one has to be excepted
        # explicitly, or the screen stays green over a credential that can do nothing at all.
        row = (await c.get("/api/v1/cloudflare/accounts", headers=headers)).json()[0]
        assert row["status"] == "error"
        assert "77.60.220.1" in row["last_error"]

        # Recovery: the admin allows the address, and nothing else has to be re-entered.
        cloudflare.ip_blocked = None
        assert (
            await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/verify", headers=headers)
        ).json()["ok"] is True
        healed = (await c.get("/api/v1/cloudflare/accounts", headers=headers)).json()[0]
        assert healed["status"] == "active" and healed["capabilities"]["zones_read"] is True


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


async def _hand_made_rule(cloudflare, zone_id: str, *, apex: str, target: str) -> dict:
    """The redirect an agency inherits: made in Cloudflare's dashboard, described by a human.

    Built through ``rules.build_rule`` so it is byte-for-byte what schakl would have written —
    except the description, which is what a person typed. That difference is deliberate: it is
    the reason ``find_our_rule`` matches on id and never on description.
    """
    body = rules.build_rule(
        apex=apex,
        target_url=target,
        status_code=301,
        preserve_path=True,
        preserve_query=True,
        include_subdomains=True,
    )
    return cloudflare.add_redirect_rule(zone_id, {**body, "description": f"Redirect {apex}"})


async def test_adopting_an_existing_rule_writes_nothing_at_cloudflare(
    client_for, cloudflare
) -> None:
    """The redirect an agency takes over is usually already right.

    Until adoption the only button appended a *second* rule to the same phase — where Cloudflare
    takes the first match, so the obvious press could leave the zone with two redirects and no
    change in behaviour. Adopting claims the rule by id and touches Cloudflare not at all.
    """
    t = await make_tenant("cf-adopt")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, domain, _ = await _connected(c, headers, cloudflare)
        zone_id = next(iter(cloudflare.zones))
        theirs = await _hand_made_rule(cloudflare, zone_id, apex="klant.nl", target="https://nieuw.nl")

        # The status report offers the id, which is the only safe way to name a rule — and it
        # is *not* a conflict, because schakl holds no rule for it to compete with. It is simply
        # this domain's redirect, which is why the panel lists it rather than boxing it in a
        # warning nobody reads.
        checked = (
            await c.post(f"/api/v1/cloudflare/domains/{domain['id']}/check", headers=headers)
        ).json()
        assert "redirect_conflict" not in checked["issues"]
        assert checked["conflicts"][0]["rule_id"] == theirs["id"]
        # Described well enough that adoption is one press: the row carries the rule's own
        # intent, so the button no longer posts whatever happens to be typed in the form above it.
        assert checked["conflicts"][0]["intent"] == {
            "target_url": "https://nieuw.nl",
            "status_code": 301,
            "preserve_path": True,
            "preserve_query": True,
            "include_subdomains": True,
        }
        assert checked["conflicts"][0]["domain_wide"] is True

        cloudflare.calls.clear()
        adopted = await c.post(
            f"/api/v1/cloudflare/domains/{domain['id']}/redirect/adopt",
            json={"rule_id": theirs["id"], "target_url": "https://nieuw.nl", "status_code": 301},
            headers=headers,
        )
        assert adopted.status_code == 200, adopted.text
        assert adopted.json()["last_status"] == "active"
        assert adopted.json()["target_url"] == "https://nieuw.nl"

        # Nothing was created, updated, re-ordered or deleted: one read, no writes.
        assert [m for m, _ in cloudflare.calls if m != "GET"] == []
        assert len(cloudflare.rulesets[zone_id]["rules"]) == 1

        # It is ours now, so it stops reading as somebody else's rule.
        after = (
            await c.post(f"/api/v1/cloudflare/domains/{domain['id']}/check", headers=headers)
        ).json()
        assert after["conflicts"] == []
        assert "redirect_conflict" not in after["issues"]
        assert "domain_says_redirect" not in after["issues"]
        assert after["redirect_live"]["present"] is True
        assert after["redirect_live"]["differences"] == []
        # And the domain agrees, exactly as it does after a save.
        assert after["domain_status"] == "redirect"


async def test_adoption_refuses_a_rule_that_is_not_what_we_would_have_written(
    client_for, cloudflare
) -> None:
    """"Adopt whatever is there" would import somebody's 302 as this domain's redirect, and the
    next save would then "fix" a live client's redirect to something nobody asked for. The
    difference is reported by field so the admin can match it or overwrite it deliberately."""
    t = await make_tenant("cf-adopt-diff")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, domain, _ = await _connected(c, headers, cloudflare)
        zone_id = next(iter(cloudflare.zones))
        theirs = await _hand_made_rule(cloudflare, zone_id, apex="klant.nl", target="https://nieuw.nl")

        refused = await c.post(
            f"/api/v1/cloudflare/domains/{domain['id']}/redirect/adopt",
            # Same rule, different intent: a 302 where Cloudflare holds a 301.
            json={"rule_id": theirs["id"], "target_url": "https://nieuw.nl", "status_code": 302},
            headers=headers,
        )
        assert refused.status_code == 409, refused.text
        body = refused.json()["error"]
        assert body["code"] == "cloudflare_redirect_differs"
        assert "status_code" in body["fields"]
        # Refused means refused: no row, and their rule untouched.
        status = (
            await c.get(f"/api/v1/cloudflare/domains/{domain['id']}/status", headers=headers)
        ).json()
        assert status["redirect"] is None
        assert len(cloudflare.rulesets[zone_id]["rules"]) == 1

        # A rule that vanished between reading the report and pressing the button is a 404, not
        # a stored redirect pointing at nothing.
        cloudflare.rulesets[zone_id]["rules"] = []
        gone = await c.post(
            f"/api/v1/cloudflare/domains/{domain['id']}/redirect/adopt",
            json={"rule_id": theirs["id"], "target_url": "https://nieuw.nl", "status_code": 301},
            headers=headers,
        )
        assert gone.status_code == 404


async def test_adoption_never_orphans_a_rule_we_already_own(client_for, cloudflare) -> None:
    """Adopting over our own live rule would leave a rule at Cloudflare that nothing here knows
    about, on a client's zone — the exact state this module exists to prevent."""
    t = await make_tenant("cf-adopt-own")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, domain, _ = await _connected(c, headers, cloudflare)
        zone_id = next(iter(cloudflare.zones))
        assert (
            await c.put(
                f"/api/v1/cloudflare/domains/{domain['id']}/redirect",
                json={"target_url": "https://nieuw.nl", "status_code": 301},
                headers=headers,
            )
        ).status_code == 200
        theirs = await _hand_made_rule(
            cloudflare, zone_id, apex="klant.nl", target="https://anders.nl"
        )

        refused = await c.post(
            f"/api/v1/cloudflare/domains/{domain['id']}/redirect/adopt",
            json={"rule_id": theirs["id"], "target_url": "https://anders.nl", "status_code": 301},
            headers=headers,
        )
        assert refused.status_code == 409
        assert refused.json()["error"]["code"] == "cloudflare_redirect_owned"


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
        assert checked["conflicts"][0]["kind"] == "page_rule"
        assert checked["conflicts"][0]["description"] == "klant.nl/*"
        # Not a conflict: schakl holds no rule here for a Page Rule to beat. But
        # ``domain_says_redirect`` stands, and this is the case that shows why it must — a
        # forwarding Page Rule is a different product this module cannot write, so it can never
        # become schakl's redirect and the domain's own status is genuinely unbacked here.
        assert "redirect_conflict" not in checked["issues"]
        assert "domain_says_redirect" in checked["issues"]
        # The status the tenant set by hand is untouched: only a domain-wide *Redirect Rule* may
        # move a domain record, and a Page Rule is never one.
        assert checked["domain_status"] == "redirect"


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


@pytest.mark.parametrize("status_code", [301, 302, 307, 308])
@pytest.mark.parametrize("preserve_path", [True, False])
@pytest.mark.parametrize("preserve_query", [True, False])
@pytest.mark.parametrize("include_subdomains", [True, False])
def test_a_rule_reads_back_as_the_intent_that_built_it(
    status_code: int, preserve_path: bool, preserve_query: bool, include_subdomains: bool
) -> None:
    """``rule_intent`` is ``build_rule`` run backwards, and the round trip is the whole contract.

    Every field it recovers is read off the rule's *shape* rather than a flag — the path is
    preserved iff the target is a ``concat``, subdomains are included iff the expression carries
    the ``ends_with`` arm — so nothing but exercising both directions can catch a shape drifting
    away from its reading. If this holds, adopting an inherited rule is one press; if it slips,
    the adopt button posts a subtly wrong intent and `compare` refuses a rule that is identical.
    """
    intent = {
        "target_url": "https://nieuw.nl/pad",
        "status_code": status_code,
        "preserve_path": preserve_path,
        "preserve_query": preserve_query,
        "include_subdomains": include_subdomains,
    }
    rule = rules.build_rule(apex="klant.nl", **intent)
    assert rules.rule_intent(rule, "klant.nl") == intent
    assert rules.domain_wide_for(rule, "klant.nl") is True


def test_only_a_whole_domain_rule_counts_as_one() -> None:
    """The reading a domain's status hangs on, so it refuses everything it does not recognise.

    Each of these mentions the apex, and only some of them redirect the domain. Matching on
    "the apex appears in the expression" would put "omleiding" on a record whose site serves
    perfectly well — a wrong answer nobody would think to look for, on a screen that is supposed
    to be reporting facts.
    """

    def rule(expression: str, *, action: str = "redirect") -> dict:
        return {
            "action": action,
            "expression": expression,
            "action_parameters": {
                "from_value": {
                    "status_code": 301,
                    "target_url": {"value": "https://nieuw.nl"},
                    "preserve_query_string": True,
                }
            },
        }

    wide = [
        '(http.host eq "klant.nl")',
        'http.host eq "klant.nl"',  # Cloudflare drops our redundant parentheses
        '(lower(http.host) eq "klant.nl")',  # ...and its dashboard adds this
        '(http.host eq "klant.nl" or ends_with(http.host, ".klant.nl"))',
        # Cloudflare's own list operator: plainly the whole domain, and **not** a shape any
        # intent of ours produces. Domain-wide, therefore, and never adoptable.
        'http.host in {"klant.nl" "www.klant.nl"}',
    ]
    narrow = [
        '(http.host eq "oud.klant.nl")',  # one subdomain is not the domain
        '(http.host eq "klant.nl") and (http.request.uri.path eq "/aanbieding")',  # one path
        '(http.host eq "nietklant.nl")',  # not even this domain
        'http.host in {"www.klant.nl"}',  # a list the apex is not in
        'http.request.full_uri wildcard "https://klant.nl/*"',  # a shape we do not read: refused
    ]
    for expression in wide:
        assert rules.domain_wide_for(rule(expression), "klant.nl") is True, expression
    for expression in narrow:
        assert rules.domain_wide_for(rule(expression), "klant.nl") is False, expression
    # A rule that does something else entirely is not a redirect however it is scoped.
    assert rules.domain_wide_for(rule(wide[0], action="block"), "klant.nl") is False
    # Domain-wide and unreadable are independent: the list form has no intent, so no adopt
    # button is drawn — but the target is still recoverable, which is what the row prints and
    # what the domain record gets.
    listed = rule('http.host in {"klant.nl" "www.klant.nl"}')
    assert rules.rule_intent(listed, "klant.nl") is None
    assert rules.rule_target(listed) == ("https://nieuw.nl", False)


def test_an_unreadable_rule_is_described_not_guessed_at() -> None:
    """``None`` is a real answer. A half-guessed intent is a button that either refuses or, far
    worse, succeeds — claiming a rule as something it is not, after which an ordinary save
    rewrites a live client's redirect to whatever we assumed."""
    unreadable = {
        "action": "redirect",
        "expression": '(http.host eq "klant.nl")',
        "action_parameters": {
            "from_value": {
                "status_code": 301,
                # A target expression that is not our ``concat`` shape.
                "target_url": {"expression": 'concat(http.host, "/nieuw")'},
                "preserve_query_string": True,
            }
        },
    }
    assert rules.rule_intent(unreadable, "klant.nl") is None
    assert rules.rule_target(unreadable) is None
    # But it still redirects the whole domain, so it still moves the domain record.
    assert rules.domain_wide_for(unreadable, "klant.nl") is True


# --------------------------------------------------------------------------------------- #
# An inherited redirect: stored, listed, and reflected on the domain record
# --------------------------------------------------------------------------------------- #
async def test_an_inherited_redirect_survives_the_page_load(client_for, cloudflare) -> None:
    """The state this module exists to serve, and the one that used not to survive a refresh.

    ``GET .../status`` reads stored rows and calls nothing, so before the zone remembered what a
    check saw, a redirect made by hand in Cloudflare's dashboard was visible only for as long as
    somebody held the check button's answer on screen. Reload the domain and it was gone.
    """
    t = await make_tenant("cf-inherited")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, domain, _ = await _connected(c, headers, cloudflare)
        zone_id = next(iter(cloudflare.zones))
        theirs = await _hand_made_rule(
            cloudflare, zone_id, apex="klant.nl", target="https://nieuw.nl"
        )

        # Nothing is known before anyone looks, and the report says *that* rather than "none".
        before = (
            await c.get(f"/api/v1/cloudflare/domains/{domain['id']}/status", headers=headers)
        ).json()
        assert before["conflicts"] == []
        assert before["redirects_observed_at"] is None

        await c.post(f"/api/v1/cloudflare/domains/{domain['id']}/check", headers=headers)

        # ...and now the *stored* read carries it, with an age. No Cloudflare call: the page load
        # must not depend on an outside API being up (docs/PERFORMANCE.md).
        cloudflare.calls.clear()
        stored = (
            await c.get(f"/api/v1/cloudflare/domains/{domain['id']}/status", headers=headers)
        ).json()
        assert cloudflare.calls == []
        assert stored["live"] is False
        assert [row["rule_id"] for row in stored["conflicts"]] == [theirs["id"]]
        assert stored["conflicts"][0]["target_url"] == "https://nieuw.nl"
        assert stored["conflicts"][0]["domain_wide"] is True
        assert stored["redirects_observed_at"] is not None
        # And the domain record itself agrees, so the domains list stops calling it active.
        assert stored["domain_status"] == "redirect"
        assert stored["domain_redirect_url"] == "https://nieuw.nl"


async def test_a_read_that_finds_nothing_clears_what_it_read_before(
    client_for, cloudflare
) -> None:
    """A list that only ever grows is `_flag_account`'s one-way flag in another costume.

    Without the clear, a redirect somebody deleted in Cloudflare's dashboard stays on this panel
    for ever — and, worse, keeps the domain record claiming a redirect that no longer exists.
    """
    t = await make_tenant("cf-cleared")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, domain, _ = await _connected(c, headers, cloudflare)
        zone_id = next(iter(cloudflare.zones))
        await _hand_made_rule(cloudflare, zone_id, apex="klant.nl", target="https://nieuw.nl")
        checked = (
            await c.post(f"/api/v1/cloudflare/domains/{domain['id']}/check", headers=headers)
        ).json()
        assert len(checked["conflicts"]) == 1
        assert checked["domain_status"] == "redirect"

        # Deleted at Cloudflare, by hand, the way it was made.
        cloudflare.rulesets[zone_id]["rules"] = []
        after = (
            await c.post(f"/api/v1/cloudflare/domains/{domain['id']}/check", headers=headers)
        ).json()
        assert after["conflicts"] == []
        # The walk-back: the domain still said exactly what the observation put there, so it is
        # ours to take back. ``redirects_observed_at`` stays set — we looked, and found nothing.
        assert after["domain_status"] == "active"
        assert after["domain_redirect_url"] is None
        assert after["redirects_observed_at"] is not None
        assert "domain_says_redirect" not in after["issues"]


async def test_the_walk_back_never_overwrites_a_status_set_by_hand(
    client_for, cloudflare
) -> None:
    """The same test :meth:`remove_redirect` applies, for the same reason: a status somebody has
    since changed by hand is theirs, and a reconcile that "tidies" it is a data-loss bug that
    reports success."""
    t = await make_tenant("cf-handset")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, domain, _ = await _connected(c, headers, cloudflare)
        zone_id = next(iter(cloudflare.zones))
        await _hand_made_rule(cloudflare, zone_id, apex="klant.nl", target="https://nieuw.nl")
        await c.post(f"/api/v1/cloudflare/domains/{domain['id']}/check", headers=headers)

        # Somebody points the domain somewhere else in schakl, then the Cloudflare rule goes.
        await c.patch(
            f"/api/v1/domains/{domain['id']}",
            json={"status": "redirect", "redirect_url": "https://ergens-anders.nl"},
            headers=headers,
        )
        cloudflare.rulesets[zone_id]["rules"] = []
        after = (
            await c.post(f"/api/v1/cloudflare/domains/{domain['id']}/check", headers=headers)
        ).json()
        assert after["domain_status"] == "redirect"
        assert after["domain_redirect_url"] == "https://ergens-anders.nl"
        # It is reported instead — the domain claims a redirect nothing here backs.
        assert "domain_says_redirect" in after["issues"]


async def test_two_domain_wide_rules_are_reported_never_guessed_between(
    client_for, cloudflare
) -> None:
    """Cloudflare evaluates top-down and this module does not evaluate filter expressions, so
    which of two whole-domain rules actually wins is not a question it can answer. Writing either
    one onto the domain record would be a coin toss presented as a fact."""
    t = await make_tenant("cf-ambiguous")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, domain, _ = await _connected(c, headers, cloudflare)
        zone_id = next(iter(cloudflare.zones))
        await _hand_made_rule(cloudflare, zone_id, apex="klant.nl", target="https://nieuw.nl")
        await _hand_made_rule(cloudflare, zone_id, apex="klant.nl", target="https://anders.nl")

        checked = (
            await c.post(f"/api/v1/cloudflare/domains/{domain['id']}/check", headers=headers)
        ).json()
        assert len(checked["conflicts"]) == 2
        assert checked["domain_status"] == "active"
        # Silence would be the bug: the panel has to say the two halves disagree.
        assert "cloudflare_says_redirect" in checked["issues"]


async def test_a_probe_that_could_not_run_keeps_what_it_saw_last_time(
    client_for, cloudflare, monkeypatch
) -> None:
    """Losing a client's Page Rules to a missing token scope would be this module deciding that
    what it cannot see does not exist. A probe that *ran* and found nothing clears its own
    entries; one that could not run leaves them exactly as they were."""
    t = await make_tenant("cf-partial")
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
                    {"id": "forwarding_url", "value": {"url": "https://elders.nl"}}
                ],
            }
        ]
        first = (
            await c.post(f"/api/v1/cloudflare/domains/{domain['id']}/check", headers=headers)
        ).json()
        assert [row["kind"] for row in first["conflicts"]] == ["page_rule"]

        # Now the Page Rules scope goes away — the token was narrowed, or never had it.
        async def refuse(self, zone_id: str):  # noqa: ANN001, ANN202, ARG001
            raise cf_client.CloudflareError("no page rules scope", status=403)

        monkeypatch.setattr(cf_client.CloudflareClient, "list_page_rules", refuse)
        after = (
            await c.post(f"/api/v1/cloudflare/domains/{domain['id']}/check", headers=headers)
        ).json()
        assert [row["kind"] for row in after["conflicts"]] == ["page_rule"]
        assert "page_rules" in after["unavailable"]
        # ...and it is still there on the next stored read, not only in that one response.
        stored = (
            await c.get(f"/api/v1/cloudflare/domains/{domain['id']}/status", headers=headers)
        ).json()
        assert [row["kind"] for row in stored["conflicts"]] == ["page_rule"]


async def test_a_read_only_caller_never_moves_the_domain_record(client_for, cloudflare) -> None:
    """``POST .../check`` declares ``cloudflare.dns.read``. Writing a domain record off the back
    of it would hand every Cloudflare-only admin an edit permission nobody granted them, so the
    write is gated on ``domains.domain.write`` — and the finding still renders, which is the
    whole point: they can see what is wrong, they just cannot silently fix it."""
    t = await make_tenant("cf-readonly")
    watcher = await make_tenant("cf-readonly-m", email="cf-readonly-member@example.com")
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        membership = await add_membership(session, t.org.id, watcher.user.id, role="member")
        membership_id = str(membership.id)
        await session.commit()
    owner_headers = await auth_cookie(t.user)
    watcher_headers = await auth_cookie(watcher.user, org_id=t.org.id)

    async with client_for(t.host) as c:
        _, domain, _ = await _connected(c, owner_headers, cloudflare)
        zone_id = next(iter(cloudflare.zones))
        await _hand_made_rule(cloudflare, zone_id, apex="klant.nl", target="https://nieuw.nl")

        # Everything this screen needs to *look*, and nothing that writes a domain.
        role = (
            await c.post(
                "/api/v1/roles",
                json={
                    "key": "cf-kijker",
                    "name_i18n": {"nl": "Cloudflare kijker", "en": "Cloudflare viewer"},
                    "permissions": ["cloudflare.dns.read", "domains.domain.read"],
                },
                headers=owner_headers,
            )
        ).json()
        assert (
            await c.put(
                f"/api/v1/members/{membership_id}/roles",
                json={"role_ids": [role["id"]]},
                headers=owner_headers,
            )
        ).status_code == 200

        checked = (
            await c.post(
                f"/api/v1/cloudflare/domains/{domain['id']}/check", headers=watcher_headers
            )
        ).json()
        assert checked["conflicts"][0]["domain_wide"] is True
        assert checked["domain_status"] == "active"
        assert "cloudflare_says_redirect" in checked["issues"]

        # The owner's own check does move it, so the difference really is the permission and
        # not something incidental about this domain.
        owned = (
            await c.post(
                f"/api/v1/cloudflare/domains/{domain['id']}/check", headers=owner_headers
            )
        ).json()
        assert owned["domain_status"] == "redirect"


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


# --------------------------------------------------------------------------------------- #
# A scope refusal is not a rejected token, and never costs a rule that was already made
# --------------------------------------------------------------------------------------- #
async def _set_observed_nameservers(org_id, domain_id: str, hosts: list[str]) -> None:
    """Write what public DNS answers, the way the domains module's own resolver would.

    That column is the *other half* of the delegation verdict and no Cloudflare call touches it
    — which is exactly what these tests are about, so they set it directly rather than pretend
    a resolver ran.
    """
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        await session.execute(
            text(
                "UPDATE domains SET nameservers = CAST(:ns AS jsonb), dns_checked_at = now() "
                "WHERE id = CAST(:id AS uuid) AND org_id = CAST(:org AS uuid)"
            ).bindparams(ns=json.dumps(hosts), id=domain_id, org=str(org_id))
        )
        await session.commit()


async def test_a_dns_refusal_keeps_the_redirect_it_already_pushed(client_for, cloudflare) -> None:
    """The placeholder's scope is DNS; the rule's is redirects. They must fail separately.

    Inside one ``try`` the DNS 403 failed the whole request **after** the rule existed at
    Cloudflare — and the raise rolled back the row that was the only record of it, so the next
    press appended a second rule to a live client's zone, and the next a third, while the screen
    said the token had been rejected.
    """
    t = await make_tenant("cf-scope-dns")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account, domain, _ = await _connected(c, headers, cloudflare)
        zone_id = next(iter(cloudflare.zones))
        cloudflare.deny.add("/dns_records")

        res = await c.put(
            f"/api/v1/cloudflare/domains/{domain['id']}/redirect",
            json={"target_url": "https://nieuw.nl", "ensure_origin": True},
            headers=headers,
        )
        assert res.status_code == 200, res.text
        # The rule is what was asked for and it is live; the placeholder's refusal is a note.
        assert res.json()["last_status"] == "active"
        assert "not authorized" in (res.json()["last_error"] or "")
        assert len(cloudflare.rulesets[zone_id]["rules"]) == 1

        # And the note lands on the *redirect*, never on the account: a missing DNS scope is
        # degraded, not a broken credential. This is the half that keeps the two failure
        # writes apart — they are gated on opposite outcomes of the same call, so a regression
        # that wired ``_record_failure`` into the placeholder path would redden a token that is
        # working. Asserting it here rather than against a credential Cloudflare refuses
        # outright, where the push fails and nothing downstream runs at all.
        listed = (await c.get("/api/v1/cloudflare/accounts", headers=headers)).json()
        row = next(a for a in listed if a["id"] == account["id"])
        assert row["status"] == "active"
        assert row["last_error"] is None

        # And the retry updates the rule it knows about instead of appending another.
        again = await c.put(
            f"/api/v1/cloudflare/domains/{domain['id']}/redirect",
            json={"target_url": "https://nieuwer.nl", "ensure_origin": True},
            headers=headers,
        )
        assert again.status_code == 200, again.text
        assert len(cloudflare.rulesets[zone_id]["rules"]) == 1


async def test_a_missing_scope_and_a_dead_token_say_different_things(
    client_for, cloudflare
) -> None:
    """403 is "not scoped for *this call*"; 401 is "I do not accept this token at all".

    Collapsed into one key, a token missing one zone permission told an admin their credential
    had been refused — the one thing that had not happened, since every other call it makes
    works. That is what "the token seems to have the right permissions" describes.
    """
    t = await make_tenant("cf-scope-msg")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account, domain, _ = await _connected(c, headers, cloudflare)

        cloudflare.deny.add("/rulesets")
        scoped = await c.put(
            f"/api/v1/cloudflare/domains/{domain['id']}/redirect",
            json={"target_url": "https://nieuw.nl"},
            headers=headers,
        )
        assert scoped.status_code == 409
        assert scoped.json()["error"]["code"] == "cloudflare_scope_missing"

        # Cloudflare's own text survives the rollback, so the settings screen can say which
        # permission is missing — a sentence no i18n key can write (§9).
        listed = (await c.get("/api/v1/cloudflare/accounts", headers=headers)).json()
        row = next(a for a in listed if a["id"] == account["id"])
        assert "not authorized" in (row["last_error"] or "")
        # ...and a plain 403 does not redden the row: degraded, not broken.
        assert row["status"] == "active"

        cloudflare.deny.clear()
        cloudflare.revoked = True
        dead = await c.put(
            f"/api/v1/cloudflare/domains/{domain['id']}/redirect",
            json={"target_url": "https://nieuw.nl"},
            headers=headers,
        )
        assert dead.status_code == 409
        assert dead.json()["error"]["code"] == "cloudflare_token_rejected"


# --------------------------------------------------------------------------------------- #
# Delegation is tri-state
# --------------------------------------------------------------------------------------- #
async def test_an_unanswered_lookup_is_unknown_delegation_not_wrong_delegation(
    client_for, cloudflare
) -> None:
    """``fetch_dns`` returns ``[]`` for a timeout exactly as it does for a domain that really
    delegates nowhere, so an empty observation is not evidence. As a plain boolean it produced a
    confident "change your nameservers at the registrar" over a lookup that never answered."""
    t = await make_tenant("cf-ns-unknown")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, domain, _ = await _connected(c, headers, cloudflare)

        checked = (
            await c.post(f"/api/v1/cloudflare/domains/{domain['id']}/check", headers=headers)
        ).json()
        assert checked["observed_nameservers"] == []
        assert checked["nameservers_delegated"] is None
        assert "nameservers_not_delegated" not in checked["issues"]


async def test_delegation_answers_true_and_false_when_both_sides_spoke(
    client_for, cloudflare
) -> None:
    """With both halves present the verdict is a real one — and it says *when* the public-DNS
    half was read, which ``checked_at`` (the Cloudflare half) never covered."""
    t = await make_tenant("cf-ns-known")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, domain, _ = await _connected(c, headers, cloudflare)

        await _set_observed_nameservers(t.org.id, domain["id"], ["ns1.oudehoster.nl"])
        elsewhere = (
            await c.post(f"/api/v1/cloudflare/domains/{domain['id']}/check", headers=headers)
        ).json()
        assert elsewhere["nameservers_delegated"] is False
        assert "nameservers_not_delegated" in elsewhere["issues"]
        assert elsewhere["nameservers_checked_at"] is not None

        # Mid-propagation: one of Cloudflare's pair beside one of the old host's is delegation
        # happening, not delegation absent — hence an intersection rather than an equality.
        await _set_observed_nameservers(
            t.org.id, domain["id"], ["ana.ns.cloudflare.com", "ns1.oudehoster.nl"]
        )
        moving = (
            await c.post(f"/api/v1/cloudflare/domains/{domain['id']}/check", headers=headers)
        ).json()
        assert moving["nameservers_delegated"] is True
        assert "nameservers_not_delegated" not in moving["issues"]


# --------------------------------------------------------------------------------------- #
# The capability list covers the scopes the buttons use
# --------------------------------------------------------------------------------------- #
async def test_verify_probes_the_two_scopes_the_domain_page_actually_uses(
    client_for, cloudflare
) -> None:
    """They were the conspicuous hole: an admin read ✓ down every line of "Wat dit token mag"
    and still got a token error at the redirect button, because neither DNS nor the redirect
    ruleset was ever probed. Both need a zone to address, so both are **absent** rather than
    false before this account has synced one — "we did not look" is not "not granted"."""
    t = await make_tenant("cf-caps")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _account(c, headers)
        cloudflare.add_zone("klant.nl")

        before = (
            await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/verify", headers=headers)
        ).json()
        assert "dns_read" not in before["capabilities"]
        assert "redirect_read" not in before["capabilities"]

        await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/sync", headers=headers)
        after = (
            await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/verify", headers=headers)
        ).json()
        # A zone with no redirect rules has no entrypoint ruleset and answers 404 — a normal
        # state, and the token was plainly allowed to ask.
        assert after["capabilities"]["dns_read"] is True
        assert after["capabilities"]["redirect_read"] is True

        cloudflare.deny.add("/dns_records")
        refused = (
            await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/verify", headers=headers)
        ).json()
        assert refused["capabilities"]["dns_read"] is False
        assert refused["capabilities"]["redirect_read"] is True
        # A scoped token is still a working token: the rest of the list is unaffected.
        assert refused["capabilities"]["zones_read"] is True


async def test_a_refused_capability_records_what_cloudflare_answered(
    client_for, cloudflare
) -> None:
    """A ✗ with no explanation is the one state an admin cannot act on.

    "Niet toegekend" reads as *add this permission* whatever the cause — so against a token whose
    Cloudflare screen plainly grants it, the sentence is unfalsifiable and the only move left is
    re-minting a credential that was never the problem. The status, the code and Cloudflare's own
    text separate the three things that produce the same ✗, and none of them is in ``str(exc)``.
    """
    t = await make_tenant("cf-caps-why")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _account(c, headers)
        cloudflare.add_zone("klant.nl")
        await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/sync", headers=headers)

        # Two refusals of different kinds, which is the whole point: a scope this token was never
        # granted, and a call the endpoint would not take. Both used to be a bare False.
        cloudflare.deny.add("/rulesets")
        cloudflare.fail["/dns_records"] = (400, "per_page must be between 5 and 100", 1003)
        body = (
            await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/verify", headers=headers)
        ).json()

        assert body["capabilities"]["redirect_read"] is False
        assert body["capabilities"]["dns_read"] is False
        assert "HTTP 403" in body["capability_errors"]["redirect_read"]
        assert "code 10000" in body["capability_errors"]["redirect_read"]
        assert "not authorized" in body["capability_errors"]["redirect_read"]
        assert "HTTP 400" in body["capability_errors"]["dns_read"]
        assert "per_page" in body["capability_errors"]["dns_read"]
        # Only refusals. A capability that answered yes has nothing to explain, and an
        # explanation printed beside a ✓ is worse than none.
        assert set(body["capability_errors"]) == {"redirect_read", "dns_read"}

        # It is stored, not just returned: the settings screen renders it on a page load that
        # ran no verify of its own.
        listed = (await c.get("/api/v1/cloudflare/accounts", headers=headers)).json()[0]
        assert listed["capability_errors"]["dns_read"] == body["capability_errors"]["dns_read"]

        # And it is replaced wholesale, never merged: a fixed permission leaves no trace of the
        # refusal it used to have.
        cloudflare.deny.clear()
        cloudflare.fail.clear()
        healed = (
            await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/verify", headers=headers)
        ).json()
        assert healed["capabilities"]["dns_read"] is True
        assert healed["capability_errors"] == {}


async def test_the_probes_ask_plainly(client_for, cloudflare) -> None:
    """A probe must differ from the call it stands in for as little as possible.

    ``per_page=1`` was the only thing the probes did that no real call does (``paginate`` sends
    50), on endpoints of which one — Registrar — refuses list options altogether. It bought
    nothing and it is exactly the kind of difference that makes a probe answer "not granted"
    about a permission the token holds. The zone pair shed it first; every probe now asks
    plainly, which is why this test sweeps them all rather than the two that were fixed.
    """
    t = await make_tenant("cf-probe-plain")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _account(c, headers)
        cloudflare.add_zone("klant.nl")
        await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/sync", headers=headers)
        cloudflare.queries.clear()
        await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/verify", headers=headers)

        probed = [
            (path, query)
            for path, query in cloudflare.queries
            if any(
                fragment in path
                for fragment in ("dns_records", "rulesets", "/zones", "pages/projects",
                                 "registrar/domains")
            )
        ]
        assert probed, "the capabilities were not probed at all"
        assert all("per_page" not in query for _, query in probed), probed


async def test_a_single_page_endpoint_is_read_whole(client_for, cloudflare) -> None:
    """Not every Cloudflare list takes a page, and one that does not must still be read.

    Registrar answers the whole register at once and refuses ``page``/``per_page`` with
    ``400 Invalid list options provided`` — so asking for page 1 of it failed the read outright,
    and the sync said *"Niet alles kon gelezen worden"* about an endpoint that was willing to
    answer, over a token that was scoped for it. The refusal now costs one plain retry.
    """
    t = await make_tenant("cf-single-page")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        account = await _account(c, headers)
        cloudflare.add_zone("klant.nl")
        cloudflare.add_registration("klant.nl")
        await _domain(c, headers, "klant.nl", company)

        body = (
            await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/sync", headers=headers)
        ).json()

        assert body["warnings"] == []
        assert body["registrar_read"] is True
        assert body["registrar_domains_synced"] == 1
        assert body["registrar_domains_matched"] == 1
        # The paged attempt happened, was refused, and was followed by the plain one — the point
        # being that nothing had to know in advance which endpoints page.
        asked = [q for p, q in cloudflare.queries if "registrar/domains" in p]
        assert any("per_page" in q for q in asked), asked
        assert "" in asked, asked


async def test_an_endpoint_that_pages_at_its_own_size_is_read_to_the_end(
    client_for, cloudflare
) -> None:
    """Pages' projects cap ``per_page`` at ten, so schakl asks for ten and the argument never
    starts.

    This is the read that reported an agency's thirteen Pages projects as unreadable. The cap is
    in no Cloudflare schema — only an ``example: 10`` — and the live endpoint answers
    ``400``/``8000024`` to anything above it, so asking for fifty bought a guaranteed refusal on
    every sync before anything could go right. Recovering from that refusal is worth doing and is
    not the same as being right about it: the size an endpoint gives is a fact about the endpoint
    (``PAGE_SIZES``), and the whole exchange is two ordinary paged requests.
    """
    t = await make_tenant("cf-capped-page")
    headers = await auth_cookie(t.user)
    cloudflare.capped_page["pages/projects"] = 10
    cloudflare.pages["acct-1"] = [
        {"name": f"site-{n:02d}", "subdomain": f"site-{n:02d}.pages.dev"} for n in range(13)
    ]
    async with client_for(t.host) as c:
        account = await _account(c, headers)
        cloudflare.queries.clear()

        body = (
            await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/sync", headers=headers)
        ).json()

        assert body["warnings"] == []
        assert body["pages_projects_synced"] == 13
        listed = await c.get("/api/v1/cloudflare/pages/projects", headers=headers)
        names = {p["name"] for p in listed.json()}
        assert names == {f"site-{n:02d}" for n in range(13)}
        # Two requests, both at a size Cloudflare serves, and **no refusal in between**: the 400
        # that used to be paid on every single sync is now never provoked at all.
        asked = [q for p, q in cloudflare.queries if "pages/projects" in p]
        assert asked == ["page=1&per_page=10", "page=2&per_page=10"], asked


async def test_an_unknown_cap_is_still_recovered_from(client_for, cloudflare) -> None:
    """The fallback stays, because the next undocumented cap is the one we do not know about.

    ``PAGE_SIZES`` is knowledge, and knowledge runs out: Cloudflare publishes none of these
    numbers, so an endpoint we ask fifty of may refuse it tomorrow exactly as Pages does today.
    When that happens the read still finishes — one plain request reveals the size Cloudflare
    chose, and the rest is ordinary paging — which is what keeps a new cap a wasted round trip
    instead of an outage.
    """
    t = await make_tenant("cf-unknown-cap")
    headers = await auth_cookie(t.user)
    # An endpoint with no entry in PAGE_SIZES: schakl asks for fifty and is refused.
    cloudflare.capped_page["/zones"] = 2
    for n in range(5):
        cloudflare.add_zone(f"klant-{n}.nl")
    async with client_for(t.host) as c:
        account = await _account(c, headers)
        cloudflare.queries.clear()

        body = (
            await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/sync", headers=headers)
        ).json()

        assert body["zones_synced"] == 5, body
        asked = [q for p, q in cloudflare.queries if p == "/zones"]
        # Refused at fifty, then asked for the *same page* without naming a size — and finished
        # at the size Cloudflare picked. Only the size was dropped, which is what leaves page one
        # knowably page one; the plain read is a rung further down and was never needed.
        assert asked[0].endswith("page=1&per_page=50"), asked
        assert all(q for q in asked), asked
        assert len([q for q in asked if "per_page" not in q]) == 3, asked


async def test_a_paged_read_that_ends_short_of_the_count_is_not_a_list(
    client_for, cloudflare
) -> None:
    """The ordinary paged path owes the same promise the fallback does, and never kept it.

    An endpoint is perfectly capable of claiming ``total_pages: 1`` over a ``total_count`` of
    thirteen and then handing over ten rows — two claims, contradicting each other. The fallback
    read has always refused to pass that off as a whole list (§17); the paged loop believed the
    last-page signal and returned the prefix silently, which is the worse of the two failures
    because nothing anywhere says a thing.
    """
    t = await make_tenant("cf-short-count")
    headers = await auth_cookie(t.user)
    cloudflare.pages["acct-1"] = [
        {"name": f"site-{n:02d}", "subdomain": f"site-{n:02d}.pages.dev"} for n in range(10)
    ]
    # Cloudflare says it holds thirteen while serving one full page of ten and calling it the last.
    cloudflare.short_count["pages/projects"] = 13
    async with client_for(t.host) as c:
        account = await _account(c, headers)

        body = (
            await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/sync", headers=headers)
        ).json()

        assert body["pages_projects_synced"] == 0
        assert any("10 of 13" in w for w in body["warnings"]), body["warnings"]


async def test_an_endpoint_that_ignores_the_page_is_named_as_one(client_for, cloudflare) -> None:
    """Twenty identical requests and a cap error about a list of ten is not a diagnosis.

    An endpoint that accepts ``page`` and ignores it answers every page with the same rows. The
    loop's only defence was the cap, so it asked twenty times and then reported *"more than 1000
    rows"* about ten projects — sending whoever read it looking for an agency too large, rather
    than for an endpoint that does not page. It is caught on the second page now, and said.
    """
    t = await make_tenant("cf-ignores-page")
    headers = await auth_cookie(t.user)
    cloudflare.pages["acct-1"] = [
        {"name": f"site-{n:02d}", "subdomain": f"site-{n:02d}.pages.dev"} for n in range(25)
    ]
    cloudflare.ignores_page.add("pages/projects")
    async with client_for(t.host) as c:
        account = await _account(c, headers)
        cloudflare.queries.clear()

        body = (
            await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/sync", headers=headers)
        ).json()

        assert body["pages_projects_synced"] == 0
        assert any("ignored the page parameter" in w for w in body["warnings"]), body["warnings"]
        assert any("10 of 25" in w for w in body["warnings"]), body["warnings"]
        # Two requests, not forty: the repeat is the evidence, and one repeat is enough of it.
        asked = [q for p, q in cloudflare.queries if "pages/projects" in p]
        assert len(asked) == 2, asked


async def test_a_refused_page_never_becomes_a_silent_prefix(client_for, cloudflare) -> None:
    """The fallback is a whole read or an error, never the first slice of one.

    An endpoint that refuses pagination and then hands back part of its collection cannot be
    paged *at all*, so there is no next page to ask for. Returning what arrived would be §17's
    worst outcome: a truncation that looks exactly like a complete answer.
    """
    t = await make_tenant("cf-single-page-short")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _account(c, headers)
        cloudflare.add_zone("klant.nl")
        cloudflare.add_registration("klant.nl")
        cloudflare.single_page_total["registrar/domains"] = 7

        body = (
            await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/sync", headers=headers)
        ).json()

        # Registrar is optional, so the sync still reports its zones — with the read named as
        # the failure it is, rather than as a register holding one domain.
        assert body["registrar_read"] is False
        assert body["registrar_domains_synced"] == 0
        assert any("1 of 7" in w for w in body["warnings"]), body["warnings"]


async def test_a_bad_request_that_is_not_about_paging_still_fails(client_for, cloudflare) -> None:
    """The retry is narrow on purpose: only a 400 that names the list options earns one.

    A 400 about anything else is an honest error, and asking again without the page parameters
    would turn it into two — a second call, the same refusal, and a longer wait for it.
    """
    t = await make_tenant("cf-bad-request")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _account(c, headers)
        cloudflare.add_zone("klant.nl")
        cloudflare.fail["registrar/domains"] = (400, "Account is not entitled to Registrar", 1099)
        cloudflare.queries.clear()

        body = (
            await c.post(f"/api/v1/cloudflare/accounts/{account['id']}/sync", headers=headers)
        ).json()

        assert any("not entitled" in w for w in body["warnings"]), body["warnings"]
        assert len([q for p, q in cloudflare.queries if "registrar/domains" in p]) == 1


# --------------------------------------------------------------------------------------- #
# Editing and deleting a redirect the zone already has (#278)
# --------------------------------------------------------------------------------------- #
async def test_an_inherited_rule_is_edited_at_cloudflare_and_not_claimed(
    client_for, cloudflare
) -> None:
    """The ordinary act on a redirect an agency took over: the target is now wrong, fix it.

    Adoption could not do this — it refuses anything that is not already exactly what schakl
    would have written, which is correct for a *claim* and useless for a *change*. So the only
    routes to "this old domain points at the wrong place" were: log in to the client's Cloudflare
    dashboard, or adopt-then-save, which needs the rule to already be right before you may correct
    it. Editing names the rule by id and writes it where it lives.

    And it deliberately does **not** claim the rule. Ownership carries consequences nobody asked
    for by correcting a URL — a reconcile that recreates the rule, a delete that removes it — so
    the row stays "found at Cloudflare", one press from adoption if that is what is wanted.
    """
    t = await make_tenant("cf-edit-rule")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, domain, _ = await _connected(c, headers, cloudflare)
        zone_id = next(iter(cloudflare.zones))
        theirs = await _hand_made_rule(
            cloudflare, zone_id, apex="klant.nl", target="https://oud.nl"
        )

        edited = await c.put(
            f"/api/v1/cloudflare/domains/{domain['id']}/redirect/rules/{theirs['id']}",
            json={"target_url": "https://nieuw.nl", "status_code": 302},
            headers=headers,
        )
        assert edited.status_code == 200, edited.text

        # The rule itself moved, in place: still one rule, still the same id, new destination.
        live = cloudflare.rulesets[zone_id]["rules"]
        assert len(live) == 1 and live[0]["id"] == theirs["id"]
        assert live[0]["action_parameters"]["from_value"]["status_code"] == 302
        assert rules.rule_target(live[0]) == ("https://nieuw.nl", True)
        # Their description survives: editing somebody's rule is not signing it. Stamping the
        # marker on it would make it read in Cloudflare's dashboard as one schakl created, which
        # is the precise confusion `find_our_rule` matches on id to avoid.
        assert live[0]["description"] == "Redirect klant.nl"

        # The answer is the refreshed report, because the write just invalidated the observation
        # the caller's list was drawn from.
        body = edited.json()
        assert body["conflicts"][0]["target_url"] == "https://nieuw.nl"
        assert body["conflicts"][0]["intent"]["status_code"] == 302
        # Not claimed: no redirect of ours exists, and the row still says where it came from.
        assert body["redirect"] is None
        # The domain record follows an edited inherited redirect, exactly as a check does.
        assert body["domain_status"] == "redirect"
        assert body["domain_redirect_url"] == "https://nieuw.nl"

        # And it survives the page load, which is the whole point of storing the observation.
        stored = (
            await c.get(f"/api/v1/cloudflare/domains/{domain['id']}/status", headers=headers)
        ).json()
        assert stored["conflicts"][0]["target_url"] == "https://nieuw.nl"


async def test_editing_a_rule_never_moves_the_traffic_it_catches(client_for, cloudflare) -> None:
    """Changing where a redirect *goes* must never change what it *answers for*.

    Cloudflare's own dashboard writes ``http.host in {"klant.nl" "www.klant.nl"}`` for the
    commonest rule an agency inherits. We can read it and cannot write it, so rebuilding the
    expression from an intent would re-scope a live client's redirect as a side effect of
    correcting its URL — and the subdomain checkbox, whose value would decide that, is a control
    nobody was shown, because the rule has no whole intent to seed a form from.

    So the API answers `include_subdomains: null` for such a rule (the panel then draws no
    checkbox and says the match set is kept), and the edit carries the expression over verbatim.
    Gating the edit on a readable *intent* instead would have withheld it from exactly the rules
    this feature exists for.
    """
    t = await make_tenant("cf-edit-scope")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, domain, _ = await _connected(c, headers, cloudflare)
        zone_id = next(iter(cloudflare.zones))
        expression = 'http.host in {"klant.nl" "www.klant.nl"}'
        theirs = cloudflare.add_redirect_rule(
            zone_id,
            {
                "action": "redirect",
                "expression": expression,
                "description": "oude site",
                "action_parameters": {
                    "from_value": {
                        "status_code": 301,
                        "target_url": {"value": "https://oud.nl"},
                        "preserve_query_string": True,
                    }
                },
            },
        )

        checked = (
            await c.post(f"/api/v1/cloudflare/domains/{domain['id']}/check", headers=headers)
        ).json()
        row = checked["conflicts"][0]
        # The readings disagree on purpose: no whole intent, a perfectly readable target, and a
        # match set that is plainly the whole domain and not ours to write.
        assert row["intent"] is None
        assert row["target_url"] == "https://oud.nl"
        assert row["include_subdomains"] is None
        assert row["domain_wide"] is True
        # And every setting the edit form posts is reported **on its own**, so the form seeds from
        # the rule rather than from defaults. `intent` being None must not cost these: this rule
        # sends every URL to one page (`preserve_path` false, read off the target's shape), and a
        # form seeded from defaults would have started appending paths on the next save.
        assert row["preserve_path"] is False
        assert row["preserve_query"] is True
        assert row["status_code"] == 301

        edited = await c.put(
            f"/api/v1/cloudflare/domains/{domain['id']}/redirect/rules/{theirs['id']}",
            json={
                "target_url": "https://nieuw.nl",
                # Posted back exactly as the report gave them — the round trip a screen makes.
                "status_code": row["status_code"],
                "preserve_path": row["preserve_path"],
                "preserve_query": row["preserve_query"],
                # A value for the field the panel does *not* draw here. It must be ignored, not
                # obeyed: obeying it would narrow a rule that answers for www to the apex alone.
                "include_subdomains": False,
            },
            headers=headers,
        )
        assert edited.status_code == 200, edited.text

        live = cloudflare.rulesets[zone_id]["rules"][0]
        assert live["expression"] == expression
        # Only the destination moved: still a plain value, so still "send everything to one page".
        assert rules.rule_target(live) == ("https://nieuw.nl", False)
        assert live["action_parameters"]["from_value"]["target_url"] == {
            "value": "https://nieuw.nl"
        }


async def test_a_status_code_we_cannot_write_costs_the_intent_and_nothing_else(
    client_for, cloudflare
) -> None:
    """A 303 is a redirect Cloudflare holds and schakl cannot express.

    It costs adoption, which is honest — writing that rule back is the one thing we genuinely
    cannot do. It must cost nothing else: the row still describes itself, still counts as this
    domain redirecting, and still reports every setting an edit form seeds from. Reading those off
    the (absent) intent instead would have filled the form with defaults and quietly rewritten a
    live client's 303 as a 301 on the next save.
    """
    t = await make_tenant("cf-odd-code")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, domain, _ = await _connected(c, headers, cloudflare)
        zone_id = next(iter(cloudflare.zones))
        body = rules.build_rule(
            apex="klant.nl",
            target_url="https://nieuw.nl",
            status_code=301,
            preserve_path=True,
            preserve_query=True,
            include_subdomains=True,
        )
        body["action_parameters"]["from_value"]["status_code"] = 303
        cloudflare.add_redirect_rule(zone_id, body)

        checked = (
            await c.post(f"/api/v1/cloudflare/domains/{domain['id']}/check", headers=headers)
        ).json()
        row = checked["conflicts"][0]
        assert row["intent"] is None
        assert row["status_code"] == 303
        # The expression is one of ours, so the match set *is* rewritable — which the whole intent
        # could never have said, since the status code had already taken it away.
        assert row["include_subdomains"] is True
        assert row["preserve_path"] is True
        assert row["domain_wide"] is True


async def test_deleting_an_inherited_rule_removes_it_and_walks_the_domain_back(
    client_for, cloudflare
) -> None:
    """The button that was missing from every row an agency inherited.

    ``DELETE .../redirect`` only ever removes the rule whose id we stored, which is right for a
    route that names no rule — but it left "this old redirect should be gone" as a job for
    Cloudflare's dashboard, which is the thing this module exists to stop people needing. The
    safety property is not "only our own rows" but "only a rule the caller pointed at, resolved
    inside this zone's own ruleset".
    """
    t = await make_tenant("cf-delete-rule")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, domain, _ = await _connected(c, headers, cloudflare)
        zone_id = next(iter(cloudflare.zones))
        theirs = await _hand_made_rule(
            cloudflare, zone_id, apex="klant.nl", target="https://nieuw.nl"
        )

        # A check first, so the domain record is carrying the observation this must walk back.
        checked = (
            await c.post(f"/api/v1/cloudflare/domains/{domain['id']}/check", headers=headers)
        ).json()
        assert checked["domain_status"] == "redirect"

        removed = await c.delete(
            f"/api/v1/cloudflare/domains/{domain['id']}/redirect/rules/{theirs['id']}",
            headers=headers,
        )
        assert removed.status_code == 200, removed.text

        assert cloudflare.rulesets[zone_id]["rules"] == []
        body = removed.json()
        assert body["conflicts"] == []
        # A flag that only ever turns on is half a mechanism: the domain goes back to active,
        # because it still says exactly what the observation put there.
        assert body["domain_status"] == "active"
        assert body["domain_redirect_url"] is None


async def test_deleting_our_own_rule_by_id_forgets_the_row_too(client_for, cloudflare) -> None:
    """Reached from the list, our own rule is still ours: the row goes with it.

    Two delete paths that disagreed about this would leave a stored redirect pointing at a rule
    that no longer exists — read as ``missing`` forever, and recreated by the next save.
    """
    t = await make_tenant("cf-delete-own")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, domain, _ = await _connected(c, headers, cloudflare)
        zone_id = next(iter(cloudflare.zones))
        saved = await c.put(
            f"/api/v1/cloudflare/domains/{domain['id']}/redirect",
            json={"target_url": "https://nieuw.nl", "status_code": 301},
            headers=headers,
        )
        assert saved.status_code == 200, saved.text
        rule_id = cloudflare.rulesets[zone_id]["rules"][0]["id"]

        removed = await c.delete(
            f"/api/v1/cloudflare/domains/{domain['id']}/redirect/rules/{rule_id}",
            headers=headers,
        )
        assert removed.status_code == 200, removed.text
        assert cloudflare.rulesets[zone_id]["rules"] == []
        body = removed.json()
        assert body["redirect"] is None
        assert body["domain_status"] == "active"


async def test_a_rule_id_that_names_nothing_on_this_zone_is_a_404(client_for, cloudflare) -> None:
    """The id comes from outside, so it is only ever resolved inside this zone's own ruleset.

    A rule id belonging to another zone — or to another tenant, or to some other product of
    Cloudflare's rules engine — must resolve to nothing rather than to a call. This is also the
    ordinary race: the rule was deleted in the dashboard between the page load and the press.
    """
    t = await make_tenant("cf-rule-404")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, domain, _ = await _connected(c, headers, cloudflare)
        zone_id = next(iter(cloudflare.zones))
        await _hand_made_rule(cloudflare, zone_id, apex="klant.nl", target="https://nieuw.nl")

        for call in (
            c.put(
                f"/api/v1/cloudflare/domains/{domain['id']}/redirect/rules/rule-elders",
                json={"target_url": "https://nieuw.nl"},
                headers=headers,
            ),
            c.delete(
                f"/api/v1/cloudflare/domains/{domain['id']}/redirect/rules/rule-elders",
                headers=headers,
            ),
        ):
            res = await call
            assert res.status_code == 404, res.text
        # Refused means refused: the rule that *is* there is untouched.
        assert len(cloudflare.rulesets[zone_id]["rules"]) == 1


def test_an_edit_rebuilds_the_action_and_keeps_a_match_set_it_cannot_write() -> None:
    """``edited_rule`` is ``build_rule`` with one refusal, and the refusal is the feature.

    Asserted on the two shapes that decide it: one of ours, which is rebuilt whole so the
    subdomain toggle works, and one of Cloudflare's, which is carried over so correcting a URL
    cannot silently re-scope the rule.
    """
    ours = rules.build_rule(
        apex="klant.nl",
        target_url="https://oud.nl",
        status_code=301,
        preserve_path=True,
        preserve_query=True,
        include_subdomains=True,
    )
    narrowed = rules.edited_rule(
        ours,
        apex="klant.nl",
        target_url="https://nieuw.nl",
        status_code=302,
        preserve_path=False,
        preserve_query=False,
        include_subdomains=False,
    )
    assert rules.rule_intent(narrowed, "klant.nl") == {
        "target_url": "https://nieuw.nl",
        "status_code": 302,
        "preserve_path": False,
        "preserve_query": False,
        "include_subdomains": False,
    }

    theirs = {**ours, "expression": 'http.host in {"klant.nl" "www.klant.nl"}', "enabled": False}
    kept = rules.edited_rule(
        theirs,
        apex="klant.nl",
        target_url="https://nieuw.nl",
        status_code=301,
        preserve_path=True,
        preserve_query=True,
        include_subdomains=False,
    )
    assert kept["expression"] == 'http.host in {"klant.nl" "www.klant.nl"}'
    assert rules.rule_target(kept) == ("https://nieuw.nl", True)
    # A rule somebody disabled on purpose stays disabled: an edit changes a destination, not a
    # switch.
    assert kept["enabled"] is False
    # And nothing to keep, with nothing of ours to put there, is refused rather than guessed.
    assert rules.edited_rule(
        {**theirs, "expression": ""},
        apex="klant.nl",
        target_url="https://nieuw.nl",
        status_code=301,
        preserve_path=True,
        preserve_query=True,
        include_subdomains=True,
    ) is None
