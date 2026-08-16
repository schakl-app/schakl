"""mollie module (epic #269, issue #267): the credential, and nothing else.

What this module owns is *a credential and a conversation*; where the money goes is
``test_invoicing_payments.py``. Four properties carry the weight here, and each has a test whose
only job is to keep it true:

* **The API key goes in and never comes back out.** It is Fernet at rest, read exactly once, and
  absent from every response shape — so the assertion is made against the raw response *text*
  rather than a parsed field, because the failure being guarded is a field nobody thought about.
* **Verify never raises.** ``require_context`` rolls the session back on any exception, so a
  verify that raised would discard the very row recording what Mollie said. A rejected key
  answers ``200`` with ``ok=false``, and the row keeps ``status="error"`` — asserted through a
  **second request**, which is the only way to tell a row that was written from one that was
  written and rolled back.
* **The mode follows the key, always.** Mollie's keys are self-typed, so ``mode`` is derived and
  cannot be entered. Getting this wrong means an agency believes it is taking money it is not.
* **Rotating the key rotates the callback URL.** A key is usually replaced *because* it leaked,
  and the webhook secret lives beside it; leaving the old URL answering would keep one half of a
  compromised pair alive.
"""

from __future__ import annotations

import pytest

from app.integrations.mollie import client as mollie_client
from app.registry import registry
from tests.conftest import auth_cookie, make_tenant
from tests.mollie_fake import FakeMollie

#: Shaped like the real thing (``live_``/``test_`` + 30 alphanumerics) because
#: ``MollieAccountCreate`` refuses anything that is not — a paste of the wrong secret is worth
#: catching before it is encrypted, stored and then reported as "Mollie rejected your key".
LIVE_KEY = "live_JhRk9NcQdTzWbV4pM2sXgY7eF3uL5aKq"
TEST_KEY = "test_2sXgY7eF3uL5aKqJhRk9NcQdTzWbV4pM"


@pytest.fixture
def mollie() -> FakeMollie:
    """A Mollie that holds state, installed as the module's only transport.

    Unset, ``client._transport`` is ``None`` and a forgotten stub fails loudly on connect rather
    than reaching the real api.mollie.com — which is the whole reason the seam exists.
    """
    fake = FakeMollie()
    mollie_client.set_transport(fake.transport())
    yield fake
    mollie_client.set_transport(None)


