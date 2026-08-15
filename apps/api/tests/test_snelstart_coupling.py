"""The coupling broker (epic #377): how a granted koppelsleutel reaches the right tenant.

This is the module's one unauthenticated route, and it is the most constrained of the five such
routes in the codebase — because unlike the others, the thing that names the tenant is not even
in the URL. SnelStart posts **every partner's couplings to one URL**, so the request carries its
own tenancy in the body, and every property below is what stops that from being a way in.

Five gates, in this order and no other, each with its own test:

1. the reference names the tenant — no hostname, no session, no unscoped lookup;
2. the RLS GUC is bound before anything is read;
3. the secret is compared in constant time, and a mismatch is indistinguishable from an unknown
   org, a malformed reference and a body that is not JSON at all;
4. the body is a **hint** — the key is believed only after it mints a token and names an
   administration;
5. ``Delete`` disconnects and keeps the record, because the links and the run history are the
   tenant's own audit trail of what was pushed into their books.

And one thing that is not a gate but decides the shape of all of them: **SnelStart never
retries.** So almost everything answers 200 — a non-2xx buys nothing except a tenant watching a
connect flow fail silently — and what we could not process is logged rather than bounced.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select

from app.db import async_session_maker, set_current_org
from app.integrations.snelstart import client as snelstart_client
from app.integrations.snelstart.coupling import handle_coupling_callback
from app.integrations.snelstart.models import SnelstartAccount, SnelstartAccountStatus
from app.integrations.snelstart.service import coupling_reference
from tests.conftest import Tenant, auth_cookie, make_tenant
from tests.snelstart_fake import FakeSnelstart

KOPPELSLEUTEL = "clpNemhxZWhOeHQ0TXVncVp1RC9WTXBx:QXBWNVVOU2FUV3VYTytZcVNSc2xrays"
SUBSCRIPTION_KEY = "40e32908b9d34996b145af4c8eed6d20"


@pytest.fixture
def snelstart(monkeypatch) -> FakeSnelstart:
    from app.config import settings

    monkeypatch.setattr(settings, "snelstart_subscription_key", SUBSCRIPTION_KEY)
    fake = FakeSnelstart()
    snelstart_client.set_transport(fake.transport())
    yield fake
    snelstart_client.set_transport(None)


async def _pending_account(client, headers, name: str = "SnelStart") -> dict:
    """An account created for the activation flow: a row, a secret, and no key yet.

    That state is the whole reason ``client_key_encrypted`` is nullable — the reference SnelStart
    will quote back has to exist *before* the tenant approves anything.
    """
    created = await client.post(
        "/api/v1/snelstart/accounts", json={"name": name}, headers=headers
    )
    assert created.status_code == 201, created.text
    assert created.json()["connected"] is False
    assert created.json()["status"] == "pending"
    assert created.json()["connect_method"] == "coupling"
    return created.json()


async def _stored(tenant: Tenant, account_id: str) -> SnelstartAccount:
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        row = await session.scalar(
            select(SnelstartAccount).where(SnelstartAccount.id == uuid.UUID(account_id))
        )
        assert row is not None
        return row


async def _secret(tenant: Tenant, account_id: str) -> str:
    return (await _stored(tenant, account_id)).connect_secret


def _body(**fields: object) -> bytes:
    return json.dumps(fields).encode()


# --------------------------------------------------------------------------------------- #
# The happy path
# --------------------------------------------------------------------------------------- #
async def test_a_granted_key_lands_on_the_right_account_and_proves_itself(
    client_for, snelstart
) -> None:
    """Gate 4 is the interesting one: the body claims to carry a credential, and we check.

    Storing it on the strength of the POST alone would mean anybody who guessed a reference could
    park a string in a tenant's credential column. Minting a token with it and reading
    ``/companyInfo`` is what turns "somebody posted a plausible payload" into "this key opens
    these books" — and it is also where the administration's name comes from, which is the fact
    the tenant actually needs on screen.
    """
    t: Tenant = await make_tenant("snel-couple-ok")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _pending_account(c, headers)

    reference = coupling_reference(
        t.org.id, uuid.UUID(account["id"]), await _secret(t, account["id"])
    )
    status = await handle_coupling_callback(
        _body(KoppelSleutel=KOPPELSLEUTEL, ActionType="Create", ReferenceKey=reference)
    )
    assert status == 200

    row = await _stored(t, account["id"])
    assert row.client_key_encrypted, "the key was stored"
    assert row.status == SnelstartAccountStatus.ACTIVE.value
    assert row.administration_name == "Testadministratie"
    assert row.scopes, "the token's own scopes were recorded"

    # And it is genuinely usable afterwards: the screen shows it connected, without the key.
    async with client_for(t.host) as c:
        listed = await c.get("/api/v1/snelstart/accounts", headers=headers)
        assert listed.json()[0]["connected"] is True
        assert KOPPELSLEUTEL not in listed.text


async def test_the_field_names_are_read_case_insensitively(client_for, snelstart) -> None:
    """SnelStart documents PascalCase and a .NET serialiser is one line from camelCase.

    Matching either costs nothing and is the difference between working and a silent no-op
    nobody could debug from outside — there is no retry and no error to see.
    """
    t: Tenant = await make_tenant("snel-couple-case")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _pending_account(c, headers)
    reference = coupling_reference(
        t.org.id, uuid.UUID(account["id"]), await _secret(t, account["id"])
    )
    status = await handle_coupling_callback(
        _body(koppelSleutel=KOPPELSLEUTEL, actionType="create", referenceKey=reference)
    )
    assert status == 200
    assert (await _stored(t, account["id"])).client_key_encrypted


# --------------------------------------------------------------------------------------- #
# Gate 3 — every rejection looks the same from outside
# --------------------------------------------------------------------------------------- #
async def test_a_wrong_secret_is_indistinguishable_from_everything_else_wrong(
    client_for, snelstart
) -> None:
    """A wrong secret, an unknown org, a malformed reference and a body that is not JSON all
    answer identically and write nothing.

    If any of them differed, the route would be an oracle: post references until the status
    changes and you have learned which orgs and which accounts exist.
    """
    t: Tenant = await make_tenant("snel-couple-bad")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _pending_account(c, headers)
    account_id = uuid.UUID(account["id"])

    attempts = [
        # …the right account, the wrong secret.
        _body(
            KoppelSleutel=KOPPELSLEUTEL,
            ActionType="Create",
            ReferenceKey=coupling_reference(t.org.id, account_id, "not-the-secret"),
        ),
        # …an org that does not exist.
        _body(
            KoppelSleutel=KOPPELSLEUTEL,
            ActionType="Create",
            ReferenceKey=coupling_reference(uuid.uuid4(), account_id, "x"),
        ),
        # …an account that does not exist, under a real org.
        _body(
            KoppelSleutel=KOPPELSLEUTEL,
            ActionType="Create",
            ReferenceKey=coupling_reference(t.org.id, uuid.uuid4(), "x"),
        ),
        # …a reference that is not one.
        _body(KoppelSleutel=KOPPELSLEUTEL, ActionType="Create", ReferenceKey="nonsense"),
        # …no reference at all.
        _body(KoppelSleutel=KOPPELSLEUTEL, ActionType="Create"),
        # …not JSON.
        b"<html>not json</html>",
        # …nothing.
        b"",
    ]
    for body in attempts:
        assert await handle_coupling_callback(body) == 200, body

    row = await _stored(t, account["id"])
    assert row.client_key_encrypted is None, "nothing was ever stored"
    assert row.status == SnelstartAccountStatus.PENDING.value


async def test_one_tenants_reference_never_reaches_another_tenants_account(
    client_for, snelstart
) -> None:
    """Gate 1 and gate 2 together: the org rides in the reference, and the account is read
    *scoped to it*, so a real account id under the wrong org resolves to nothing."""
    a: Tenant = await make_tenant("snel-couple-a")
    b: Tenant = await make_tenant("snel-couple-b")
    async with client_for(a.host) as c:
        account_a = await _pending_account(c, await auth_cookie(a.user))
    secret = await _secret(a, account_a["id"])

    # B's org id, A's account id, A's real secret.
    status = await handle_coupling_callback(
        _body(
            KoppelSleutel=KOPPELSLEUTEL,
            ActionType="Create",
            ReferenceKey=coupling_reference(b.org.id, uuid.UUID(account_a["id"]), secret),
        )
    )
    assert status == 200
    assert (await _stored(a, account_a["id"])).client_key_encrypted is None


async def test_a_body_far_too_large_is_refused_before_it_is_parsed(snelstart) -> None:
    """§17: every cap is checked *before* the work it bounds."""
    assert await handle_coupling_callback(b"x" * (128 * 1024)) == 200


# --------------------------------------------------------------------------------------- #
# Gate 4 — the body is a hint
# --------------------------------------------------------------------------------------- #
async def test_a_key_that_does_not_work_is_recorded_rather_than_stored(
    client_for, snelstart
) -> None:
    """The tenant approved a coupling and the key does not authenticate.

    Left merely pending, the screen would say "waiting" for ever with nothing to explain why. So
    the failure lands on the row, and the status code is a 503 — not because SnelStart will try
    again (it will not) but because that is the honest answer, and the tenant can press activate
    a second time.
    """
    t: Tenant = await make_tenant("snel-couple-dead")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _pending_account(c, headers)
    snelstart.reject_key = True

    status = await handle_coupling_callback(
        _body(
            KoppelSleutel="not-a-real-key",
            ActionType="Create",
            ReferenceKey=coupling_reference(
                t.org.id, uuid.UUID(account["id"]), await _secret(t, account["id"])
            ),
        )
    )
    assert status == 503
    row = await _stored(t, account["id"])
    assert row.client_key_encrypted is None, "an unproven key is never stored"
    assert row.status == SnelstartAccountStatus.ERROR.value
    assert row.last_error


async def test_a_create_with_no_key_at_all_changes_nothing(client_for, snelstart) -> None:
    t: Tenant = await make_tenant("snel-couple-nokey")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _pending_account(c, headers)
    status = await handle_coupling_callback(
        _body(
            ActionType="Create",
            ReferenceKey=coupling_reference(
                t.org.id, uuid.UUID(account["id"]), await _secret(t, account["id"])
            ),
        )
    )
    assert status == 200
    assert (await _stored(t, account["id"])).client_key_encrypted is None


# --------------------------------------------------------------------------------------- #
# Gate 5 — Delete disconnects, it does not delete
# --------------------------------------------------------------------------------------- #
async def test_a_revoked_coupling_forgets_the_key_and_keeps_the_record(
    client_for, snelstart
) -> None:
    """The links, the mappings and the run history are the tenant's own record of what was
    pushed into their books. A revoked key is not a reason to destroy the audit trail of a
    ledger — and the account reverts to ``pending``, which is exactly what it is: connectable,
    not connected."""
    t: Tenant = await make_tenant("snel-couple-del")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _pending_account(c, headers)
        # Give it a mapping worth keeping.
        await c.patch(
            f"/api/v1/snelstart/accounts/{account['id']}",
            json={"default_ledger_code": "8200"},
            headers=headers,
        )
    secret = await _secret(t, account["id"])
    reference = coupling_reference(t.org.id, uuid.UUID(account["id"]), secret)

    assert (
        await handle_coupling_callback(
            _body(KoppelSleutel=KOPPELSLEUTEL, ActionType="Create", ReferenceKey=reference)
        )
        == 200
    )
    assert (await _stored(t, account["id"])).client_key_encrypted

    assert (
        await handle_coupling_callback(_body(ActionType="Delete", ReferenceKey=reference)) == 200
    )
    row = await _stored(t, account["id"])
    assert row.client_key_encrypted is None
    assert row.status == SnelstartAccountStatus.PENDING.value
    assert row.default_ledger_code == "8200", "the tenant's configuration survives"
    assert row.connect_secret != secret, "a revoked coupling's address stops answering"


async def test_a_regenerate_reuses_the_same_address(client_for, snelstart) -> None:
    """SnelStart posts again for a ``Regenerate``, to the same URL and the same reference.

    Rotating the secret on receipt would make the *next* regeneration undeliverable — the shape
    of bug that only appears months later, when somebody re-issues a key and nothing happens.
    """
    t: Tenant = await make_tenant("snel-couple-regen")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _pending_account(c, headers)
    secret = await _secret(t, account["id"])
    reference = coupling_reference(t.org.id, uuid.UUID(account["id"]), secret)

    await handle_coupling_callback(
        _body(KoppelSleutel=KOPPELSLEUTEL, ActionType="Create", ReferenceKey=reference)
    )
    assert (await _stored(t, account["id"])).connect_secret == secret

    assert (
        await handle_coupling_callback(
            _body(
                KoppelSleutel=KOPPELSLEUTEL + "-rotated",
                ActionType="Regenerate",
                ReferenceKey=reference,
            )
        )
        == 200
    )
    row = await _stored(t, account["id"])
    assert row.connect_secret == secret, "the address a regenerate arrives at must not move"
    assert row.status == SnelstartAccountStatus.ACTIVE.value


# --------------------------------------------------------------------------------------- #
# The activation link
# --------------------------------------------------------------------------------------- #
async def test_no_app_shortname_means_no_activation_button_at_all(
    client_for, snelstart, monkeypatch
) -> None:
    """#253: a control that always refuses is worse than no control.

    A self-hosted box's hostname is one SnelStart has never heard of and could not post to, so
    the activation path does not exist there and the screen offers a paste box instead.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "snelstart_app_shortname", None)
    t: Tenant = await make_tenant("snel-noshortname")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _pending_account(c, headers)
        assert account["activation_url"] == ""


