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


async def _entry(
    client, headers, *, company_id: str, minutes: int, day: int, task_id: str | None = None
) -> str:
    started = datetime(2026, 3, 2, 9, 0, tzinfo=UTC) + timedelta(days=day)
    res = await client.post(
        "/api/v1/time/entries",
        json={
            "company_id": company_id,
            "task_id": task_id,
            "started_at": _iso(started),
            "ended_at": _iso(started + timedelta(minutes=minutes)),
            "billable": True,
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _task(
    client, headers, *, company_id: str, title: str, allocated_minutes: int | None = None
) -> str:
    res = await client.post(
        "/api/v1/tasks",
        json={
            "title": title,
            "company_id": company_id,
            "allocated_minutes": allocated_minutes,
        },
        headers=headers,
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


async def test_task_hour_budget_is_one_grouped_query_however_many_tasks(
    client_for, count_queries
) -> None:
    """``?hours=true`` costs exactly one statement more than ``?hours=false``, at any page size.

    The burn is opt-in (#313), so the ordinary list must pay nothing at all for it, and the
    aggregate must be one ``GROUP BY task_id`` rather than the per-task ``GET /time/logged``
    the card uses. Both halves are invisible in the JSON — three tasks and thirty return
    identical rows either way, which is the whole reason this file exists.
    """
    t = await make_tenant("perf-task-hours")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        company = await _company(c, headers)
        # Seed the status vocabulary, or the first read below pays for it (see above).
        assert (await c.get("/api/v1/tasks", headers=headers)).status_code == 200

        async def seed(count: int, offset: int) -> None:
            for i in range(offset, offset + count):
                task = await _task(
                    c, headers, company_id=company, title=f"T{i}", allocated_minutes=120
                )
                await _entry(
                    c, headers, company_id=company, task_id=task, minutes=30, day=i % 20
                )

        async def statements(query: str) -> tuple[int, list[str]]:
            with count_queries() as counter:
                res = await c.get(f"/api/v1/tasks?{query}", headers=headers)
            assert res.status_code == 200, res.text
            assert all(r["allocated_minutes"] == 120 for r in res.json()["items"])
            return len(counter), counter.matching("group by time_entries.task_id")

        await seed(3, 0)
        plain_at_3, plain_burns = await statements("hours=false")
        enriched_at_3, burns_at_3 = await statements("hours=true")
        assert plain_burns == [], "hours=false paid for an aggregate nobody asked for"
        assert len(burns_at_3) == 1, burns_at_3
        assert enriched_at_3 == plain_at_3 + 1

        # Ten times the rows, the same statement count. A per-row read would be +30 here.
        await seed(27, 3)
        plain_at_30, _ = await statements("hours=false")
        enriched_at_30, burns_at_30 = await statements("hours=true")
        assert len(burns_at_30) == 1, burns_at_30
        assert (plain_at_30, enriched_at_30) == (plain_at_3, enriched_at_3)


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


# --- the websites list: a filter that crosses a module boundary, not a prefetch ------------- #
async def test_website_company_filter_never_reads_the_clients_domains(
    client_for, count_queries
) -> None:
    """``?company_id=`` costs the same at two domains as at two hundred.

    A website's client is its *parent domain's* (§6 — no import, a bare-table bridge), and the
    first way to express that was to `SELECT id FROM domains WHERE company_id = …` and hand the
    ids back as an ``IN``. That is an unbounded read whose cost tracks the client's register
    rather than the page, and the count statement paid for it a second time. Correlated, it is
    one more predicate on a statement that was going to run anyway.

    Invisible in the JSON, which is why it is pinned here: the endpoint returns identical rows
    either way (``QueryCounter``'s docstring, and this file's).
    """
    t = await make_tenant("perf-website-filter")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        company = await _company(c, headers)

        async def add_site(i: int) -> None:
            domain = (
                await c.post(
                    "/api/v1/domains",
                    json={"name": f"site-{i}.nl", "company_id": company},
                    headers=headers,
                )
            ).json()["id"]
            created = await c.post(
                "/api/v1/websites", json={"domain_id": domain}, headers=headers
            )
            assert created.status_code == 201, created.text

        async def measure(expected_total: int) -> int:
            with count_queries() as counter:
                res = await c.get(f"/api/v1/websites?company_id={company}", headers=headers)
            assert res.status_code == 200, res.text
            assert res.json()["total"] == expected_total
            # The prefetch's shape, gone: nothing asks the domains table for a bare id list to
            # feed straight back in as a filter.
            flat = [" ".join(s.split()).lower() for s in counter.statements]
            assert not [s for s in flat if s.startswith("select id from domains")], flat
            return len(counter)

        for i in range(2):
            await add_site(i)
        small = await measure(2)
        for i in range(2, 6):
            await add_site(i)
        assert await measure(6) == small


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
#: included). Measured, not guessed — see the umbrella test at the bottom of this file. Fourteen
#: panel providers run in sequence on one session, so the hub's cost is the *sum* of theirs;
#: this is the number that stops it growing one panel at a time.
#:
#: 40 -> 41 when `reporting` (#300) added the fourteenth panel: one `SELECT … FROM reports`,
#: which is the deliberate raise this comment asks for rather than a budget quietly slipping.
#:
#: 41 -> 43 when the domains and websites panels were capped at five rows with an honest total.
#: Both now cost `SELECT … LIMIT 5` + `COUNT(*)` where each used to cost one statement — but
#: only *here*, because this company deliberately has neither a domain nor a website, and both
#: panels used to short-circuit on exactly that: the domains one loaded the client's entire
#: portfolio in a single unbounded query (nothing to load), and the websites one asked for the
#: client's domain ids first and returned early when there were none. On a client who actually
#: has some, the new shape is *cheaper* — the websites panel drops that id prefetch (a
#: correlated EXISTS now), so it went 3 statements -> 2, and the domains panel stopped
#: transferring 400 rows to render 5. The measurement below is the worst case for this change
#: and the best case for the code it replaced; raise it knowingly, as this comment asks.
#:
#: 43 -> 44 (#364): the projects panel stopped taking the newest 50 rows and sorting them
#: active-first *in Python* — a client with 60 projects could lose active ones off a list that
#: claims to lead with them — so it caps at 5, orders in SQL, and pays one grouped `count(*)` for
#: the honest total the "Alle N bekijken" link needs. One statement for a silent truncation, on
#: the two panels beside it (domains, websites) that already made the same trade.
#:
#: 44 -> 45 (#375): a task carries a roster of assignees rather than one column, so the tasks
#: panel reads ``task_assignees`` once for the whole page. One statement for any number of rows —
#: which is the shape that had to be paid for, since the alternative (a chip row resolving per
#: task) is the exact N+1 this file exists to catch.
#:
#: 45 -> 47 (#377): the snelstart panel, which answers "is this client's bookkeeping in step?"
#: in two statements — the relation pairing, and one `GROUP BY status` over the client's invoice
#: pairings. It is deliberately **two** rather than one: folding them would mean loading every
#: invoice link to count it in Python, which is the N+1 this file exists to catch, on a client
#: with two hundred invoices. It also calls SnelStart **never** — a company page must not wait on
#: somebody else's bookkeeping server to render.
#:
#: A *ceiling*, and the change that would breach it is now the interesting one: since #365 the
#: composer skips every panel the caller may not read, so a restricted member's page costs
#: strictly less than this. The budget stays measured as the owner, which is the worst case.
_PANELS_BUDGET = 47

#: The vital-signs strip (#364): one aggregate per contributing module, plus the request's own
#: context and the org timezone each of them resolves. Measured, not guessed — see the test.
_SUMMARY_BUDGET = 26


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


async def test_interaction_rosters_cost_one_query_per_page(client_for, count_queries) -> None:
    """#300: the roster is one batched read for the page, and none when nobody is named.

    A chip per person per row is exactly the shape that is invisible at three rows: the JSON is
    identical either way, and the version that reads ``contacts`` per interaction only shows up
    when a client's timeline is long enough to matter. So the statement count is pinned twice —
    once for the batching, once for the claim ``_contact_rosters`` makes in its own docstring,
    that an all-``NULL`` page provably has no links to fetch.
    """
    t = await make_tenant("perf-inter-roster")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        company = await _company(c, headers)

        # Nobody named: the lead column is NULL on every row, so there is nothing to look up.
        for i in range(3):
            await _interaction(c, headers, company_id=company, subject=f"S{i}", body="x")
        with count_queries() as counter:
            listed = (await c.get("/api/v1/interactions", headers=headers)).json()["items"]
        assert [row["contacts"] for row in listed] == [[], [], []]
        assert counter.matching("from interaction_contacts") == []

        people = []
        for i in range(3):
            res = await c.post(
                "/api/v1/contacts",
                json={"first_name": f"P{i}", "company_ids": [company]},
                headers=headers,
            )
            assert res.status_code == 201, res.text
            people.append(res.json()["id"])

        # Three moments, each naming everybody: nine chips, still one query for the page.
        for i in range(3):
            res = await c.post(
                "/api/v1/interactions",
                json={
                    "kind": "note",
                    "subject": f"M{i}",
                    "company_id": company,
                    "contact_ids": people,
                    "occurred_at": datetime(2026, 3, 3, 9, 0, tzinfo=UTC).isoformat(),
                },
                headers=headers,
            )
            assert res.status_code == 201, res.text

        with count_queries() as counter:
            rows = (await c.get("/api/v1/interactions", headers=headers)).json()["items"]
        named = [row for row in rows if row["contacts"]]
        assert len(named) == 3
        assert all(len(row["contacts"]) == 3 for row in named)
        assert len(counter.matching("from interaction_contacts")) == 1


async def test_a_record_filtered_interaction_page_costs_what_the_unfiltered_one_does(
    client_for, count_queries
) -> None:
    """#323 pointed every panel's truncation notice at ``/interactions?<record>_id=…``.

    A destination reached from every company, project, contact and task page is worth pinning.
    The four filters must stay **predicates on the feed**, never joins that fan it out — three
    are indexed FK comparisons and ``contact_id`` is an ``EXISTS`` over the roster (a join there
    would multiply the folded rows) — so a scoped page issues exactly the statements the
    unscoped one does.

    "The same statements" is only comparable over the **same rows**, so that half is asserted
    with the two filters that narrow to nothing here: a page's label lookups are one batch per
    kind of link *present on it* (``_link_names``), so a page holding no task-linked row is
    legitimately one statement cheaper and comparing those two would measure the fixture.

    What the narrowing filters get instead is the property that actually matters and that no
    functional test can see: **the count does not move when the record gains rows**. Including
    the roll-up (#147), where the project's task ids are fetched once for the filter — three
    more tasks, each with a moment, and not one more statement.

    Every moment names the same contact, so the batched roster read (#300) happens on every
    variant — otherwise the comparison would be measuring which pages have people on them.
    """
    t = await make_tenant("perf-inter-scope")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        company = await _company(c, headers)
        project = await c.post(
            "/api/v1/projects", json={"name": "Herbouw", "company_id": company}, headers=headers
        )
        assert project.status_code == 201, project.text
        project_id = project.json()["id"]
        person = await c.post(
            "/api/v1/contacts",
            json={"first_name": "Iris", "company_ids": [company]},
            headers=headers,
        )
        assert person.status_code == 201, person.text
        contact_id = person.json()["id"]

        async def _moment(**links: str) -> None:
            res = await c.post(
                "/api/v1/interactions",
                json={
                    "kind": "note",
                    "subject": "Overleg",
                    "contact_ids": [contact_id],
                    "occurred_at": datetime(2026, 3, 4, 9, 0, tzinfo=UTC).isoformat(),
                    **links,
                },
                headers=headers,
            )
            assert res.status_code == 201, res.text

        async def _tasks_with_moments(count: int, offset: int) -> None:
            for i in range(count):
                task = await c.post(
                    "/api/v1/tasks",
                    json={
                        "title": f"T{offset + i}",
                        "company_id": company,
                        "project_id": project_id,
                    },
                    headers=headers,
                )
                assert task.status_code == 201, task.text
                await _moment(task_id=task.json()["id"])

        await _moment(company_id=company)
        await _moment(project_id=project_id)
        await _tasks_with_moments(3, 0)

        # The batched roster read, named by its own ordering rather than by its table: with
        # ``contact_id`` set, the filter's EXISTS mentions ``interaction_contacts`` inside the
        # feed statement and the count statement too, and a substring match would count three.
        roster = "order by interaction_contacts.interaction_id"

        async def _cost(query: str) -> tuple[int, int]:
            with count_queries() as counter:
                res = await c.get(f"/api/v1/interactions{query}", headers=headers)
            assert res.status_code == 200, res.text
            assert len(counter.matching(roster)) == 1, counter.statements
            return len(counter), res.json()["total"]

        baseline, everything = await _cost("")
        assert everything == 5

        # Same five rows, filtered two different ways: the FK comparison and the roster EXISTS
        # each cost nothing at all. An ``EXISTS`` that had been written as a join would also
        # have multiplied the folded feed, which is why the total is asserted beside the count.
        assert await _cost(f"?company_id={company}") == (baseline, 5)
        assert await _cost(f"?contact_id={contact_id}") == (baseline, 5)

        # The roll-up (#147): the project's own moment plus its three tasks'.
        scoped, total = await _cost(f"?project_id={project_id}&include=tasks")
        assert total == 4

        # And now the property the JSON cannot show. Three more tasks, three more moments — the
        # task ids are one prefetch for the filter and the labels one batch for the page, so
        # nothing about this read grows with the project.
        await _tasks_with_moments(3, 3)
        assert await _cost(f"?project_id={project_id}&include=tasks") == (scoped, 7)
        assert await _cost(f"?company_id={company}") == (baseline, 8)


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


async def test_company_summary_has_a_query_budget(client_for, count_queries) -> None:
    """The vital-signs strip is an aggregate per module, never a list (#364).

    Every tile is a `count`/`sum`/`min`/`max` over one indexed predicate, so a client with four
    hundred invoices and ten years of hours costs what a new one does. The failure this budget
    catches is the tempting one: answering "openstaand" by loading the ledger and summing it in
    Python, which passes every functional test and is invisible in the JSON.

    Same umbrella shape as the panels budget above — the providers run in sequence on one
    session, so the cost is the sum. Raise it knowingly when a module contributes a sign.
    """
    t = await make_tenant("perf-summary-budget")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        company = await _company(c, headers)
        await _entry(c, headers, company_id=company, minutes=60, day=0)
        await _task(c, headers, company_id=company, title="Werk")
        await _interaction(c, headers, company_id=company, subject="Hoi", body="tekst")

        with count_queries() as counter:
            res = await c.get(f"/api/v1/companies/{company}/summary", headers=headers)
        assert res.status_code == 200, res.text
        tiles = {tile["key"] for tile in res.json()}
        # Anti-vacuum: an empty strip would meet any budget, so the assertion names tiles this
        # client actually has. `time.month` is deliberately absent — `_entry` books against a
        # fixed 2026-03 date and the tile counts *this* month, which is the "nothing is not a
        # number" rule doing its job rather than a gap in the fixture.
        assert {"tasks.open", "interactions.last"} <= tiles, tiles
        assert len(counter) <= _SUMMARY_BUDGET, (
            f"{len(counter)} statements, budget {_SUMMARY_BUDGET}:\n"
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


# --- ticking a to-do: the task page's most-repeated write ---------------------------------- #
async def test_ticking_a_checklist_item_costs_the_same_however_long_the_list(
    client_for, count_queries
) -> None:
    """A tick is the gesture a task page gets dozens of times a day, so it is a budget.

    It used to be free to be sloppy here, because the browser paid for a whole page reload on
    top of it: the toggle called SvelteKit's ``update()``, which invalidates every load above
    it — the two layouts and the task page, sixteen GETs, one of them the eight-round-trip
    ``GET /tasks/{id}`` — before the checkbox even changed colour. The tick is optimistic now
    and invalidates nothing, so this PATCH *is* the cost of the gesture.

    The property is that nothing on the path reads the item's siblings: a checklist of eleven
    must cost exactly what a checklist of one costs. Invisible in the JSON, as ever.
    """
    t = await make_tenant("perf-checklist-tick")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        company = await _company(c, headers)
        task = await _task(c, headers, company_id=company, title="Onboarding")
        created = await c.post(
            f"/api/v1/tasks/{task}/checklists", json={"title": "Stappen"}, headers=headers
        )
        assert created.status_code == 201, created.text
        checklist = created.json()["id"]

        async def add_item(title: str) -> str:
            res = await c.post(
                f"/api/v1/tasks/{task}/checklists/{checklist}/items",
                json={"title": title},
                headers=headers,
            )
            assert res.status_code == 201, res.text
            return res.json()["id"]

        async def tick(item_id: str, *, done: bool) -> int:
            with count_queries() as counter:
                res = await c.patch(
                    f"/api/v1/tasks/{task}/checklists/{checklist}/items/{item_id}",
                    json={"done": done},
                    headers=headers,
                )
            assert res.status_code == 200, res.text
            assert res.json()["done"] is done
            # Whatever else moves, the write is one UPDATE plus the trail line the tick owes
            # the activity feed (#61) — never a re-read of the list it belongs to.
            assert len(counter.matching("update task_checklist_items")) == 1, counter.statements
            assert len(counter.matching("insert into task_activities")) == 1, counter.statements
            assert counter.matching("checklist_id in") == [], counter.statements
            return len(counter)

        first = await add_item("Stap 1")
        small = await tick(first, done=True)

        for i in range(2, 12):
            await add_item(f"Stap {i}")
        large = await tick(first, done=False)

        assert small == large, (small, large)


# --- the task card: one open, one fixed budget --------------------------------------------- #
async def test_task_detail_costs_the_same_however_much_the_card_carries(
    client_for, count_queries
) -> None:
    """``GET /tasks/{id}`` is the most expensive read on the busiest screen, so it is a budget.

    Two properties, and the second is why the number is written down at all. The obvious one:
    nothing on the card is fetched per row — comments, planned blocks (#188) and the logged-
    minutes total are each one statement whether the task carries none or a dozen.

    The written-down number is the guard against the *other* way this page gets slower, which is
    the one that reads as a feature rather than as a regression: someone needs a fact the card
    does not have yet — a running timer, an unlogged block's window, a budget remainder — and
    reaches for one more round trip on the way in to serve a dialog most opens never see. #314
    is exactly that pressure and deliberately paid nothing: everything the finish prompt suggests
    from is already on this response. A rise here means the next feature did not.

    14 -> 15 (#375): the assignee roster. One statement, and one is the floor — the card draws
    every person on the task, and the column it replaces could only ever name one of them.
    """
    t = await make_tenant("perf-task-detail")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        company = await _company(c, headers)
        task = await _task(c, headers, company_id=company, title="Homepage herzien")

        with count_queries() as bare:
            assert (await c.get(f"/api/v1/tasks/{task}", headers=headers)).status_code == 200
        assert len(bare) == 15, bare.statements

        for i in range(5):
            assert (
                await c.post(
                    f"/api/v1/tasks/{task}/comments", json={"body": f"Opmerking {i}"},
                    headers=headers,
                )
            ).status_code == 201
            assert (
                await c.post(
                    "/api/v1/tasks/schedules",
                    json={
                        "task_id": task,
                        "day": "2026-07-20",
                        "start_time": f"0{i + 1}:00",
                        "duration_minutes": 60,
                    },
                    headers=headers,
                )
            ).status_code == 201

        with count_queries() as loaded:
            detail = await c.get(f"/api/v1/tasks/{task}", headers=headers)
        assert detail.status_code == 200, detail.text
        assert len(detail.json()["comments"]) == 5
        assert len(loaded) == len(bare), (loaded.statements, bare.statements)


async def test_the_ai_status_poll_stays_a_poll(client_for, count_queries) -> None:
    """``GET /tasks/{id}/ai-status`` (#327) is fetched **on a timer**, so its cost is a budget.

    It exists precisely so the card does not re-fetch itself every few seconds while an email is
    being read. Two assertions, and they say different things. The **task read is exactly one
    statement** — that is the property this endpoint owns, and a rise means somebody put a
    lookup behind a one-column answer. The **total stays far below the card's own** — that is
    the property that justifies the endpoint existing at all, since polling multiplies whatever
    it costs; asserted as a ceiling rather than an exact number because most of the remainder is
    the ``require_context`` preamble every route pays, and pinning that here would fail this
    test for changes that have nothing to do with it.
    """
    t = await make_tenant("perf-ai-status")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        company = await _company(c, headers)
        task = await _task(c, headers, company_id=company, title="Homepage")
        with count_queries() as counter:
            polled = await c.get(f"/api/v1/tasks/{task}/ai-status", headers=headers)
        assert polled.status_code == 200, polled.text
        assert polled.json()["ai_status"] is None
        task_reads = counter.matching("from tasks")
        assert len(task_reads) == 1, task_reads
        assert len(counter) <= 8, counter.statements


# --- the websites/domains section layout: pickers pay picker prices (#290, extended) --------- #
async def test_available_domains_is_one_statement_whatever_the_register_holds(
    client_for, count_queries
) -> None:
    """The create picker's vocabulary is one query, and it does not grow with the portfolio.

    It used to be a subtraction in the browser: every domain (200 rows, fully resolved) minus
    every website (200 rows, fully resolved). Both halves ran their service's whole display
    attach — register facts, TLD prices, party labels, provider and hosting names — to produce a
    list of ``{id, name}``, and the subtraction was *wrong* past 200 websites, offering a taken
    domain that then 409'd on save.

    Pinned as a shape because the JSON is identical either way, which is this file's whole point.
    """
    t = await make_tenant("perf-available-domains")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        company = await _company(c, headers)

        async def add_domain(i: int, *, with_site: bool) -> None:
            domain = (
                await c.post(
                    "/api/v1/domains",
                    json={"name": f"free-{i}.nl", "company_id": company},
                    headers=headers,
                )
            ).json()["id"]
            if with_site:
                created = await c.post(
                    "/api/v1/websites", json={"domain_id": domain}, headers=headers
                )
                assert created.status_code == 201, created.text

        async def measure(expected_free: int) -> int:
            with count_queries() as counter:
                res = await c.get("/api/v1/websites/available-domains", headers=headers)
            assert res.status_code == 200, res.text
            assert len(res.json()) == expected_free, res.text
            # Never the two list reads this replaced, and never their attach work.
            flat = [" ".join(s.split()).lower() for s in counter.statements]
            assert not [s for s in flat if "domain_tld_prices" in s], flat
            return len(counter)

        await add_domain(1, with_site=False)
        await add_domain(2, with_site=True)
        small = await measure(1)

        for i in range(3, 9):
            await add_domain(i, with_site=i % 2 == 0)
        large = await measure(4)

        assert small == large, (small, large)


async def test_definitions_batch_reads_the_set_once_for_every_type(
    client_for, count_queries
) -> None:
    """Five entity types, one read — not five reads of the same set.

    ``definitions()`` loads the tenant's whole definition set and filters it in Python, so asking
    per entity type cost a full read *and* a round-trip each. The websites section layout asked
    five times on every entry to the section.
    """
    t = await make_tenant("perf-defs-batch")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        types = ["website", "hosting", "domain", "company", "contact"]
        query = "&".join(f"entity_type={x}" for x in types)
        with count_queries() as counter:
            res = await c.get(f"/api/v1/custom-fields/definitions/batch?{query}", headers=headers)
        assert res.status_code == 200, res.text
        # Every requested type is a key, empty list included — a caller never has to tell
        # "no definitions" from "did not come back".
        assert sorted(res.json()) == sorted(types), res.text
        assert len(counter.matching("from custom_field_definitions")) == 1, counter.statements


async def test_meta_false_skips_the_display_attach_a_picker_discards(
    client_for, count_queries
) -> None:
    """``meta=false`` (+ ``count=false``) is strictly fewer statements, and the ids still answer.

    The rows a picker draws are ``{id, name}``; resolving the client name, the provider names,
    the party labels, the register facts and the current TLD price is work it throws away.
    """
    t = await make_tenant("perf-domains-meta")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        company = await _company(c, headers)
        for i in range(3):
            res = await c.post(
                "/api/v1/domains",
                json={"name": f"meta-{i}.nl", "company_id": company},
                headers=headers,
            )
            assert res.status_code == 201, res.text

        with count_queries() as full:
            res = await c.get("/api/v1/domains", headers=headers)
        assert res.status_code == 200, res.text
        assert res.json()["items"][0]["company_name"] == "Acme"

        with count_queries() as slim:
            res = await c.get("/api/v1/domains?meta=false&count=false", headers=headers)
        assert res.status_code == 200, res.text
        body = res.json()
        # The rows are still there and still identify themselves — only the resolution is gone.
        assert len(body["items"]) == 3, body
        assert body["total"] == 3, body  # `count=false` reports the page length
        assert all(d["name"] and d["company_id"] for d in body["items"]), body
        assert all(d["company_name"] == "" for d in body["items"]), body
        assert len(slim) < len(full), (len(slim), len(full))
        assert slim.matching("from domain_tld_prices") == [], slim.statements


async def test_the_monitor_list_costs_the_same_however_many_groups_there_are(
    client_for, count_queries, monkeypatch
) -> None:
    """``meta=true`` resolves every group name in **one** query, not one per monitor.

    The shape this file exists to catch: a group is a monitor in the same table, so the obvious
    implementation resolves ``parent_id`` per row and is indistinguishable in the JSON from the
    grouped one. It is also the shape most likely to be written here, because the lookup is a
    self-reference and looks free.

    Two groups and four children rather than one and one, because a per-row read and a grouped
    read agree at a single row and only diverge once the same parent is asked for twice.
    """
    from app.integrations.uptime import client as kuma_client
    from tests.uptime_fake import FakeKuma

    fake = FakeKuma()
    monkeypatch.setattr(kuma_client, "_connector", fake.connector)
    first = fake.add_group("hosting klanten")
    second = fake.add_group("intern")
    for i in range(4):
        fake.add(name=f"site-{i}", parent=first if i % 2 else second, url=f"https://s{i}.nl")

    t = await make_tenant("perf-uptime-groups")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        res = await c.post(
            "/api/v1/uptime/instances",
            json={"name": "Kuma", "mode": "managed", "base_url": "https://kuma.example.nl"},
            headers=headers,
        )
        assert res.status_code == 201, res.text
        instance = res.json()["id"]
        res = await c.post(
            f"/api/v1/uptime/instances/{instance}/enrol",
            json={"username": "admin", "password": "secret"},
            headers=headers,
        )
        assert res.status_code == 200, res.text
        res = await c.post(f"/api/v1/uptime/instances/{instance}/sync", headers=headers)
        assert res.status_code == 200, res.text
        assert res.json()["groups"] == 2, res.text

        with count_queries() as counter:
            res = await c.get("/api/v1/uptime/monitors?meta=true&count=false", headers=headers)
        assert res.status_code == 200, res.text
        items = res.json()["items"]
        assert len(items) == 6, items
        named = [m for m in items if m["parent_name"]]
        assert len(named) == 4, named
        # One statement for the page, one for every group name on it, and one for every child
        # count (#321 — what makes the group delete guard predictable). Three, whatever the
        # page holds: not one per child, and not one per group.
        reads = counter.matching("from uptime_monitors")
        assert len(reads) == 3, counter.statements

        # And a caller that did not ask pays for neither.
        with count_queries() as slim:
            res = await c.get("/api/v1/uptime/monitors?count=false", headers=headers)
        assert res.status_code == 200, res.text
        assert all(m["parent_name"] is None for m in res.json()["items"])
        assert len(slim.matching("from uptime_monitors")) == 1, slim.statements


# --- /companies: a status *set* is still one WHERE, not one read per status ---------------- #
async def test_company_status_set_costs_no_extra_read(client_for, count_queries) -> None:
    """The default Klanten view narrows to a set of statuses (#329) and pays nothing for it.

    Worth writing down because the tempting implementation of "everything except archived" is a
    read per status folded together in Python — which returns byte-identical JSON at four
    statuses and four clients, and scales with both. Two statements: the page, and its count.
    Neither the number of statuses named nor the number of rows behind them moves the number.
    """
    t = await make_tenant("perf-company-status")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)

        async def add(name: str, status: str) -> None:
            res = await c.post(
                "/api/v1/companies", json={"name": name, "status": status}, headers=headers
            )
            assert res.status_code == 201, res.text

        async def measure(expected: list[str]) -> int:
            with count_queries() as counter:
                res = await c.get(
                    "/api/v1/companies",
                    params={"status": "lead,onboarding,active,offboarding"},
                    headers=headers,
                )
            assert res.status_code == 200, res.text
            assert [i["name"] for i in res.json()["items"]] == expected
            return len(counter.matching("from companies"))

        await add("Aannemer", "lead")
        await add("Zonwering", "archived")
        first = await measure(["Aannemer"])
        assert first == 2, first

        await add("Bakkerij", "active")
        await add("Molenaar", "offboarding")
        await add("Notaris", "archived")
        assert await measure(["Aannemer", "Bakkerij", "Molenaar"]) == first

        # And the narrowing itself is free: the unfiltered list costs exactly the same.
        with count_queries() as unfiltered:
            res = await c.get("/api/v1/companies", headers=headers)
        assert res.json()["total"] == 5
        assert len(unfiltered.matching("from companies")) == first
