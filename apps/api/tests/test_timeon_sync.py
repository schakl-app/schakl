"""The Timeon sync, end to end against the fake organisation.

Every test here defends one of the nine rules stated in ``app/integrations/timeon/sync.py``, and
they are separate tests rather than one "sync it and see" because that one would pass on any
single guard working and tell you nothing about the other eight.

The property with the most at stake is **adoption before creation**. This module ships into an
instance that already holds 2814 entries a one-way importer wrote, so the first press of a
two-way sync either recognises them or duplicates three years of somebody's timesheet — and no
undo exists for that.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.integrations.timeon import client as timeon_client
from app.integrations.timeon.models import (
    ConflictPolicy,
    TimeonLink,
    TimeonLinkKind,
    TimeonLinkStatus,
)
from app.modules.time.models import TimeEntry
from tests.conftest import Tenant, auth_cookie, make_tenant, org_today
from tests.timeon_fake import FakeTimeon

API_KEY = "test-key"
TIMEON_USER = 2004392
TIMEON_CUSTOMER = 2112237
TIMEON_PROJECT = 2115429

#: A fixed month, so a window assertion is not an argument with the calendar. Every date in this
#: file lives inside it and the sync is always asked for it explicitly.
DAY = date(2026, 5, 12)
WINDOW = {"window_from": "2026-05-01", "window_to": "2026-05-31"}


@pytest.fixture
def timeon() -> FakeTimeon:
    fake = FakeTimeon()
    fake.api_key = API_KEY
    timeon_client.set_transport(fake.transport())
    yield fake
    timeon_client.set_transport(None)


# --------------------------------------------------------------------------------------- #
# Scaffolding
# --------------------------------------------------------------------------------------- #
async def _tenant(client_for, slug: str = "timeonco") -> tuple[Tenant, dict, object]:
    tenant = await make_tenant(slug, email=f"{slug}@example.com")
    headers = await auth_cookie(tenant.user)
    return tenant, headers, client_for(tenant.host)


async def _connect(client, headers, timeon: FakeTimeon, **policy) -> str:
    resp = await client.post(
        "/api/v1/timeon/accounts",
        json={"name": "Timeon", "api_key": API_KEY},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    account_id = resp.json()["id"]
    assert resp.json()["hours_direction"] == "off", "connecting must not start syncing"

    verify = await client.post(f"/api/v1/timeon/accounts/{account_id}/verify", headers=headers)
    assert verify.status_code == 200, verify.text
    assert verify.json()["ok"] is True
    assert verify.json()["organisation_name"] == "breik."

    if policy:
        patch = await client.patch(
            f"/api/v1/timeon/accounts/{account_id}", json=policy, headers=headers
        )
        assert patch.status_code == 200, patch.text
    return account_id


async def _company(client, headers, *, name="Klant BV", number="402148") -> str:
    resp = await client.post(
        "/api/v1/companies", json={"name": name, "client_number": number}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _seed_remote(timeon: FakeTimeon, tenant: Tenant) -> None:
    timeon.add_user(TIMEON_USER, "Stan", tenant.user.email)
    timeon.add_customer(TIMEON_CUSTOMER, "Klant BV", "402148")
    timeon.add_project(TIMEON_PROJECT, TIMEON_CUSTOMER, "CRM", budget_seconds=302400)


async def _sync(client, headers, account_id: str, **body) -> dict:
    payload = {"kind": "hours", "dry_run": False, **WINDOW, **body}
    resp = await client.post(
        f"/api/v1/timeon/accounts/{account_id}/sync", json=payload, headers=headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _entries(tenant: Tenant) -> list[TimeEntry]:
    from app.db import async_session_maker, set_current_org

    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        rows = (
            (
                await session.execute(
                    select(TimeEntry)
                    .where(TimeEntry.org_id == tenant.org.id)
                    .order_by(TimeEntry.started_at)
                )
            )
            .scalars()
            .all()
        )
        return list(rows)


async def _links(tenant: Tenant, kind: str = TimeonLinkKind.HOUR.value) -> list[TimeonLink]:
    from app.db import async_session_maker, set_current_org

    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        rows = (
            (
                await session.execute(
                    select(TimeonLink)
                    .where(TimeonLink.org_id == tenant.org.id, TimeonLink.kind == kind)
                )
            )
            .scalars()
            .all()
        )
        return list(rows)


# --------------------------------------------------------------------------------------- #
# The credential
# --------------------------------------------------------------------------------------- #
async def test_a_refused_key_is_an_answer_not_an_error(client_for, timeon) -> None:
    """Verify never raises.

    ``require_context`` rolls the session back on any exception, so raising here would discard
    the very row that records what Timeon said — the screen would show the account exactly as it
    was, with no explanation anywhere.
    """
    tenant, headers, client = await _tenant(client_for)
    resp = await client.post(
        "/api/v1/timeon/accounts", json={"name": "T", "api_key": "wrong"}, headers=headers
    )
    account_id = resp.json()["id"]

    verify = await client.post(f"/api/v1/timeon/accounts/{account_id}/verify", headers=headers)
    assert verify.status_code == 200
    assert verify.json()["ok"] is False
    assert verify.json()["error_key"] == "errors.timeon.key_refused"

    # And the refusal survived on the row, which is the half a raise would have lost.
    row = (await client.get("/api/v1/timeon/accounts", headers=headers)).json()[0]
    assert row["status"] == "error"
    assert "Invalid API key" in (row["last_error"] or "")


async def test_the_edge_blocking_us_is_a_different_sentence(client_for, timeon) -> None:
    """Cloudflare's 1010 reads exactly like an auth failure and is nothing like one.

    Three refusals, three keys (``service.error_key_for``): the credential is fine and the
    *deployment* is being filtered, so telling an admin to re-issue their key sends them to fix
    the thing that was already right.
    """
    import httpx

    tenant, headers, client = await _tenant(client_for)
    resp = await client.post(
        "/api/v1/timeon/accounts", json={"name": "T", "api_key": API_KEY}, headers=headers
    )
    account_id = resp.json()["id"]
    timeon.failures.append(("/token", httpx.Response(403, text="error code: 1010")))

    verify = await client.post(f"/api/v1/timeon/accounts/{account_id}/verify", headers=headers)
    assert verify.json()["error_key"] == "errors.timeon.edge_blocked"


async def test_the_api_key_never_leaves_the_database(client_for, timeon) -> None:
    tenant, headers, client = await _tenant(client_for)
    await _connect(client, headers, timeon)
    body = (await client.get("/api/v1/timeon/accounts", headers=headers)).text
    assert API_KEY not in body
    assert (await client.get("/api/v1/timeon/workspace", headers=headers)).text.count(API_KEY) == 0


# --------------------------------------------------------------------------------------- #
# Rule 1 — adoption before creation
# --------------------------------------------------------------------------------------- #
async def test_an_already_imported_entry_is_adopted_never_duplicated(client_for, timeon) -> None:
    """The first press against an instance the importer already filled.

    The natural key is the importer's own, so an entry it wrote is *recognised*. A sync that
    created before it adopted would double every hour of three years of history on its first run.
    """
    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    company_id = await _company(client, headers)
    account_id = await _connect(
        client, headers, timeon, hours_direction="two_way", projects_direction="pull"
    )

    timeon.add_hour(
        user_id=TIMEON_USER,
        day=DAY.isoformat(),
        seconds=7200,
        from_seconds=32400,
        remark="Sprint review",
        customer_id=TIMEON_CUSTOMER,
        project_id=TIMEON_PROJECT,
    )
    # The importer's own product: an entry at the same wall clock, duration and description.
    created = await client.post(
        "/api/v1/time/entries",
        json={
            "company_id": company_id,
            "started_at": f"{DAY.isoformat()}T09:00:00Z",
            "minutes": 120,
            "description": "Sprint review",
            "billable": True,
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text

    run = await _sync(client, headers, account_id, kind="adopt")
    assert run["counts"]["adopted"] == 1
    assert run["counts"].get("pulled_new", 0) == 0
    assert len(await _entries(tenant)) == 1, "adoption must not create a second entry"
    assert [link.status for link in await _links(tenant)] == [TimeonLinkStatus.LINKED.value]


async def test_adopt_only_writes_no_hours_at_all(client_for, timeon) -> None:
    """``adopt`` is the safe first press: it pairs what is already here and stops.

    Which is what lets an agency look at "2814 paired, 3 only in Timeon" *before* deciding what
    the sync should do about the difference.
    """
    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    await _company(client, headers)
    account_id = await _connect(client, headers, timeon, hours_direction="pull")
    timeon.add_hour(user_id=TIMEON_USER, day=DAY.isoformat(), seconds=3600, remark="Alleen daar")

    run = await _sync(client, headers, account_id, kind="adopt")
    assert run["counts"].get("pulled_new", 0) == 0
    assert await _entries(tenant) == []


# --------------------------------------------------------------------------------------- #
# Rule 9 — a dry run is the default and writes nothing
# --------------------------------------------------------------------------------------- #
async def test_a_dry_run_counts_everything_and_writes_nothing(client_for, timeon) -> None:
    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    await _company(client, headers)
    account_id = await _connect(
        client, headers, timeon, hours_direction="pull", projects_direction="pull"
    )
    timeon.add_hour(
        user_id=TIMEON_USER,
        day=DAY.isoformat(),
        seconds=5400,
        remark="Nieuw",
        customer_id=TIMEON_CUSTOMER,
        project_id=TIMEON_PROJECT,
    )

    run = await _sync(client, headers, account_id, dry_run=True)
    assert run["dry_run"] is True
    assert run["counts"]["pulled_new"] == 1
    assert await _entries(tenant) == []
    assert await _links(tenant) == []

    # The same request, for real, does exactly what the dry run said it would.
    run = await _sync(client, headers, account_id)
    assert run["counts"]["pulled_new"] == 1
    assert len(await _entries(tenant)) == 1


async def test_a_dry_run_needs_no_write_permission(client_for, timeon) -> None:
    """It is a read of both systems and a piece of arithmetic. A real run additionally needs
    ``timeon.sync.write`` **and** ``time.entry.write:any`` (#314)."""
    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    account_id = await _connect(client, headers, timeon, hours_direction="pull")

    from app.core.permissions.service import (
        create_membership,
        replace_permissions,
        role_by_key,
    )
    from app.db import async_session_maker, set_current_org

    other = await make_tenant("timeonco2", email="reader@example.com")
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        role = await role_by_key(session, tenant.org.id, "member")
        # Exactly the read half: may run a sync, may not write one.
        await replace_permissions(session, tenant.org.id, role.id, ["timeon.sync.run"])
        await create_membership(session, tenant.org.id, other.user.id, "member")
        await session.commit()

    reader_headers = await auth_cookie(other.user, tenant.org.id)
    dry = await client.post(
        f"/api/v1/timeon/accounts/{account_id}/sync",
        json={"kind": "hours", "dry_run": True, **WINDOW},
        headers=reader_headers,
    )
    assert dry.status_code == 200, dry.text
    wet = await client.post(
        f"/api/v1/timeon/accounts/{account_id}/sync",
        json={"kind": "hours", "dry_run": False, **WINDOW},
        headers=reader_headers,
    )
    assert wet.status_code == 403


# --------------------------------------------------------------------------------------- #
# Pulling
# --------------------------------------------------------------------------------------- #
async def test_a_pulled_entry_carries_its_clock_client_project_and_billable(
    client_for, timeon
) -> None:
    """The four things a mirrored hour is *for*.

    The clock is wall-clock-as-UTC (§8): converting Europe/Amsterdam here would shift every
    historical timesheet by an hour and make the first run report every row as changed.
    """
    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    company_id = await _company(client, headers)
    account_id = await _connect(
        client,
        headers,
        timeon,
        hours_direction="pull",
        projects_direction="pull",
        create_missing_projects=True,
    )
    timeon.add_hour(
        user_id=TIMEON_USER,
        day=DAY.isoformat(),
        seconds=8100,
        from_seconds=44100,  # 12:15
        remark="Nieuwe features",
        customer_id=TIMEON_CUSTOMER,
        project_id=TIMEON_PROJECT,
        billable=False,
    )

    run = await _sync(client, headers, account_id, kind="full")
    assert run["ok"] is True, run
    entries = await _entries(tenant)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.started_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M") == "2026-05-12 12:15"
    assert entry.minutes == 135
    assert entry.company_id == uuid.UUID(company_id)
    assert entry.project_id is not None, "the project was created and paired"
    assert entry.billable is False, "stated explicitly, never left to the project default (#284)"


async def test_the_project_budget_arrives_in_hours_not_seconds(client_for, timeon) -> None:
    """Timeon states a budget in **seconds** (302400 = 84:00)."""
    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    await _company(client, headers)
    account_id = await _connect(
        client,
        headers,
        timeon,
        projects_direction="pull",
        create_missing_projects=True,
    )
    await _sync(client, headers, account_id, kind="projects")

    projects = (await client.get("/api/v1/projects", headers=headers)).json()
    rows = projects["items"] if isinstance(projects, dict) else projects
    assert len(rows) == 1
    assert float(rows[0]["budget_hours"]) == pytest.approx(84.0)


async def test_a_remote_edit_is_carried_onto_the_entry(client_for, timeon) -> None:
    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    await _company(client, headers)
    account_id = await _connect(client, headers, timeon, hours_direction="pull")
    row = timeon.add_hour(
        user_id=TIMEON_USER, day=DAY.isoformat(), seconds=3600, remark="Eerst",
        customer_id=TIMEON_CUSTOMER,
    )
    await _sync(client, headers, account_id)

    row["seconds"] = 5400
    row["remark"] = "Gecorrigeerd"
    run = await _sync(client, headers, account_id)
    assert run["counts"]["pulled"] == 1

    entry = (await _entries(tenant))[0]
    assert entry.minutes == 90
    assert entry.description == "Gecorrigeerd"


async def test_an_unchanged_window_writes_nothing_the_second_time(client_for, timeon) -> None:
    """The fingerprint's whole job. A sync that rewrites four hundred unchanged rows every night
    is one that makes ``updated_at`` meaningless and burns a rate limit for no reason."""
    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    await _company(client, headers)
    account_id = await _connect(client, headers, timeon, hours_direction="two_way")
    for index in range(3):
        timeon.add_hour(
            user_id=TIMEON_USER,
            day=DAY.isoformat(),
            seconds=3600 * (index + 1),
            from_seconds=32400 + index * 3600,
            remark=f"Rij {index}",
            customer_id=TIMEON_CUSTOMER,
        )
    await _sync(client, headers, account_id)
    run = await _sync(client, headers, account_id)
    assert run["counts"]["in_step"] == 3
    assert run["counts"].get("pulled", 0) == 0
    assert run["counts"].get("pushed", 0) == 0
    assert run["counts"].get("conflicts", 0) == 0


async def test_an_unpairable_project_is_not_a_difference_in_the_row_it_was_pulled_into(
    client_for, timeon
) -> None:
    """The sentinel is a sentinel, not a value — and the first real run is what proved it wasn't.

    62 of 66 rows pulled from the live organisation came back on the *next* run as "allowed to
    differ", which is the state a **dismissed conflict** leaves behind: a decision nobody had
    made, recorded 62 times, burying the one signal that arm carries. The cause is one asymmetry:
    a Timeon project with no pairing here canonicalises to ``"?"`` while the entry the pull just
    wrote carries no project at all and canonicalises to ``""``.

    Neither direction could ever close that gap — a pull cannot set a project schakl has never
    heard of — so the run reports it once as ``project_unmapped`` and the *row* says nothing.
    """
    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    await _company(client, headers)
    # Projects deliberately left off: the hour names one Timeon knows and schakl cannot pair.
    account_id = await _connect(client, headers, timeon, hours_direction="two_way")
    timeon.add_hour(
        user_id=TIMEON_USER,
        day=DAY.isoformat(),
        seconds=2400,
        remark="Uur op een niet te koppelen project",
        customer_id=TIMEON_CUSTOMER,
        project_id=TIMEON_PROJECT,
    )

    first = await _sync(client, headers, account_id)
    assert first["counts"]["pulled_new"] == 1
    entry = (await _entries(tenant))[0]
    assert entry.project_id is None, "there was no project here to point at"

    second = await _sync(client, headers, account_id)
    assert second["counts"].get("in_step", 0) == 1, second["counts"]
    assert second["counts"].get("tolerated", 0) == 0, (
        "a difference no direction can act on is not a decision somebody made"
    )
    assert second["counts"].get("conflicts", 0) == 0
    assert second["counts"].get("pulled", 0) == 0
    assert second["counts"].get("pushed", 0) == 0, "and it must not be pushed back either"


def test_an_unresolved_reference_never_reaches_the_conflict_card() -> None:
    """The screen half of the same rule: a card offering "keep mine / take theirs" over a project
    neither side can name is #253's control that can only refuse."""
    from app.integrations.timeon.mapping import UNRESOLVED, differences

    local = {"minutes": 60, "project": "", "description": "Zelfde"}
    remote = {"minutes": 60, "project": UNRESOLVED, "description": "Zelfde"}
    assert differences(local, remote) == {}

    remote_moved = {"minutes": 90, "project": UNRESOLVED, "description": "Zelfde"}
    assert differences(local, remote_moved) == {"minutes": {"local": 60, "remote": 90}}, (
        "a real difference beside an unactionable one is still reported"
    )


# --------------------------------------------------------------------------------------- #
# Rule 5 — an invoiced entry is a record
# --------------------------------------------------------------------------------------- #
async def test_an_invoiced_entry_is_never_rewritten_and_says_so(client_for, timeon) -> None:
    """``docs/TIMEON.md`` §2 argued against building this at all on exactly this ground.

    The answer is a mechanism, not a dismissal: nothing that has reached a client's invoice is
    rewritten, and the divergence is *reported* so it is visible rather than silently swallowed.
    """
    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    await _company(client, headers)
    account_id = await _connect(client, headers, timeon, hours_direction="pull")
    row = timeon.add_hour(
        user_id=TIMEON_USER, day=DAY.isoformat(), seconds=3600, remark="Gefactureerd",
        customer_id=TIMEON_CUSTOMER,
    )
    await _sync(client, headers, account_id)

    entry = (await _entries(tenant))[0]
    from app.db import async_session_maker, set_current_org

    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        live = await session.get(TimeEntry, entry.id)
        live.invoiced_at = datetime.now(UTC)
        await session.commit()

    row["seconds"] = 9999
    run = await _sync(client, headers, account_id)
    assert run["counts"]["protected_invoiced"] == 1
    assert any(w["code"] == "protected" for w in run["warnings"])
    assert (await _entries(tenant))[0].minutes == 60, "the billed hour stands"


async def test_an_invoiced_entry_survives_a_deletion_over_there(client_for, timeon) -> None:
    """An hour does not stop having happened because somebody tidied up in the other system."""
    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    await _company(client, headers)
    account_id = await _connect(client, headers, timeon, hours_direction="pull")
    row = timeon.add_hour(
        user_id=TIMEON_USER, day=DAY.isoformat(), seconds=3600, remark="Weg", customer_id=None
    )
    await _sync(client, headers, account_id)
    entry = (await _entries(tenant))[0]

    from app.db import async_session_maker, set_current_org

    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        live = await session.get(TimeEntry, entry.id)
        live.invoiced_at = datetime.now(UTC)
        await session.commit()

    del timeon.hours[row["hourID"]]
    run = await _sync(client, headers, account_id)
    assert run["counts"]["protected_invoiced"] == 1
    assert len(await _entries(tenant)) == 1
    assert [link.status for link in await _links(tenant)] == [TimeonLinkStatus.MISSING.value]


# --------------------------------------------------------------------------------------- #
# Rule 3 — absence is a deletion only under two guards
# --------------------------------------------------------------------------------------- #
async def test_a_short_window_is_refused_rather_than_read_as_deletions(
    client_for, timeon
) -> None:
    """The guard that makes absence safe to act on at all.

    ``filter.deleted`` is accepted and ignored by the live API, so a delete has no signal but
    absence — which means a window that quietly came back short would be read as a batch of
    deletions and would delete live work.
    """
    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    await _company(client, headers)
    account_id = await _connect(client, headers, timeon, hours_direction="pull")
    for index in range(3):
        timeon.add_hour(
            user_id=TIMEON_USER, day=DAY.isoformat(), seconds=3600,
            from_seconds=32400 + index * 3600, remark=f"r{index}",
        )
    await _sync(client, headers, account_id)
    assert len(await _entries(tenant)) == 3

    timeon.drop_rows = 2
    run = await _sync(client, headers, account_id)
    assert run["ok"] is False
    assert "refusing a partial window" in (run["message"] or "")
    assert len(await _entries(tenant)) == 3, "nothing was deleted on the strength of a short read"


async def test_an_hour_moved_to_another_month_is_not_a_deletion(client_for, timeon) -> None:
    """Asked again by id before anything is deleted.

    An hour moved from 12 May to 12 January is absent from May and is not gone — and the *only*
    thing that tells the two apart is a second question.
    """
    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    await _company(client, headers)
    account_id = await _connect(client, headers, timeon, hours_direction="pull")
    row = timeon.add_hour(
        user_id=TIMEON_USER, day=DAY.isoformat(), seconds=3600, remark="Verplaatst"
    )
    await _sync(client, headers, account_id)
    assert len(await _entries(tenant)) == 1

    row["date"] = "2026-01-12T00:00:00"
    run = await _sync(client, headers, account_id)
    assert run["counts"]["moved_out_of_window"] == 1
    assert run["counts"].get("deleted_local", 0) == 0
    assert len(await _entries(tenant)) == 1


async def test_a_deleted_remote_row_removes_the_entry_under_a_pull(client_for, timeon) -> None:
    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    await _company(client, headers)
    account_id = await _connect(client, headers, timeon, hours_direction="pull")
    row = timeon.add_hour(user_id=TIMEON_USER, day=DAY.isoformat(), seconds=3600, remark="Fout")
    await _sync(client, headers, account_id)

    del timeon.hours[row["hourID"]]
    run = await _sync(client, headers, account_id)
    assert run["counts"]["deleted_local"] == 1
    assert await _entries(tenant) == []
    assert await _links(tenant) == []


async def test_a_push_only_direction_never_deletes_here(client_for, timeon) -> None:
    """Under ``push`` schakl is authoritative, so a row missing over there is something to
    *restore*, never an instruction to delete."""
    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    await _company(client, headers)
    account_id = await _connect(client, headers, timeon, hours_direction="pull")
    row = timeon.add_hour(user_id=TIMEON_USER, day=DAY.isoformat(), seconds=3600, remark="Blijf")
    await _sync(client, headers, account_id)

    await client.patch(
        f"/api/v1/timeon/accounts/{account_id}",
        json={"hours_direction": "push"},
        headers=headers,
    )
    del timeon.hours[row["hourID"]]
    run = await _sync(client, headers, account_id)
    assert run["counts"]["remote_missing"] == 1
    assert len(await _entries(tenant)) == 1


# --------------------------------------------------------------------------------------- #
# Pushing (rule 7)
# --------------------------------------------------------------------------------------- #
async def test_a_push_sends_the_whole_row_and_keeps_what_schakl_has_no_field_for(
    client_for, timeon
) -> None:
    """``hour/save`` **replaces**. A save carrying ``{hourID, seconds}`` was measured to blank
    the remark and null out both ``projectID`` and ``customerID`` — so a description correction
    would delete a client's mileage claim unless the push carries it back."""
    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    await _company(client, headers)
    account_id = await _connect(
        client, headers, timeon, hours_direction="two_way", projects_direction="pull"
    )
    row = timeon.add_hour(
        user_id=TIMEON_USER,
        day=DAY.isoformat(),
        seconds=3600,
        remark="Origineel",
        customer_id=TIMEON_CUSTOMER,
        project_id=TIMEON_PROJECT,
        distance=42.5,
        distanceCategoryID=7,
    )
    await _sync(client, headers, account_id, kind="full")

    entry = (await _entries(tenant))[0]
    patched = await client.patch(
        f"/api/v1/time/entries/{entry.id}",
        json={"description": "Bijgewerkt in schakl"},
        headers=headers,
    )
    assert patched.status_code == 200, patched.text

    run = await _sync(client, headers, account_id)
    assert run["counts"]["pushed"] == 1
    saved = timeon.hours[row["hourID"]]
    assert saved["remark"] == "Bijgewerkt in schakl"
    assert saved["customerID"] == TIMEON_CUSTOMER, "not blanked by an omitted field"
    # The project was never paired (no `create_missing_projects`), so the entry carries none —
    # and schakl must not assert an absence it cannot express. Detaching a client's hour from
    # its project as a side effect of correcting a description is the failure rule 7 is about.
    assert saved["projectID"] == TIMEON_PROJECT, "unpairable reference carried, not cleared"
    assert saved["distance"] == 42.5, "carried over, never authored"


async def test_detaching_a_project_here_does_detach_it_there(client_for, timeon) -> None:
    """The other half of the rule above, and the reason it is not simply "never clear".

    Where the Timeon reference *is* pairable, schakl having none is somebody's deliberate act
    and travels as one. Only an unpairable reference is carried — the case where we have no way
    to say what we mean.
    """
    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    await _company(client, headers)
    account_id = await _connect(
        client,
        headers,
        timeon,
        hours_direction="two_way",
        projects_direction="pull",
        create_missing_projects=True,
    )
    row = timeon.add_hour(
        user_id=TIMEON_USER,
        day=DAY.isoformat(),
        seconds=3600,
        remark="Met project",
        customer_id=TIMEON_CUSTOMER,
        project_id=TIMEON_PROJECT,
    )
    await _sync(client, headers, account_id, kind="full")
    entry = (await _entries(tenant))[0]
    assert entry.project_id is not None

    patched = await client.patch(
        f"/api/v1/time/entries/{entry.id}", json={"project_id": None}, headers=headers
    )
    assert patched.status_code == 200, patched.text
    run = await _sync(client, headers, account_id)
    assert run["counts"]["pushed"] == 1
    assert timeon.hours[row["hourID"]]["projectID"] is None


async def test_a_new_schakl_entry_is_created_over_there_under_a_push(client_for, timeon) -> None:
    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    company_id = await _company(client, headers)
    account_id = await _connect(client, headers, timeon, hours_direction="push")
    await _sync(client, headers, account_id, kind="adopt")  # pair the references

    await client.post(
        "/api/v1/time/entries",
        json={
            "company_id": company_id,
            "started_at": f"{DAY.isoformat()}T10:00:00Z",
            "minutes": 45,
            "description": "Nieuw hier",
        },
        headers=headers,
    )
    run = await _sync(client, headers, account_id)
    assert run["counts"]["pushed_new"] == 1
    assert len(timeon.hours) == 1
    remote = next(iter(timeon.hours.values()))
    assert remote["seconds"] == 45 * 60
    assert remote["fromSeconds"] == 10 * 3600
    assert remote["customerID"] == TIMEON_CUSTOMER
    assert remote["userID"] == TIMEON_USER


async def test_a_pull_only_direction_never_writes_to_timeon(client_for, timeon) -> None:
    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    company_id = await _company(client, headers)
    account_id = await _connect(client, headers, timeon, hours_direction="pull")
    await _sync(client, headers, account_id, kind="adopt")

    await client.post(
        "/api/v1/time/entries",
        json={
            "company_id": company_id,
            "started_at": f"{DAY.isoformat()}T10:00:00Z",
            "minutes": 45,
            "description": "Alleen hier",
        },
        headers=headers,
    )
    run = await _sync(client, headers, account_id)
    assert run["counts"]["local_only"] == 1
    assert timeon.hours == {}


async def test_deleting_an_entry_here_removes_it_there_under_a_push(client_for, timeon) -> None:
    """Softly, so it lands in Timeon's own bin: an integration should not be able to destroy a
    client's record beyond what their own UI can undo."""
    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    await _company(client, headers)
    account_id = await _connect(client, headers, timeon, hours_direction="two_way")
    row = timeon.add_hour(user_id=TIMEON_USER, day=DAY.isoformat(), seconds=3600, remark="Weg")
    await _sync(client, headers, account_id)
    entry = (await _entries(tenant))[0]

    assert (
        await client.delete(f"/api/v1/time/entries/{entry.id}", headers=headers)
    ).status_code == 204
    run = await _sync(client, headers, account_id)
    assert run["counts"]["deleted_remote"] == 1
    assert row["hourID"] not in timeon.hours
    assert row["hourID"] in timeon.deleted, "soft, not definitive"


# --------------------------------------------------------------------------------------- #
# Rule 4 — conflicts
# --------------------------------------------------------------------------------------- #
async def _diverge(client, headers, tenant, timeon, account_id, row) -> dict:
    """Change the same paired record on both sides, then sync."""
    entry = (await _entries(tenant))[0]
    await client.patch(
        f"/api/v1/time/entries/{entry.id}", json={"description": "Onze versie"}, headers=headers
    )
    row["remark"] = "Hun versie"
    return await _sync(client, headers, account_id)


async def test_both_sides_moving_is_a_conflict_and_neither_is_written(
    client_for, timeon
) -> None:
    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    await _company(client, headers)
    account_id = await _connect(client, headers, timeon, hours_direction="two_way")
    row = timeon.add_hour(user_id=TIMEON_USER, day=DAY.isoformat(), seconds=3600, remark="Start")
    await _sync(client, headers, account_id)

    run = await _diverge(client, headers, tenant, timeon, account_id, row)
    assert run["counts"]["conflicts"] == 1
    assert (await _entries(tenant))[0].description == "Onze versie"
    assert timeon.hours[row["hourID"]]["remark"] == "Hun versie"

    queue = (await client.get("/api/v1/timeon/conflicts", headers=headers)).json()
    assert len(queue) == 1
    assert queue[0]["differences"]["description"] == {
        "local": "Onze versie",
        "remote": "Hun versie",
    }
    # Presented, never dumped (#300): no `seconds`, no `hourID`, no `remark`.
    assert set(queue[0]["differences"]) <= {
        "started_on", "start_seconds", "minutes", "project", "company", "description", "billable"
    }
    assert queue[0]["user_name"] is not None


async def test_a_second_detection_updates_the_conflict_rather_than_stacking(
    client_for, timeon
) -> None:
    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    await _company(client, headers)
    account_id = await _connect(client, headers, timeon, hours_direction="two_way")
    row = timeon.add_hour(user_id=TIMEON_USER, day=DAY.isoformat(), seconds=3600, remark="Start")
    await _sync(client, headers, account_id)
    await _diverge(client, headers, tenant, timeon, account_id, row)
    await _sync(client, headers, account_id)

    queue = (await client.get("/api/v1/timeon/conflicts", headers=headers)).json()
    assert len(queue) == 1, "one pairing diverging further is still one decision"


@pytest.mark.parametrize(
    ("resolution", "expected_local", "expected_remote"),
    [
        ("kept_local", "Onze versie", "Onze versie"),
        ("kept_remote", "Hun versie", "Hun versie"),
    ],
)
async def test_resolving_a_conflict_carries_the_chosen_side(
    client_for, timeon, resolution, expected_local, expected_remote
) -> None:
    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    await _company(client, headers)
    account_id = await _connect(client, headers, timeon, hours_direction="two_way")
    row = timeon.add_hour(user_id=TIMEON_USER, day=DAY.isoformat(), seconds=3600, remark="Start")
    await _sync(client, headers, account_id)
    await _diverge(client, headers, tenant, timeon, account_id, row)

    conflict = (await client.get("/api/v1/timeon/conflicts", headers=headers)).json()[0]
    resp = await client.post(
        f"/api/v1/timeon/conflicts/{conflict['id']}/resolve",
        json={"resolution": resolution},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert (await _entries(tenant))[0].description == expected_local
    assert timeon.hours[row["hourID"]]["remark"] == expected_remote

    # And the next run is quiet: the resolution re-took both fingerprints.
    run = await _sync(client, headers, account_id)
    assert run["counts"].get("conflicts", 0) == 0
    assert run["counts"]["in_step"] == 1


async def test_a_dismissed_conflict_never_comes_back(client_for, timeon) -> None:
    """The reason this is a table rather than a nightly recomputation (#318).

    "These two rows are allowed to differ" is a real decision, and a queue that re-proposes it
    every night is one nobody reads by the third week.
    """
    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    await _company(client, headers)
    account_id = await _connect(client, headers, timeon, hours_direction="two_way")
    row = timeon.add_hour(user_id=TIMEON_USER, day=DAY.isoformat(), seconds=3600, remark="Start")
    await _sync(client, headers, account_id)
    await _diverge(client, headers, tenant, timeon, account_id, row)

    conflict = (await client.get("/api/v1/timeon/conflicts", headers=headers)).json()[0]
    resp = await client.post(
        f"/api/v1/timeon/conflicts/{conflict['id']}/resolve",
        json={"resolution": "dismissed", "note": "Mag verschillen"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    run = await _sync(client, headers, account_id)
    assert run["counts"].get("conflicts", 0) == 0
    assert run["counts"]["tolerated"] == 1
    assert (await _entries(tenant))[0].description == "Onze versie"
    assert timeon.hours[row["hourID"]]["remark"] == "Hun versie"
    assert (await client.get("/api/v1/timeon/conflicts", headers=headers)).json() == []

    # And two runs later it is still quiet — the branch, not a one-shot flag.
    run = await _sync(client, headers, account_id)
    assert run["counts"]["tolerated"] == 1


@pytest.mark.parametrize(
    ("policy", "local", "remote"),
    [
        (ConflictPolicy.SCHAKL_WINS.value, "Onze versie", "Onze versie"),
        (ConflictPolicy.TIMEON_WINS.value, "Hun versie", "Hun versie"),
    ],
)
async def test_an_authoritative_side_settles_conflicts_without_a_queue(
    client_for, timeon, policy, local, remote
) -> None:
    """A real arrangement, and one a tenant *chooses* rather than one inferred from which side
    happened to be edited last."""
    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    await _company(client, headers)
    account_id = await _connect(
        client, headers, timeon, hours_direction="two_way", conflict_policy=policy
    )
    row = timeon.add_hour(user_id=TIMEON_USER, day=DAY.isoformat(), seconds=3600, remark="Start")
    await _sync(client, headers, account_id)

    run = await _diverge(client, headers, tenant, timeon, account_id, row)
    assert run["counts"].get("conflicts", 0) == 0
    assert (await _entries(tenant))[0].description == local
    assert timeon.hours[row["hourID"]]["remark"] == remote
    assert (await client.get("/api/v1/timeon/conflicts", headers=headers)).json() == []


# --------------------------------------------------------------------------------------- #
# Rule 6 — an unmapped person is reported, never guessed
# --------------------------------------------------------------------------------------- #
async def test_an_unmapped_person_is_reported_and_their_hours_are_not_filed_under_anyone(
    client_for, timeon
) -> None:
    """A third of the migration's hours belonged to people with no schakl account. Filing them
    under whoever ran the sync destroys per-employee reporting silently."""
    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    timeon.add_user(999, "Renzo", "renzo@breik.nl")
    await _company(client, headers)
    account_id = await _connect(client, headers, timeon, hours_direction="pull")
    timeon.add_hour(user_id=999, day=DAY.isoformat(), seconds=3600, remark="Van Renzo")
    timeon.add_hour(
        user_id=TIMEON_USER, day=DAY.isoformat(), seconds=1800, from_seconds=36000, remark="Mijn"
    )

    run = await _sync(client, headers, account_id)
    assert run["counts"]["skipped_user"] == 1
    assert run["counts"]["pulled_new"] == 1
    assert any(w["code"] == "user_unmapped" for w in run["warnings"])
    entries = await _entries(tenant)
    assert len(entries) == 1
    assert entries[0].user_id == tenant.user.id


async def test_an_unmapped_client_is_reported_rather_than_matched_on_name(
    client_for, timeon
) -> None:
    """Never a name match. Two clients called *Maatschap Mini Camping Boudewijnskerke* exist in
    both systems (402148 / 402149), so a name match is ambiguous exactly where it looks safest."""
    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    timeon.add_customer(999, "Klant BV", "999999")  # same name, different number
    await _company(client, headers, name="Klant BV", number="402148")
    account_id = await _connect(client, headers, timeon, hours_direction="pull")

    run = await _sync(client, headers, account_id, kind="full")
    unmapped = [w for w in run["warnings"] if w["code"] == "customer_unmapped"]
    assert [w["number"] for w in unmapped] == ["999999"]


# --------------------------------------------------------------------------------------- #
# The horizon, the floor and the start-less rows
# --------------------------------------------------------------------------------------- #
async def test_the_history_floor_keeps_the_imported_past_out_of_reach(
    client_for, timeon
) -> None:
    """Without it, a sync re-reading 2024 would find every migrated entry "changed" and hand an
    agency a two-thousand-row queue about work settled years ago."""
    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    await _company(client, headers)
    account_id = await _connect(
        client, headers, timeon, hours_direction="pull", history_floor="2026-05-15"
    )
    timeon.add_hour(user_id=TIMEON_USER, day="2026-05-12", seconds=3600, remark="Oud")
    timeon.add_hour(user_id=TIMEON_USER, day="2026-05-20", seconds=3600, remark="Nieuw")

    run = await _sync(client, headers, account_id)
    assert run["window_from"] == "2026-05-16"
    entries = await _entries(tenant)
    assert [e.description for e in entries] == ["Nieuw"]


async def test_a_start_less_row_is_placed_deterministically_and_never_drifts(
    client_for, timeon
) -> None:
    """605 of 2823 real rows carry no start time.

    They are stacked from 09:00 in ``hourID`` order — the importer's own placement, so an entry
    it wrote adopts without moving — and the placed value is then **excluded** from the
    fingerprint. Including it would mean deleting one morning row silently re-timed every later
    row and reported six rows of drift about a change nobody made.
    """
    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    await _company(client, headers)
    account_id = await _connect(client, headers, timeon, hours_direction="two_way")
    first = timeon.add_hour(
        user_id=TIMEON_USER, day=DAY.isoformat(), seconds=3600, from_seconds=None, remark="A"
    )
    timeon.add_hour(
        user_id=TIMEON_USER, day=DAY.isoformat(), seconds=1800, from_seconds=None, remark="B"
    )
    await _sync(client, headers, account_id)

    entries = await _entries(tenant)
    assert [e.started_at.astimezone(UTC).strftime("%H:%M") for e in entries] == ["09:00", "10:00"]

    # Delete the first row: B's stacked start moves to 09:00, and that must not read as an edit.
    del timeon.hours[first["hourID"]]
    run = await _sync(client, headers, account_id)
    assert run["counts"].get("conflicts", 0) == 0
    assert run["counts"].get("pushed", 0) == 0, "a placement we computed is not somebody's edit"
    assert run["counts"]["deleted_local"] == 1


async def test_approval_travels_from_timeon_and_only_back_when_asked(
    client_for, timeon
) -> None:
    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    await _company(client, headers)
    account_id = await _connect(client, headers, timeon, hours_direction="pull")
    row = timeon.add_hour(user_id=TIMEON_USER, day=DAY.isoformat(), seconds=3600, remark="Uren")
    await _sync(client, headers, account_id)
    assert (await _entries(tenant))[0].approved_at is None

    row["approved"] = True
    row["approvedOn"] = "2026-05-13T08:00:00"
    run = await _sync(client, headers, account_id)
    assert run["counts"]["approvals_pulled"] == 1
    entry = (await _entries(tenant))[0]
    assert entry.approved_at is not None
    assert entry.approved_at.astimezone(UTC).strftime("%Y-%m-%d") == "2026-05-13"


async def test_pushing_an_approval_is_its_own_switch(client_for, timeon) -> None:
    """Approving is a different act from logging: an agency may want its sign-offs to reach
    Timeon while the hours themselves only ever come the other way."""
    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    await _company(client, headers)
    account_id = await _connect(
        client, headers, timeon, hours_direction="push", push_approvals=True
    )
    row = timeon.add_hour(user_id=TIMEON_USER, day=DAY.isoformat(), seconds=3600, remark="Uren")
    await _sync(client, headers, account_id, kind="adopt")

    entry = (await _entries(tenant))[0] if await _entries(tenant) else None
    if entry is None:  # nothing local yet — pull one first through a two-way pass
        await client.patch(
            f"/api/v1/timeon/accounts/{account_id}",
            json={"hours_direction": "two_way"},
            headers=headers,
        )
        await _sync(client, headers, account_id)
        entry = (await _entries(tenant))[0]

    approved = await client.post(
        "/api/v1/time/entries/approve",
        json={"entry_ids": [str(entry.id)], "approved": True},
        headers=headers,
    )
    assert approved.status_code in (200, 204), approved.text

    run = await _sync(client, headers, account_id)
    assert run["counts"]["approvals_pushed"] == 1
    assert timeon.hours[row["hourID"]]["approved"] is True


# --------------------------------------------------------------------------------------- #
# Tenancy
# --------------------------------------------------------------------------------------- #
async def test_a_connection_is_invisible_to_another_tenant(client_for, timeon) -> None:
    tenant_a, headers_a, client_a = await _tenant(client_for, "timeonalpha")
    await _seed_remote(timeon, tenant_a)
    account_id = await _connect(client_a, headers_a, timeon)

    tenant_b, headers_b, client_b = await _tenant(client_for, "timeonbeta")
    assert (await client_b.get("/api/v1/timeon/accounts", headers=headers_b)).json() == []
    assert (
        await client_b.post(f"/api/v1/timeon/accounts/{account_id}/verify", headers=headers_b)
    ).status_code == 404
    assert (
        await client_b.patch(
            f"/api/v1/timeon/accounts/{account_id}", json={"name": "gekaapt"}, headers=headers_b
        )
    ).status_code == 404


async def test_the_nightly_job_only_touches_a_connection_that_asked_for_it(
    client_for, timeon
) -> None:
    """``auto_sync`` is off until somebody has watched a dry run and a real one. A nightly job
    that started the moment a key was pasted would make connecting an irreversible act."""
    from app.integrations.timeon.jobs import timeon_nightly

    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    await _company(client, headers)
    account_id = await _connect(client, headers, timeon, hours_direction="pull")
    # `org_today`, not `date.today()` (§8): the nightly window is derived on the org's calendar,
    # and the two clocks disagree for the hours between local midnight and UTC midnight — which
    # is every CI run, and no run on a machine in Europe/Amsterdam.
    timeon.add_hour(
        user_id=TIMEON_USER, day=org_today().isoformat(), seconds=3600, remark="Vandaag"
    )

    await timeon_nightly({})
    assert await _entries(tenant) == []

    await client.patch(
        f"/api/v1/timeon/accounts/{account_id}", json={"auto_sync": True}, headers=headers
    )
    await timeon_nightly({})
    assert len(await _entries(tenant)) == 1

    runs = (await client.get("/api/v1/timeon/runs", headers=headers)).json()
    assert runs[0]["actor_user_id"] is None, "a cron run says it was the cron"


async def test_the_window_is_recorded_so_a_narrow_run_cannot_look_complete(
    client_for, timeon
) -> None:
    """A windowed sync invites exactly one question — "why is last March still wrong?" — and a
    run that says nothing about its own horizon reads as one that looked at everything."""
    tenant, headers, client = await _tenant(client_for)
    await _seed_remote(timeon, tenant)
    account_id = await _connect(client, headers, timeon, hours_direction="pull", window_days=7)
    resp = await client.post(
        f"/api/v1/timeon/accounts/{account_id}/sync",
        json={"kind": "hours", "dry_run": True},
        headers=headers,
    )
    body = resp.json()
    span = date.fromisoformat(body["window_to"]) - date.fromisoformat(body["window_from"])
    assert span == timedelta(days=7)