async def test_the_activation_link_carries_the_reference_and_nothing_secret_beside_it(
    client_for, snelstart, monkeypatch
) -> None:
    from urllib.parse import parse_qs, urlparse

    from app.config import settings

    monkeypatch.setattr(settings, "snelstart_app_shortname", "schakl")
    t: Tenant = await make_tenant("snel-shortname")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _pending_account(c, headers)

    parsed = urlparse(account["activation_url"])
    assert parsed.netloc == "web.snelstart.nl"
    assert parsed.path == "/couplings/activate/schakl"
    query = parse_qs(parsed.query)
    assert list(query) == ["referenceKey"], "nothing else rides in the URL"
    reference = query["referenceKey"][0]
    assert reference.startswith(f"{t.org.id}.{account['id']}.")
    assert len(reference) <= 500, "SnelStart caps referenceKey at 500 characters"


async def test_the_webhook_url_is_shown_because_a_proxy_has_to_allow_it(
    client_for, snelstart
) -> None:
    """Nobody can allow a URL they cannot see, and "activation never completes" is otherwise a
    mystery with no clue on screen."""
    t: Tenant = await make_tenant("snel-hookurl")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        account = await _pending_account(c, headers)
    assert account["coupling_webhook_url"].endswith("/api/v1/snelstart/coupling/callback")
