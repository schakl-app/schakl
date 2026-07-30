"""Query budgets for the paths that used to scale with their data (#290).

Every assertion here is a *shape*, not a timing: how many statements a request issues, and
whether that number moves when you add rows. That is the only property a test can hold onto —
an endpoint that is one query at three rows and one-per-row at three hundred returns identical
JSON either way (``QueryCounter``'s own docstring), and the version that dies in production
passes every functional test.

The rows stay deliberately small: ``conftest`` truncates between tests, so a fixture that seeds
hundreds of rows costs the whole suite for a property two rows already prove.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.db import async_session_maker, set_current_org
from tests.conftest import add_membership, auth_cookie, make_tenant


def _iso(dt: datetime) -> str:
    return dt.isoformat()


async def _company(client, headers, name: str = "Acme") -> str:
    res = await client.post("/api/v1/companies", json={"name": name}, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _entry(client, headers, *, company_id: str, minutes: int, day: int) -> str:
    started = datetime(2026, 3, 2, 9, 0, tzinfo=UTC) + timedelta(days=day)
    res = await client.post(
        "/api/v1/time/entries",
        json={
            "company_id": company_id,
            "started_at": _iso(started),
            "ended_at": _iso(started + timedelta(minutes=minutes)),
            "billable": True,
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _task(client, headers, *, company_id: str, title: str) -> str:
    res = await client.post(
        "/api/v1/tasks", json={"title": title, "company_id": company_id}, headers=headers
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


# --- /time/logged: an aggregate, not a row scan ------------------------------------------ #
async def test_logged_minutes_never_reads_the_entries_it_sums(client_for, count_queries) -> None:
    """One ``SUM`` statement, and no growth in it when the history grows.

    The old implementation selected every matching entry and folded ``minutes`` in Python — a
    client with years of hours shipped its whole timesheet to render a budget bar.
    """
    t = await make_tenant("perf-logged")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        company = await _company(c, headers)
        for day in range(4):
            await _entry(c, headers, company_id=company, minutes=60, day=day)

        with count_queries() as counter:
            res = await c.get(f"/api/v1/time/logged?company_id={company}", headers=headers)
        assert res.status_code == 200, res.text
        assert res.json()["minutes"] == 240
        statements = counter.matching("from time_entries")
        assert len(statements) == 1, statements
        # The one statement aggregates; it does not select the rows.
        assert "sum(" in statements[0].lower()


# The horizon half of the same rewrite is pinned where the horizon harness already lives:
# ``test_company_groups.test_horizon_reaches_totals_and_summary_tiles``.


# --- the time company panel: bounded, and its total is an aggregate ----------------------- #
async def test_time_panel_is_bounded_however_long_the_history(client_for, count_queries) -> None:
    t = await make_tenant("perf-time-panel")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        company = await _company(c, headers)
        for day in range(12):
            await _entry(c, headers, company_id=company, minutes=30, day=day)

        with count_queries() as counter:
            res = await c.get(f"/api/v1/companies/{company}/panels", headers=headers)
        assert res.status_code == 200, res.text
        panel = next(p for p in res.json() if p["key"] == "time.company")
        # The total counts all twelve entries; the list carries ten.
        assert panel["data"]["total_minutes"] == 360
        assert len(panel["data"]["recent"]) == 10
        # Two statements: the aggregate and the bounded page. Neither grows with the history.
        entry_reads = counter.matching("from time_entries")
        assert len(entry_reads) == 2, entry_reads
        assert any("limit" in s.lower() for s in entry_reads)


# --- task statuses: one statement on the hot path ----------------------------------------- #
async def test_task_status_vocabulary_costs_one_statement_once_seeded(
    client_for, count_queries
) -> None:
    """The "does this org have any?" probe answered what the ordered read already tells us."""
    t = await make_tenant("perf-statuses")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        # First read seeds; every read after it is the hot path.
        assert (await c.get("/api/v1/tasks", headers=headers)).status_code == 200

        with count_queries() as counter:
            assert (await c.get("/api/v1/tasks", headers=headers)).status_code == 200
        reads = counter.matching("from task_statuses")
        assert len(reads) == 1, reads


async def test_task_statuses_still_seed_for_a_fresh_org(client_for) -> None:
    """The saving is on the hot path only — an org that has none must still get them."""
    t = await make_tenant("perf-statuses-fresh")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        res = await c.get("/api/v1/tasks/statuses", headers=headers)
        assert res.status_code == 200, res.text
        assert [s["key"] for s in res.json()] == ["open", "in_progress", "done"]


# --- the tasks panel: a counted count ------------------------------------------------------ #
async def test_tasks_panel_open_count_is_counted_not_measured(client_for) -> None:
    """``len(items)`` capped at the page size, so a busy client's header read "50" at 300.

    Fifty-one tasks is the smallest number that tells the truth apart from the lie.
    """
    t = await make_tenant("perf-tasks-panel")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        company = await _company(c, headers)
        for i in range(51):
            await _task(c, headers, company_id=company, title=f"T{i}")

        res = await c.get(f"/api/v1/companies/{company}/panels", headers=headers)
        assert res.status_code == 200, res.text
        panel = next(p for p in res.json() if p["key"] == "tasks.company")
        assert panel["data"]["open_count"] == 51
        assert len(panel["data"]["tasks"]) == 50


# --- per-request tenancy overhead ---------------------------------------------------------- #
# Every request in the app pays this, so it is budgeted rather than left to drift. An owner
# holds ``*`` and never reaches horizon resolution at all; a member and a client both do, and
# each used to re-derive a fact the membership statement had already answered.


async def _member_of(t, slug: str, *, role: str = "member"):
    """A second person in ``t``'s org, holding ``role``. Returns their auth headers."""
    other = await make_tenant(f"{slug}-other", email=f"{slug}-other@example.com")
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        await add_membership(session, t.org.id, other.user.id, role=role)
        await session.commit()
    return await auth_cookie(other.user)


