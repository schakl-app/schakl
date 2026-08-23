"""The ``timeon`` surface: the credential, the permissions, and what the module declares.

Separate from ``test_timeon_sync.py`` because these are claims about the *shape* — which keys
exist, what they default to, what a route refuses — rather than about what a sync does. Both
kinds break for different reasons and a failure in one should not obscure the other.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.integrations.timeon import client as timeon_client
from app.integrations.timeon import module
from app.integrations.timeon.client import month_windows
from app.integrations.timeon.mapping import (
    UNRESOLVED,
    Resolver,
    differences,
    fingerprint,
    neutral_from_row,
    plan_start_seconds,
    timeon_payload,
)
from app.registry import KIND_INTEGRATION
from tests.conftest import auth_cookie, make_tenant
from tests.timeon_fake import FakeTimeon


@pytest.fixture
def timeon() -> FakeTimeon:
    fake = FakeTimeon()
    timeon_client.set_transport(fake.transport())
    yield fake
    timeon_client.set_transport(None)


# --------------------------------------------------------------------------------------- #
# What the module declares
# --------------------------------------------------------------------------------------- #
def test_it_is_an_integration_that_needs_only_time() -> None:
    """§6a: a conversation with somebody else's service, and ``requires`` names only what it has
    nowhere to put its data without.

    ``projects`` is deliberately absent. Pairing projects is *better* with that module and is not
    impossible without it — an hour books onto a client — and over-declaring makes a tenant switch
    on a module they did not want, which is the failure direction this field must not have.
    """
    assert module.kind == KIND_INTEGRATION
    assert module.requires == ("time",)
    assert module.sku == "timeon"


def test_the_three_permissions_are_admin_only_and_never_a_clients() -> None:
    """A portal login that could read which projects an agency tracks in somebody else's tool is
    not a smaller version of this feature."""
    keys = {spec.key for spec in module.permissions}
    assert keys == {"timeon.settings.manage", "timeon.sync.run", "timeon.sync.write"}
    assert all("client" not in spec.default_roles for spec in module.permissions)
    assert all("client" not in spec.default_own_roles for spec in module.permissions)
    assert all(spec.default_roles == ("admin",) for spec in module.permissions)


def test_this_integration_draws_nothing_on_the_company_hub() -> None:
    """No company panel, and the loss is deliberate (#411, docs/TIMEON.md §9a).

    It used to declare one, gated on ``timeon.sync.run`` — never ``time.entry.read``, which a
    client-portal login may hold at ``:own`` (#266). The card carried this client's pairing
    count and their open conflicts, and unlike the Ads and Tag Manager cards removed beside it,
    **nothing takes its place**: a cutover ends, and a card on every client's page for a
    migration with a stated end date is a card that outlives its reason.

    Asserted rather than left implicit, because the way this comes back is somebody restoring
    the panel for a good reason and not re-reading why it went — at which point it needs a
    permission again, and #365 says a provider declaring none is a build break."""
    assert module.panels == []


# --------------------------------------------------------------------------------------- #
# The credential surface
# --------------------------------------------------------------------------------------- #
async def test_a_member_holding_nothing_cannot_reach_any_of_it(client_for) -> None:
    """The behavioural half of deny-by-default (§15). The sweep in
    ``test_rbac_deny_by_default`` covers every route; this names the two that matter most."""
    from app.core.permissions.service import create_membership, replace_permissions, role_by_key
    from app.db import async_session_maker, set_current_org

    tenant = await make_tenant("timeonrbac")
    other = await make_tenant("timeonrbac2", email="nobody@example.com")
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        role = await role_by_key(session, tenant.org.id, "member")
        await replace_permissions(session, tenant.org.id, role.id, [])
        await create_membership(session, tenant.org.id, other.user.id, "member")
        await session.commit()

    client = client_for(tenant.host)
    headers = await auth_cookie(other.user, tenant.org.id)
    for method, path in (
        ("get", "/api/v1/timeon/accounts"),
        ("get", "/api/v1/timeon/workspace"),
        ("get", "/api/v1/timeon/conflicts"),
    ):
        resp = await getattr(client, method)(path, headers=headers)
        assert resp.status_code == 403, f"{path} answered {resp.status_code}"


async def test_a_connection_starts_switched_off(client_for, timeon) -> None:
    """Connecting must never be the act that starts writing. Both directions are ``off``,
    ``auto_sync`` is off, and the invoiced protection is on."""
    tenant = await make_tenant("timeondefaults")
    headers = await auth_cookie(tenant.user)
    client = client_for(tenant.host)
    resp = await client.post(
        "/api/v1/timeon/accounts",
        json={"name": "Timeon", "api_key": "test-key"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    row = resp.json()
    assert row["hours_direction"] == "off"
    assert row["projects_direction"] == "off"
    assert row["auto_sync"] is False
    assert row["conflict_policy"] == "manual"
    assert row["protect_invoiced"] is True
    assert row["window_days"] == 45
    assert "api_key" not in row


async def test_two_connections_cannot_share_a_name(client_for, timeon) -> None:
    tenant = await make_tenant("timeonnames")
    headers = await auth_cookie(tenant.user)
    client = client_for(tenant.host)
    body = {"name": "Timeon", "api_key": "test-key"}
    assert (
        await client.post("/api/v1/timeon/accounts", json=body, headers=headers)
    ).status_code == 201
    clash = await client.post("/api/v1/timeon/accounts", json=body, headers=headers)
    assert clash.status_code == 422
    assert clash.json()["error"]["fields"]["name"] == "errors.timeon.name_taken"


async def test_rotating_the_key_forgets_what_the_previous_one_opened(client_for, timeon) -> None:
    """What the old key opened says nothing about what this one does, and leaving the old
    organisation's name on the row is a screen stating a fact that may have stopped being true."""
    tenant = await make_tenant("timeonrotate")
    headers = await auth_cookie(tenant.user)
    client = client_for(tenant.host)
    created = await client.post(
        "/api/v1/timeon/accounts", json={"name": "T", "api_key": "test-key"}, headers=headers
    )
    account_id = created.json()["id"]
    await client.post(f"/api/v1/timeon/accounts/{account_id}/verify", headers=headers)
    assert (await client.get("/api/v1/timeon/accounts", headers=headers)).json()[0][
        "organisation_name"
    ] == "breik."

    patched = await client.patch(
        f"/api/v1/timeon/accounts/{account_id}", json={"api_key": "another"}, headers=headers
    )
    assert patched.status_code == 200
    row = patched.json()
    assert row["status"] == "pending"
    assert row["last_verified_at"] is None


async def test_the_workspace_is_one_round_trip_and_never_calls_timeon(client_for, timeon) -> None:
    """docs/GOOGLE_TAG_MANAGER.md §3a: four reads that each resolve the same account are four
    round trips for one screen. And it must render during an outage, which is when it is opened."""
    tenant = await make_tenant("timeonws")
    headers = await auth_cookie(tenant.user)
    client = client_for(tenant.host)
    await client.post(
        "/api/v1/timeon/accounts", json={"name": "T", "api_key": "test-key"}, headers=headers
    )
    timeon.calls.clear()
    resp = await client.get("/api/v1/timeon/workspace", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["accounts"]) == 1
    assert body["recent_runs"] == []
    assert timeon.calls == [], "the workspace must not touch Timeon"


# --------------------------------------------------------------------------------------- #
# The client's own behaviours (the ones its docstring numbers)
# --------------------------------------------------------------------------------------- #
def test_month_windows_covers_the_span_and_never_overshoots() -> None:
    windows = list(month_windows(date(2026, 1, 15), date(2026, 3, 2)))
    assert windows == [
        (date(2026, 1, 15), date(2026, 1, 31)),
        (date(2026, 2, 1), date(2026, 2, 28)),
        (date(2026, 3, 1), date(2026, 3, 2)),
    ]
    assert list(month_windows(date(2026, 3, 2), date(2026, 3, 1))) == []


async def test_the_token_exchange_sends_a_content_length(timeon) -> None:
    """Without it the live server answers **411**, not 400 — and that surfaces as a transport
    error rather than as anything about the credential. The fake reproduces the 411 so a
    regression here fails loudly instead of only in production."""
    client = timeon_client.TimeonClient("test-key")
    org = await client.organisation()
    assert org["organisationID"] == 2362
    assert timeon.tokens_issued == 1


async def test_a_lapsed_token_is_re_exchanged_once_mid_run(timeon) -> None:
    """Four hours, and no refresh token for this grant, so a full-history read can outlive it."""
    timeon.add_user(1, "A", "a@example.com")
    client = timeon_client.TimeonClient("test-key")
    await client.organisation()
    timeon.expire_token_after = 0
    users = await client.users()
    assert [u["email"] for u in users] == ["a@example.com"]
    assert timeon.tokens_issued == 2


async def test_a_refusal_arrives_as_http_200(timeon) -> None:
    """Rule 7: a client that only checks the status code reports every failure as a write that
    worked."""
    client = timeon_client.TimeonClient("test-key")
    with pytest.raises(timeon_client.TimeonError, match="hour not found"):
        await client.save_hour({"hourID": 999_999, "seconds": 60})


async def test_a_wrong_key_raises_the_auth_error_and_not_a_generic_one(timeon) -> None:
    client = timeon_client.TimeonClient("nope")
    with pytest.raises(timeon_client.TimeonAuthError):
        await client.organisation()


async def test_the_edge_block_is_its_own_class(timeon) -> None:
    """Cloudflare's 1010 is HTTP 403 with a body that reads exactly like a permissions failure."""
    import httpx

    timeon.failures.append(("/token", httpx.Response(403, text="error code: 1010")))
    client = timeon_client.TimeonClient("test-key")
    with pytest.raises(timeon_client.TimeonBlockedError):
        await client.organisation()


# --------------------------------------------------------------------------------------- #
# The mapping's own rules
# --------------------------------------------------------------------------------------- #
def _resolver() -> Resolver:
    import uuid

    return Resolver(users={}, companies={"7": uuid.uuid4()}, projects={})


def test_an_unpairable_reference_is_a_sentinel_on_both_sides() -> None:
    """A schakl project with no Timeon pairing and a Timeon project with no schakl pairing both
    canonicalise to ``?``. Comparing an id against nothing would report drift on every run for a
    difference the sync is not configured to fix — the queue nobody reads."""
    resolver = _resolver()
    row = {"hourID": 1, "date": "2026-05-12T00:00:00", "seconds": 3600, "fromSeconds": 32400,
           "projectID": 99, "customerID": 7, "remark": "x", "billable": True}
    neutral = neutral_from_row(row, start_seconds=32400, resolver=resolver)
    assert neutral["project"] == UNRESOLVED
    assert neutral["company"] == "7"


def test_a_placed_start_never_enters_the_fingerprint() -> None:
    """605 of 2823 real rows carry no start. Including the value we placed would make deleting one
    morning row report six rows of drift about a change nobody made."""
    resolver = _resolver()
    base = {"hourID": 1, "date": "2026-05-12T00:00:00", "seconds": 3600, "remark": "x",
            "billable": True}
    without = neutral_from_row({**base, "fromSeconds": None}, start_seconds=32400,
                               resolver=resolver)
    moved = neutral_from_row({**base, "fromSeconds": None}, start_seconds=39600,
                             resolver=resolver)
    assert without["start_seconds"] is None
    assert fingerprint(without) == fingerprint(moved)


def test_start_less_rows_stack_deterministically_and_appending_moves_nothing() -> None:
    """``hourID`` increases monotonically, so a new row appends rather than shifting the day."""
    rows = [
        {"hourID": 10, "userID": 1, "date": "2026-05-12T00:00:00", "seconds": 3600,
         "fromSeconds": None},
        {"hourID": 11, "userID": 1, "date": "2026-05-12T00:00:00", "seconds": 1800,
         "fromSeconds": None},
    ]
    first = plan_start_seconds(rows)
    assert first == {10: 32400, 11: 36000}
    later = plan_start_seconds(
        rows
        + [
            {"hourID": 12, "userID": 1, "date": "2026-05-12T00:00:00", "seconds": 900,
             "fromSeconds": None}
        ]
    )
    assert later[10] == first[10] and later[11] == first[11]
    assert later[12] == 37800


def test_a_row_with_a_real_start_is_never_re_placed() -> None:
    rows = [
        {"hourID": 10, "userID": 1, "date": "2026-05-12T00:00:00", "seconds": 3600,
         "fromSeconds": 44100},
        {"hourID": 11, "userID": 1, "date": "2026-05-12T00:00:00", "seconds": 1800,
         "fromSeconds": None},
    ]
    placed = plan_start_seconds(rows)
    assert placed[10] == 44100
    assert placed[11] == 32400, "a start-less row stacks from 09:00, not after a timed one"


def test_a_push_payload_carries_what_schakl_cannot_author() -> None:
    """Rule 7. ``hour/save`` replaces, so anything schakl has no field for is carried over from
    what we last observed — or a description correction deletes a client's mileage claim."""
    payload = timeon_payload(
        hour_id=42,
        observed={"distance": 42.5, "distanceCategoryID": 7, "expenseValue": 3.0, "taskID": 9},
        user_ext="2004392",
        company_ext="2112237",
        project_ext="2115429",
        day=date(2026, 5, 12),
        start_seconds=32400,
        minutes=90,
        description="Werk",
        billable=True,
    )
    assert payload["hourID"] == 42
    assert payload["seconds"] == 5400
    assert payload["distance"] == 42.5
    assert payload["taskID"] == 9
    assert payload["date"] == "2026-05-12T00:00:00"


def test_only_the_compared_fields_can_ever_be_a_difference() -> None:
    """The conflict screen names what a person can reason about, never the eighty fields Timeon
    ships beside them (#300's rule)."""
    left = {"minutes": 60, "description": "a", "billable": True}
    right = {"minutes": 90, "description": "a", "billable": True}
    assert differences(left, right) == {"minutes": {"local": 60, "remote": 90}}