# --------------------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------------------- #
async def _account(c, headers, *, api_key: str = LIVE_KEY, name: str = "Mollie", **extra) -> dict:
    res = await c.post(
        "/api/v1/mollie/accounts",
        json={"name": name, "api_key": api_key, **extra},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()


async def _row(c, headers, account_id: str) -> dict:
    """The stored row, read back in its own request — see the module docstring."""
    listed = await c.get("/api/v1/mollie/accounts", headers=headers)
    assert listed.status_code == 200, listed.text
    match = next((row for row in listed.json() if row["id"] == account_id), None)
    assert match is not None, listed.text
    return match


# --------------------------------------------------------------------------------------- #
# Module wiring
# --------------------------------------------------------------------------------------- #
def test_mollie_module_is_licensed_and_never_reaches_a_client() -> None:
    """The commercial boundary is the sku on the descriptor (issue #137); the safety boundary is
    that its one key is never a client's (#267, CLAUDE.md §15).

    The single-key assertion is deliberately exact rather than a subset check: the temptation
    this module resisted was minting a ``mollie.payment.*`` key beside it, which would mean an
    agency granting two permissions to let a bookkeeper do one thing — and three once a second
    provider ships. Starting a payment declares ``invoicing.payment.link``, and that is the only
    arrangement in which a client can pay their own invoice at all.
    """
    module = registry.get("mollie")
    assert module is not None and module.sku == "mollie"
    assert {p.key for p in module.permissions} == {"mollie.settings.manage"}
    assert all("client" not in p.default_roles for p in module.permissions)
    assert all("client" not in p.default_own_roles for p in module.permissions)


# --------------------------------------------------------------------------------------- #
# The credential
# --------------------------------------------------------------------------------------- #
async def test_the_api_key_is_never_echoed_in_any_response(client_for, mollie) -> None:
    """Create, list, patch, verify: the key is in none of them, and the row says only that one
    is stored. Asserted on the raw text, so a field added later that happens to carry it fails
    here rather than in production."""
    mollie.require_key(LIVE_KEY)
    t = await make_tenant("mollie-secret")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        created = await c.post(
            "/api/v1/mollie/accounts",
            json={"name": "Mollie — Breik", "api_key": LIVE_KEY},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        assert LIVE_KEY not in created.text
        assert "api_key" not in {k for k in created.json() if k != "api_key_configured"}
        assert created.json()["api_key_configured"] is True
        account_id = created.json()["id"]

        listed = await c.get("/api/v1/mollie/accounts", headers=headers)
        assert listed.status_code == 200, listed.text
        assert LIVE_KEY not in listed.text

        renamed = await c.patch(
            f"/api/v1/mollie/accounts/{account_id}",
            json={"name": "Mollie hoofdaccount"},
            headers=headers,
        )
        assert renamed.status_code == 200, renamed.text
        assert LIVE_KEY not in renamed.text

        verified = await c.post(
            f"/api/v1/mollie/accounts/{account_id}/verify", headers=headers
        )
        assert verified.status_code == 200, verified.text
        assert LIVE_KEY not in verified.text
        # The rename did not blank the credential: the stored one still authenticates against
        # a fake that refuses anything else.
        assert verified.json()["ok"] is True, verified.text
        assert verified.json()["methods"] == list(mollie.methods)


async def test_the_fake_never_records_the_api_key(client_for, mollie) -> None:
    """A harness that logged the request headers would put the tenant's Mollie key in every
    pytest failure output — the leak ``redact`` exists to prevent, reintroduced one layer down."""
    t = await make_tenant("mollie-nolog")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _account(c, headers)
        assert (
            await c.post(f"/api/v1/mollie/accounts/{account['id']}/verify", headers=headers)
        ).json()["ok"] is True

    assert mollie.calls, "the verify should have called Mollie"
    assert LIVE_KEY not in repr(mollie.calls)
    assert "Authorization" not in repr(mollie.calls)
    for method, path, body in mollie.calls:
        assert "://" not in path  # a path, never a URL
        assert LIVE_KEY not in repr(body)
        assert method in ("GET", "POST")


async def test_a_rejected_key_answers_ok_false_and_the_row_survives_the_request(
    client_for, mollie
) -> None:
    """The rule the whole module bends around: **verify never raises.**

    ``require_context`` rolls the session back on any exception, so a raising verify would throw
    away the record of its own failed probe — the screen would then show a credential that looks
    untested rather than one Mollie has refused. The failure is read back in a *second* request,
    because that is the only way to tell a written row from a written-and-rolled-back one.
    """
    mollie.fail(
        "methods",
        status=401,
        title="Unauthorized Request",
        detail="Missing authentication, or failed to authenticate",
    )
    t = await make_tenant("mollie-refused")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _account(c, headers)
        verified = await c.post(
            f"/api/v1/mollie/accounts/{account['id']}/verify", headers=headers
        )
        assert verified.status_code == 200, verified.text
        assert verified.json()["ok"] is False
        # Mollie's own untranslatable words, never an i18n key (§9).
        assert "authenticate" in verified.json()["error"]

        stored = await _row(c, headers, account["id"])
        assert stored["status"] == "error"
        assert "authenticate" in stored["last_error"]
        # An auth failure invalidates what the credential vouched for; a stale method list
        # would keep a rejected key looking usable.
        assert stored["methods"] == []
        assert stored["last_verified_at"] is not None

        # And it recovers: the row is still the same row, and a working credential clears it.
        mollie.recover("methods")
        again = await c.post(
            f"/api/v1/mollie/accounts/{account['id']}/verify", headers=headers
        )
        assert again.json()["ok"] is True, again.text
        recovered = await _row(c, headers, account["id"])
        assert recovered["status"] == "active" and recovered["last_error"] is None
        assert recovered["methods"] == list(mollie.methods)


async def test_rotating_the_key_clears_what_the_old_one_vouched_for_and_moves_the_callback(
    client_for, mollie
) -> None:
    """A rotation invalidates two things at once, and forgetting either is a real failure.

    The **observations** (``methods``, ``last_verified_at``) belonged to the old credential: a
    stale "verified" badge vouching for a key nobody has tested is exactly the screen an agency
    should not be shown. And the **callback URL** moves, because a key is usually rotated after
    it leaked and the webhook secret lives beside it — leaving the old URL answering would keep
    one half of a compromised pair alive. Only the secret moves: the org and the account are
    still the same row, which is what the split assertion below says.
    """
    t = await make_tenant("mollie-rotate")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _account(c, headers, api_key=TEST_KEY)
        assert (
            await c.post(f"/api/v1/mollie/accounts/{account['id']}/verify", headers=headers)
        ).json()["ok"] is True
        before = await _row(c, headers, account["id"])
        assert before["mode"] == "test"
        assert before["methods"] == list(mollie.methods)
        assert before["last_verified_at"] is not None

        rotated = await c.patch(
            f"/api/v1/mollie/accounts/{account['id']}",
            json={"api_key": LIVE_KEY},
            headers=headers,
        )
        assert rotated.status_code == 200, rotated.text
        after = await _row(c, headers, account["id"])
        assert after["methods"] == []
        assert after["last_verified_at"] is None
        assert after["mode"] == "live"
        assert after["webhook_url"] != before["webhook_url"]

        old_token = before["webhook_url"].rsplit("/", 1)[1]
        new_token = after["webhook_url"].rsplit("/", 1)[1]
        # ``{org}.{account}.{secret}``: the first two are the same row, the third is not.
        assert old_token.split(".")[:2] == new_token.split(".")[:2]
        assert old_token.split(".", 2)[2] != new_token.split(".", 2)[2]


async def test_the_mode_follows_the_key_and_cannot_be_entered(client_for, mollie) -> None:
    """Mollie's keys are self-typed and its two worlds are fully isolated, so ``mode`` is read
    off the prefix rather than asked for. The last assertion is the one that matters: a body
    claiming ``mode: live`` beside a ``test_`` key changes nothing, because there is no such
    field to send — a field an admin can get wrong about money is a field that should not exist.
    """
    t = await make_tenant("mollie-mode")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        live = await _account(c, headers, api_key=LIVE_KEY, name="Mollie live")
        assert live["mode"] == "live"

        sandbox = await c.post(
            "/api/v1/mollie/accounts",
            json={"name": "Mollie test", "api_key": TEST_KEY, "mode": "live"},
            headers=headers,
        )
        assert sandbox.status_code == 201, sandbox.text
        assert sandbox.json()["mode"] == "test"

        # And a key that is not a Mollie key at all never reaches the encryption at all.
        refused = await c.post(
            "/api/v1/mollie/accounts",
            json={"name": "Verkeerd geplakt", "api_key": "sk_live_stripe_looking_secret"},
            headers=headers,
        )
        assert refused.status_code == 422, refused.text


# --------------------------------------------------------------------------------------- #
# Tenant isolation (CLAUDE.md §9 — required per module)
# --------------------------------------------------------------------------------------- #
async def test_accounts_are_tenant_isolated(client_for, mollie) -> None:
    """Golden Rule 1 against the highest-blast-radius row this module owns: the credential that
    collects an agency's money.

    **404, never 403** on every by-id route: a 403 confirms the account exists, which for a row
    addressed by a guessable uuid is a different leak wearing an authorization answer's clothes.
    """
    a = await make_tenant("mollie-iso-a")
    b = await make_tenant("mollie-iso-b")
    a_headers, b_headers = await auth_cookie(a.user), await auth_cookie(b.user)
    async with client_for(a.host) as ca:
        account = await _account(ca, a_headers)
    async with client_for(b.host) as cb:
        assert (await cb.get("/api/v1/mollie/accounts", headers=b_headers)).json() == []
        assert (
            await cb.patch(
                f"/api/v1/mollie/accounts/{account['id']}",
                json={"name": "gestolen"},
                headers=b_headers,
            )
        ).status_code == 404
        assert (
            await cb.post(
                f"/api/v1/mollie/accounts/{account['id']}/verify", headers=b_headers
            )
        ).status_code == 404
        assert (
            await cb.delete(f"/api/v1/mollie/accounts/{account['id']}", headers=b_headers)
        ).status_code == 404

    # …and A still has it: a refused cross-tenant delete must not have half-succeeded.
    async with client_for(a.host) as ca:
        assert len((await ca.get("/api/v1/mollie/accounts", headers=a_headers)).json()) == 1