#: Statements an ordinary member's ``GET /meta/me`` issues, end to end: the auth user, the two
#: org resolutions, the RLS ``set_config``, the combined membership/permissions/client-role
#: statement, the company-groups resolver, the portal resolver, and the endpoint's own read.
#: It was 10 before #290 — the client-role floor re-ran an ``EXISTS`` the membership statement
#: had already answered, and ``is_portal`` re-ran the contacts join the portal resolver had.
#: Update this deliberately: it is the floor under **every** request in the app.
_MEMBER_REQUEST_BUDGET = 8


async def test_a_staff_request_never_queries_contacts_to_learn_it_is_staff(
    client_for, count_queries
) -> None:
    """The heaviest of the two duplicates, and the one that hit *everyone*.

    ``is_portal`` asked the contacts module "is this user contact-linked?" on every non-owner
    request — so a member loading any screen paid a standalone contacts read to be told no.
    """
    t = await make_tenant("perf-ctx-member")
    headers = await _member_of(t, "perf-ctx-member")
    async with client_for(t.host) as c:
        with count_queries() as counter:
            assert (await c.get("/api/v1/meta/me", headers=headers)).status_code == 200
    # The portal resolver reads contacts through ``FROM memberships JOIN contacts``; a bare
    # ``FROM contacts`` is the duplicate this removed.
    assert counter.matching("from contacts") == [], counter.matching("from contacts")
    # The client-role floor no longer re-asks what the membership statement's ``bool_or``
    # answered: the only membership_roles read is the one inside it.
    assert len(counter.matching("membership_roles")) == 1, counter.matching("membership_roles")
    assert len(counter) == _MEMBER_REQUEST_BUDGET, "\n".join(counter.statements)


async def test_a_client_request_resolves_its_horizon_without_re_deriving_the_role(
    client_for, count_queries
) -> None:
    """A client is the case the skipped resolver exists for — it must still be floored (#252).

    The saving is only safe if the synthesized floor is the same answer the query gave, so the
    behaviour is asserted beside the count: a client with no company assignment sees nothing.
    """
    t = await make_tenant("perf-ctx-client")
    owner_headers = await auth_cookie(t.user)
    headers = await _member_of(t, "perf-ctx-client", role="client")
    async with client_for(t.host) as c:
        await _company(c, owner_headers, name="Alpha")

        with count_queries() as counter:
            res = await c.get("/api/v1/meta/me", headers=headers)
        assert res.status_code == 200
        # Still exactly one membership_roles read: the floor is synthesized, not queried.
        assert len(counter.matching("membership_roles")) == 1, counter.matching(
            "membership_roles"
        )

        # …and the floor really applied. The owner sees the client; the client sees nothing —
        # the empty horizon, not the unrestricted ``None`` a missing resolver would have given.
        assert (await c.get("/api/v1/companies", headers=headers)).json()["items"] == []
        owned = (await c.get("/api/v1/companies", headers=owner_headers)).json()
        assert [r["name"] for r in owned["items"]] == ["Alpha"]


# --- automation rules: grouped, not one query per rule -------------------------------------- #
async def test_automation_rules_load_their_actions_in_one_statement(
    client_for, count_queries
) -> None:
    t = await make_tenant("perf-automation")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        for i in range(5):
            res = await c.post(
                "/api/v1/automation/rules",
                json={
                    "name": f"Rule {i}",
                    "trigger_event": "task.status_changed",
                    "conditions": {"field": "to", "op": "eq", "value": "done"},
                    "enabled": True,
                    "actions": [
                        {
                            "action_type": "notification.send",
                            "config": {"message": "Klaar!", "user_ids": []},
                        }
                    ],
                },
                headers=headers,
            )
            assert res.status_code == 201, res.text

        with count_queries() as counter:
            res = await c.get("/api/v1/automation/rules", headers=headers)
        assert res.status_code == 200, res.text
        rules = res.json()
        assert len(rules) == 5
        assert all(len(r["actions"]) == 1 for r in rules)
        # Five rules, one action load — not five.
        action_reads = counter.matching("from automation_actions")
        assert len(action_reads) == 1, action_reads
