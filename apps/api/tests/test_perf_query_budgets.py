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
from tests.test_invoicing_api import _setup_org as _invoicing_setup_org


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
    # They hold two memberships (their own tenant and ``t``); the session under test is ``t``.
    return await auth_cookie(other.user, org_id=t.org.id)


#: Statements an ordinary member's ``GET /meta/me`` issues, end to end: the auth user, the two
#: org resolutions, the RLS ``set_config``, the combined membership/permissions/client-role
#: statement, the company-groups resolver, the portal resolver, and the endpoint's own read.
#: It was 10 before #290 — the client-role floor re-ran an ``EXISTS`` the membership statement
#: had already answered, and ``is_portal`` re-ran the contacts join the portal resolver had.
#: Update this deliberately: it is the floor under **every** request in the app.
_MEMBER_REQUEST_BUDGET = 8

#: Statements a populated company's panel composition may issue, end to end (request context
#: included). Measured, not guessed — see the umbrella test at the bottom of this file. Thirteen
#: panel providers run in sequence on one session, so the hub's cost is the *sum* of theirs;
#: this is the number that stops it growing one panel at a time.
_PANELS_BUDGET = 40


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


# --- list shapes: a row carries only what the list draws ------------------------------------ #
async def _interaction(client, headers, *, company_id: str, subject: str, body: str) -> str:
    res = await client.post(
        "/api/v1/interactions",
        json={
            "kind": "note",
            "subject": subject,
            "body_text": body,
            "company_id": company_id,
            "occurred_at": datetime(2026, 3, 2, 9, 0, tzinfo=UTC).isoformat(),
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def test_interaction_rows_carry_no_body_until_asked(client_for) -> None:
    """A page of full e-mail bodies to render a snippet column was the bulk of the response."""
    t = await make_tenant("perf-inter-body")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        company = await _company(c, headers)
        row_id = await _interaction(
            c, headers, company_id=company, subject="Hoi", body="De hele lange tekst"
        )

        listed = (await c.get("/api/v1/interactions", headers=headers)).json()["items"]
        assert [r["subject"] for r in listed] == ["Hoi"]
        # The key stays — the response shape is unchanged, only the payload is lighter.
        assert "body_text" in listed[0]
        assert listed[0]["body_text"] is None

        opt_in = (await c.get("/api/v1/interactions?with_body=true", headers=headers)).json()
        assert opt_in["items"][0]["body_text"] == "De hele lange tekst"
        # …and the single-row read the detail modal uses always carries it.
        one = (await c.get(f"/api/v1/interactions/{row_id}", headers=headers)).json()
        assert one["body_text"] == "De hele lange tekst"


async def test_interaction_count_can_be_skipped(client_for, count_queries) -> None:
    """The fold makes the total a second full pass, not a free by-product."""
    t = await make_tenant("perf-inter-count")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        company = await _company(c, headers)
        for i in range(3):
            await _interaction(c, headers, company_id=company, subject=f"S{i}", body="x")

        with count_queries() as counter:
            counted = (await c.get("/api/v1/interactions?limit=2", headers=headers)).json()
        assert counted["total"] == 3
        assert len(counter.matching("count(distinct")) == 1

        with count_queries() as counter:
            skipped = (
                await c.get("/api/v1/interactions?limit=2&count=false", headers=headers)
            ).json()
        assert counter.matching("count(distinct") == []
        # `total` degrades to the page length, the same contract every other list uses.
        assert skipped["total"] == len(skipped["items"]) == 2


async def test_invoice_rows_carry_no_lines_until_asked(client_for, count_queries) -> None:
    t = await make_tenant("perf-invoice-lines")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        company = await _company(c, headers)
        res = await c.post(
            "/api/v1/invoicing/invoices",
            json={
                "company_id": company,
                "lines": [{"description": "Werk", "quantity": "2", "unit_price": "100"}],
            },
            headers=headers,
        )
        assert res.status_code == 201, res.text
        invoice_id = res.json()["id"]

        with count_queries() as counter:
            slim = (
                await c.get("/api/v1/invoicing/invoices?lines=false", headers=headers)
            ).json()["items"][0]
        assert counter.matching("from invoice_lines") == []
        assert slim["lines"] == [] and slim["tax_groups"] == []
        # The figures the index actually draws are columns, so they still answer.
        assert slim["total"] == "200.00"
        assert slim["outstanding"] == "200.00"
        assert slim["overdue"] is False

        full = (await c.get("/api/v1/invoicing/invoices", headers=headers)).json()["items"][0]
        assert len(full["lines"]) == 1
        assert full["total"] == slim["total"], "the total is a column; slimming must not change it"
        # The detail view is never slimmed.
        detail = (
            await c.get(f"/api/v1/invoicing/invoices/{invoice_id}", headers=headers)
        ).json()
        assert len(detail["lines"]) == 1


async def test_contacts_count_can_be_skipped(client_for, count_queries) -> None:
    """The service supported this from the start; the router never forwarded it, so every
    picker that asked for ``count=false`` was silently paying for the total anyway."""
    t = await make_tenant("perf-contacts-count")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        for i in range(3):
            res = await c.post(
                "/api/v1/contacts", json={"first_name": f"P{i}"}, headers=headers
            )
            assert res.status_code == 201, res.text

        assert (await c.get("/api/v1/contacts", headers=headers)).json()["total"] == 3
        with count_queries() as counter:
            page = (
                await c.get("/api/v1/contacts?limit=2&count=false", headers=headers)
            ).json()
        assert page["total"] == len(page["items"]) == 2
        assert counter.matching("count(*)") == [], counter.matching("count(*)")


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


# --- the dashboard burn tile: sorted and cut on the server ---------------------------------- #
async def test_dashboard_budgets_returns_only_the_hottest_rows(client_for, count_queries) -> None:
    """The widget asked for 200 projects with full enrichment and kept four."""
    t = await make_tenant("perf-dash-budgets")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        company = await _company(c, headers)
        # Three budgeted projects burning 25% / 50% / 75%, plus one with no budget at all.
        for name, budget, minutes in (("Koel", 8, 120), ("Warm", 8, 240), ("Heet", 8, 360)):
            res = await c.post(
                "/api/v1/projects",
                json={
                    "name": name,
                    "company_id": company,
                    "budget_hours": budget,
                    "budget_period": "total",
                },
                headers=headers,
            )
            assert res.status_code == 201, res.text
            project = res.json()["id"]
            started = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
            entry = await c.post(
                "/api/v1/time/entries",
                json={
                    "project_id": project,
                    "started_at": _iso(started),
                    "ended_at": _iso(started + timedelta(minutes=minutes)),
                },
                headers=headers,
            )
            assert entry.status_code == 201, entry.text
        unbudgeted = await c.post(
            "/api/v1/projects", json={"name": "Geen budget", "company_id": company}, headers=headers
        )
        assert unbudgeted.status_code == 201, unbudgeted.text

        with count_queries() as counter:
            res = await c.get("/api/v1/projects/dashboard-budgets?limit=2", headers=headers)
        assert res.status_code == 200, res.text
        rows = res.json()
        # Hottest first, cut to the asked-for length, and a project with no budget is absent.
        assert [r["name"] for r in rows] == ["Heet", "Warm"]
        assert rows[0]["hours"]["spent_hours"] == 6.0
        assert rows[0]["hours"]["budget_hours"] == 8.0
        # The burn enrichment stays one grouped aggregate, whatever the project count.
        assert len(counter.matching("from time_entries")) == 1


# --- the company hub: an umbrella budget over every panel ----------------------------------- #
async def test_company_panels_have_a_query_budget(client_for, count_queries) -> None:
    """One number that stops the hub regressing panel by panel (#290).

    `GET /companies/{id}/panels` composes a provider per enabled module, in sequence — correct
    on a single ``AsyncSession``, and the reason the hub's cost is the *sum* of its panels. No
    individual panel review catches "each one added a query"; this does.

    The company is deliberately populated (an entry, a task, a contact, an interaction), because
    a panel over an empty table often short-circuits and would hide exactly the fan-out this
    budget exists to catch. Raising the number is fine when a panel is added — do it knowingly,
    with the new panel's own count_queries test beside it.
    """
    t = await make_tenant("perf-panels-budget")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        company = await _company(c, headers)
        await _entry(c, headers, company_id=company, minutes=60, day=0)
        await _task(c, headers, company_id=company, title="Werk")
        await _interaction(c, headers, company_id=company, subject="Hoi", body="tekst")
        contact = await c.post(
            "/api/v1/contacts", json={"first_name": "Jan", "company_ids": [company]},
            headers=headers,
        )
        assert contact.status_code == 201, contact.text

        with count_queries() as counter:
            res = await c.get(f"/api/v1/companies/{company}/panels", headers=headers)
        assert res.status_code == 200, res.text
        assert len(res.json()) >= 4
        assert len(counter) <= _PANELS_BUDGET, (
            f"{len(counter)} statements, budget {_PANELS_BUDGET}:\n"
            + "\n".join(counter.statements)
        )


async def test_credit_links_are_batched_and_the_list_never_pays_for_them(
    client_for, count_queries
) -> None:
    """The two halves of a correction link to each other without going per row.

    The FK points one way only, so naming the credit notes that corrected an invoice needs a
    reverse read. It is a *detail* concern: the index draws the `credited` flag, which is a
    column. So the list must not run it at all, and the detail must run it once however many
    credit notes point home — the shape that is invisible in the JSON and only shows up at
    three hundred rows.
    """
    t = await make_tenant("perf-credit-links")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        await _invoicing_setup_org(c, headers)
        company = await _company(c, headers)

        async def issued(price: str) -> dict:
            inv = (
                await c.post(
                    "/api/v1/invoicing/invoices",
                    json={
                        "company_id": company,
                        "lines": [
                            {"description": "W", "quantity": "1", "unit_price": price}
                        ],
                    },
                    headers=headers,
                )
            ).json()
            return (
                await c.post(
                    f"/api/v1/invoicing/invoices/{inv['id']}/issue",
                    json={},
                    headers=headers,
                )
            ).json()

        # One invoice corrected by two partial credit notes, plus two unrelated invoices so
        # a per-row read would be visible as more than one statement.
        invoice = await issued("500")
        for _ in range(2):
            note = (
                await c.post(
                    f"/api/v1/invoicing/invoices/{invoice['id']}/credit", headers=headers
                )
            ).json()
            await c.patch(
                f"/api/v1/invoicing/invoices/{note['id']}",
                json={
                    "lines": [
                        {"description": "W", "quantity": "1", "unit_price": "-100"}
                    ]
                },
                headers=headers,
            )
            await c.post(
                f"/api/v1/invoicing/invoices/{note['id']}/issue", json={}, headers=headers
            )
        await issued("300")
        await issued("200")

        with count_queries() as counter:
            rows = (await c.get("/api/v1/invoicing/invoices", headers=headers)).json()[
                "items"
            ]
        assert counter.matching("credit_for_id in") == [], "the list draws a column, not a join"
        credited_row = next(r for r in rows if r["id"] == invoice["id"])
        assert credited_row["credited"] is True
        assert credited_row["credited_total"] == "242.00"
        assert credited_row["outstanding"] == "363.00"  # 605 − 242
        assert credited_row["credit_notes"] == [], "links are a detail concern"

        with count_queries() as counter:
            detail = (
                await c.get(
                    f"/api/v1/invoicing/invoices/{invoice['id']}", headers=headers
                )
            ).json()
        assert len(detail["credit_notes"]) == 2
        assert len(counter.matching("credit_for_id in")) == 1, "one grouped read, never per note"
