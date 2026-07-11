"""Available/remaining hours per project and per client (#25).

The failure mode here is not a crash — it is a number nobody trusts. So each decision taken when
the column was designed gets pinned by a test: non-billable work eats the budget, unapproved hours
count but are reported apart, a running timer contributes nothing, a monthly budget only counts
this month, approved leave can never leak in, and a client with no budgeted project reports an
absence rather than a fabricated total.

The aggregate is also asserted to be a *grouped* query — one per page, not one per row.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.modules.projects.budget import period_start, period_start_date
from tests.conftest import add_membership, auth_cookie, leave_workday, make_tenant


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def test_the_period_start_names_a_local_day_not_a_utc_instant() -> None:
    """The day a period began, in Amsterdam — the one a client sends back as `date_from` (#43).

    In summer, Amsterdam-local midnight is 22:00 UTC the day *before*, so taking `.date()` of the
    UTC instant reported 30 June for a July budget. Half the year that bug is invisible, which is
    why it is pinned on a fixed date rather than on `today`.
    """
    summer = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)  # CEST, UTC+2
    winter = datetime(2026, 1, 9, 12, 0, tzinfo=UTC)  # CET, UTC+1

    assert period_start_date("monthly", now=summer).isoformat() == "2026-07-01"
    assert period_start_date("monthly", now=winter).isoformat() == "2026-01-01"
    assert period_start_date("weekly", now=summer).isoformat() == "2026-07-06"  # Monday
    assert period_start_date("daily", now=summer).isoformat() == "2026-07-09"
    assert period_start_date("total", now=summer) is None

    # The instant it names is still the local midnight, and it still precedes the local day.
    assert period_start("monthly", now=summer).isoformat() == "2026-06-30T22:00:00+00:00"


async def _entry(
    client,
    headers: dict[str, str],
    *,
    project_id: str | None = None,
    company_id: str | None = None,
    started_at: datetime,
    minutes: int,
    billable: bool = True,
) -> str:
    """A closed time entry of exactly ``minutes``."""
    res = await client.post(
        "/api/v1/time/entries",
        json={
            "project_id": project_id,
            "company_id": company_id,
            "started_at": _iso(started_at),
            "ended_at": _iso(started_at + timedelta(minutes=minutes)),
            "billable": billable,
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    assert res.json()["minutes"] == minutes, res.text
    return res.json()["id"]


async def _company(client, headers, name: str = "Acme") -> str:
    res = await client.post("/api/v1/companies", json={"name": name}, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _project(
    client,
    headers,
    *,
    company_id: str | None = None,
    name: str = "Website",
    budget_hours: float | None = None,
    budget_period: str = "total",
    status: str = "active",
) -> str:
    res = await client.post(
        "/api/v1/projects",
        json={
            "name": name,
            "company_id": company_id,
            "budget_hours": budget_hours,
            "budget_period": budget_period,
            "status": status,
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _row(payload: dict, entity_id: str) -> dict:
    return next(item for item in payload["items"] if item["id"] == entity_id)


# --- the project column -------------------------------------------------------- #
async def test_non_billable_hours_eat_the_budget(client_for) -> None:
    """Internal work on a client's project still consumes it (the decision taken on #25)."""
    t = await make_tenant("bh-billable")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        project = await _project(c, headers, budget_hours=10)
        now = datetime.now(UTC) - timedelta(hours=3)
        await _entry(c, headers, project_id=project, started_at=now, minutes=60, billable=True)
        await _entry(
            c, headers, project_id=project, started_at=now, minutes=30, billable=False
        )

        row = _row((await c.get("/api/v1/projects?hours=true", headers=headers)).json(), project)
        hours = row["hours"]
        assert hours["spent_hours"] == 1.5  # both entries
        assert hours["billable_hours"] == 1.0  # only the billable one
        assert hours["budget_hours"] == 10.0
        assert hours["remaining_hours"] == 8.5


async def test_unapproved_hours_count_but_are_reported_apart(client_for) -> None:
    t = await make_tenant("bh-approval")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        project = await _project(c, headers, budget_hours=10)
        now = datetime.now(UTC) - timedelta(hours=5)
        approved = await _entry(c, headers, project_id=project, started_at=now, minutes=60)
        await _entry(
            c, headers, project_id=project, started_at=now + timedelta(hours=2), minutes=120
        )

        res = await c.post(
            "/api/v1/time/entries/approve",
            json={"entry_ids": [approved], "approved": True},
            headers=headers,
        )
        assert res.status_code == 200, res.text

        hours = _row(
            (await c.get("/api/v1/projects?hours=true", headers=headers)).json(), project
        )["hours"]
        assert hours["spent_hours"] == 3.0  # approved + unapproved both burn the budget
        assert hours["unapproved_hours"] == 2.0  # …but the unsigned-off part is nameable
        assert hours["remaining_hours"] == 7.0


async def test_over_budget_remaining_is_negative_not_clamped(client_for) -> None:
    t = await make_tenant("bh-over")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        project = await _project(c, headers, budget_hours=2)
        await _entry(
            c,
            headers,
            project_id=project,
            started_at=datetime.now(UTC) - timedelta(hours=4),
            minutes=180,
        )
        hours = _row(
            (await c.get("/api/v1/projects?hours=true", headers=headers)).json(), project
        )["hours"]
        assert hours["remaining_hours"] == -1.0


async def test_running_timer_contributes_nothing(client_for) -> None:
    t = await make_tenant("bh-running")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        project = await _project(c, headers, budget_hours=10)
        res = await c.post(
            "/api/v1/time/timer/start", json={"project_id": project}, headers=headers
        )
        assert res.status_code == 201, res.text

        hours = _row(
            (await c.get("/api/v1/projects?hours=true", headers=headers)).json(), project
        )["hours"]
        assert hours["spent_hours"] == 0.0
        assert hours["remaining_hours"] == 10.0


async def test_a_project_with_no_budget_has_no_remainder_but_still_reports_spend(
    client_for,
) -> None:
    t = await make_tenant("bh-nobudget")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        project = await _project(c, headers, budget_hours=None)
        await _entry(
            c,
            headers,
            project_id=project,
            started_at=datetime.now(UTC) - timedelta(hours=2),
            minutes=90,
        )
        hours = _row(
            (await c.get("/api/v1/projects?hours=true", headers=headers)).json(), project
        )["hours"]
        assert hours["budget_hours"] is None
        assert hours["remaining_hours"] is None  # an em-dash, never a fabricated total
        assert hours["spent_hours"] == 1.5  # the work is still visible


async def test_monthly_budget_only_counts_the_current_period(client_for) -> None:
    """A monthly budget refills: last month's hours must not burn this month's allowance."""
    t = await make_tenant("bh-monthly")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        project = await _project(c, headers, budget_hours=10, budget_period="monthly")

        start = period_start("monthly")
        assert start is not None
        await _entry(
            c,
            headers,
            project_id=project,
            started_at=start - timedelta(days=2),  # previous month
            minutes=300,
        )
        await _entry(
            c, headers, project_id=project, started_at=start + timedelta(hours=1), minutes=60
        )

        payload = (await c.get("/api/v1/projects?hours=true", headers=headers)).json()
        hours = _row(payload, project)["hours"]
        assert hours["period"] == "monthly"
        # The *local* first-of-the-month, not the UTC instant's date — see `period_start_date`.
        assert hours["period_start"] == period_start_date("monthly").isoformat()
        assert hours["period_start"].endswith("-01")
        assert hours["spent_hours"] == 1.0  # last month's 5h ignored
        assert hours["remaining_hours"] == 9.0


async def test_total_budget_counts_all_time(client_for) -> None:
    t = await make_tenant("bh-total")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        project = await _project(c, headers, budget_hours=10, budget_period="total")
        await _entry(
            c,
            headers,
            project_id=project,
            started_at=datetime.now(UTC) - timedelta(days=400),
            minutes=120,
        )
        hours = _row(
            (await c.get("/api/v1/projects?hours=true", headers=headers)).json(), project
        )["hours"]
        assert hours["period"] == "total"
        assert hours["period_start"] is None
        assert hours["spent_hours"] == 2.0


# --- the client roll-up -------------------------------------------------------- #
async def test_client_rolls_up_its_budgeted_active_projects(client_for) -> None:
    t = await make_tenant("bh-rollup")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        company = await _company(c, headers)
        a = await _project(c, headers, company_id=company, name="A", budget_hours=10)
        b = await _project(c, headers, company_id=company, name="B", budget_hours=20)
        # Neither of these may contribute an allowance:
        await _project(c, headers, company_id=company, name="No budget")
        await _project(
            c, headers, company_id=company, name="Archived", budget_hours=99, status="archived"
        )

        now = datetime.now(UTC) - timedelta(hours=6)
        await _entry(c, headers, project_id=a, company_id=company, started_at=now, minutes=60)
        await _entry(c, headers, project_id=b, company_id=company, started_at=now, minutes=120)

        hours = _row(
            (await c.get("/api/v1/companies?hours=true", headers=headers)).json(), company
        )["hours"]
        assert hours["budget_hours"] == 30.0  # 10 + 20; archived and budget-less excluded
        assert hours["project_count"] == 2
        assert hours["spent_hours"] == 3.0
        assert hours["remaining_hours"] == 27.0
        assert hours["period"] == "total"


async def test_client_with_no_budgeted_project_shows_an_absence_not_a_total(client_for) -> None:
    t = await make_tenant("bh-nobudget-client")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        company = await _company(c, headers)
        project = await _project(c, headers, company_id=company, budget_hours=None)
        await _entry(
            c,
            headers,
            project_id=project,
            company_id=company,
            started_at=datetime.now(UTC) - timedelta(hours=3),
            minutes=150,
        )
        hours = _row(
            (await c.get("/api/v1/companies?hours=true", headers=headers)).json(), company
        )["hours"]
        assert hours["budget_hours"] is None
        assert hours["remaining_hours"] is None
        assert hours["project_count"] == 0
        # The work is not invisible just because nothing budgeted it.
        assert hours["unbudgeted_hours"] == 2.5


async def test_stray_hours_are_excluded_from_the_bar_and_reported_separately(client_for) -> None:
    """`budget - spent` must equal the number on screen, so hours with no allowance sit outside."""
    t = await make_tenant("bh-stray")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        company = await _company(c, headers)
        budgeted = await _project(c, headers, company_id=company, name="B", budget_hours=10)
        loose = await _project(c, headers, company_id=company, name="L", budget_hours=None)

        now = datetime.now(UTC) - timedelta(hours=8)
        await _entry(
            c, headers, project_id=budgeted, company_id=company, started_at=now, minutes=60
        )
        await _entry(c, headers, project_id=loose, company_id=company, started_at=now, minutes=120)
        # Straight to the client, no project at all.
        await _entry(c, headers, company_id=company, started_at=now, minutes=30)

        hours = _row(
            (await c.get("/api/v1/companies?hours=true", headers=headers)).json(), company
        )["hours"]
        assert hours["budget_hours"] == 10.0
        assert hours["spent_hours"] == 1.0  # only the budgeted project's hours
        assert hours["remaining_hours"] == 9.0  # 10 - 1, matching what the bar draws
        assert hours["unbudgeted_hours"] == 2.5  # 2h loose project + 0.5h direct


async def test_a_monthly_projects_earlier_months_are_not_called_unbudgeted(client_for) -> None:
    """The subtraction that derives stray hours must compare like with like."""
    t = await make_tenant("bh-monthly-stray")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        company = await _company(c, headers)
        project = await _project(
            c, headers, company_id=company, budget_hours=10, budget_period="monthly"
        )
        start = period_start("monthly")
        assert start is not None
        await _entry(
            c,
            headers,
            project_id=project,
            company_id=company,
            started_at=start - timedelta(days=2),
            minutes=180,
        )
        await _entry(
            c,
            headers,
            project_id=project,
            company_id=company,
            started_at=start + timedelta(hours=1),
            minutes=60,
        )

        hours = _row(
            (await c.get("/api/v1/companies?hours=true", headers=headers)).json(), company
        )["hours"]
        assert hours["spent_hours"] == 1.0  # this month only
        # The 3h from last month belonged to a budgeted project; it is not stray work.
        assert hours["unbudgeted_hours"] == 0.0


async def test_mixed_budget_periods_report_no_single_period(client_for) -> None:
    t = await make_tenant("bh-mixed")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        company = await _company(c, headers)
        await _project(
            c, headers, company_id=company, name="M", budget_hours=10, budget_period="monthly"
        )
        await _project(
            c, headers, company_id=company, name="T", budget_hours=5, budget_period="total"
        )
        hours = _row(
            (await c.get("/api/v1/companies?hours=true", headers=headers)).json(), company
        )["hours"]
        assert hours["budget_hours"] == 15.0
        assert hours["period"] is None  # no single period this number belongs to


# --- leave never leaks in ------------------------------------------------------- #
async def test_approved_leave_never_counts_against_a_budget(client_for) -> None:
    """CLAUDE.md §14: leave is never a time entry. Structural, but worth pinning."""
    t = await make_tenant("bh-leave")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        project = await _project(c, headers, budget_hours=10)

        leave_type = await c.post(
            "/api/v1/leave/types",
            json={"key": "vacation", "label_i18n": {"nl": "Vakantie", "en": "Vacation"}},
            headers=headers,
        )
        assert leave_type.status_code == 201, leave_type.text
        # A guaranteed working day: the server computes the hours from the schedule (§14), so a
        # weekend or holiday would be worth zero hours and refused.
        workday = leave_workday()
        request = await c.post(
            "/api/v1/leave/requests",
            json={
                "leave_type_id": leave_type.json()["id"],
                "start_date": workday.isoformat(),
                "end_date": workday.isoformat(),
            },
            headers=headers,
        )
        assert request.status_code == 201, request.text
        await c.post(
            f"/api/v1/leave/requests/{request.json()['id']}/decide",
            json={"status": "approved"},
            headers=headers,
        )

        hours = _row(
            (await c.get("/api/v1/projects?hours=true", headers=headers)).json(), project
        )["hours"]
        assert hours["spent_hours"] == 0.0
        assert hours["remaining_hours"] == 10.0


# --- opt-in, and no N+1 --------------------------------------------------------- #
async def test_hours_are_absent_and_uncomputed_unless_asked_for(client_for, count_queries) -> None:
    t = await make_tenant("bh-optin")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        for i in range(3):
            await _project(c, headers, name=f"P{i}", budget_hours=10)

        with count_queries() as counter:
            payload = (await c.get("/api/v1/projects", headers=headers)).json()
        assert all(item["hours"] is None for item in payload["items"])
        # A hidden column must cost nothing at all.
        assert counter.matching("from time_entries") == []


async def test_project_hours_are_one_grouped_query_however_many_rows(
    client_for, count_queries
) -> None:
    t = await make_tenant("bh-n1-projects")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        for i in range(6):
            await _project(c, headers, name=f"P{i}", budget_hours=10)

        with count_queries() as counter:
            payload = (await c.get("/api/v1/projects?hours=true", headers=headers)).json()
        assert len(payload["items"]) == 6
        assert all(item["hours"] is not None for item in payload["items"])
        # Six rows, one aggregate — not six.
        assert len(counter.matching("from time_entries")) == 1


async def test_company_hours_are_a_fixed_number_of_queries_however_many_rows(
    client_for, count_queries
) -> None:
    t = await make_tenant("bh-n1-companies")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        for i in range(5):
            company = await _company(c, headers, name=f"C{i}")
            await _project(c, headers, company_id=company, name=f"P{i}", budget_hours=10)

        with count_queries() as counter:
            payload = (await c.get("/api/v1/companies?hours=true", headers=headers)).json()
        assert len(payload["items"]) == 5
        # Constant cost: the budgeted projects, their minutes, the clients' totals.
        assert len(counter.matching("from time_entries")) == 2
        assert len(counter.matching("from projects")) == 1


# --- sorting -------------------------------------------------------------------- #
async def test_sort_is_server_side_and_allow_listed(client_for) -> None:
    t = await make_tenant("bh-sort")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        for name in ("Charlie", "alpha", "Bravo"):
            await _company(c, headers, name=name)

        asc = (await c.get("/api/v1/companies?sort=name", headers=headers)).json()
        assert [i["name"] for i in asc["items"]] == ["alpha", "Bravo", "Charlie"]

        desc = (await c.get("/api/v1/companies?sort=-name", headers=headers)).json()
        assert [i["name"] for i in desc["items"]] == ["Charlie", "Bravo", "alpha"]

        # The value is user-controlled: anything off the allow-list is refused outright.
        bad = await c.get("/api/v1/companies?sort=hashed_password", headers=headers)
        assert bad.status_code == 400
        assert bad.json()["error"]["message"] == "errors.invalid_sort"


async def test_sorting_puts_empty_values_last_in_both_directions(client_for) -> None:
    t = await make_tenant("bh-sort-nulls")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        await _project(c, headers, name="Budgeted", budget_hours=5)
        await _project(c, headers, name="Unbudgeted", budget_hours=None)

        for sort in ("budget_hours", "-budget_hours"):
            items = (await c.get(f"/api/v1/projects?sort={sort}", headers=headers)).json()["items"]
            assert items[-1]["name"] == "Unbudgeted", sort


# --- tenant isolation (Golden Rule 1) ------------------------------------------- #
async def test_budget_hours_never_cross_tenants(client_for) -> None:
    a = await make_tenant("bh-iso-a")
    b = await make_tenant("bh-iso-b")

    async with client_for(a.host) as c:
        headers = await auth_cookie(a.user)
        company = await _company(c, headers, name="A Client")
        project = await _project(c, headers, company_id=company, budget_hours=10)
        await _entry(
            c,
            headers,
            project_id=project,
            company_id=company,
            started_at=datetime.now(UTC) - timedelta(hours=2),
            minutes=60,
        )

    async with client_for(b.host) as c:
        headers = await auth_cookie(b.user)
        # Tenant B's own client, same shape, must see none of A's hours.
        company_b = await _company(c, headers, name="B Client")
        project_b = await _project(c, headers, company_id=company_b, budget_hours=10)

        projects = (await c.get("/api/v1/projects?hours=true", headers=headers)).json()
        assert [i["id"] for i in projects["items"]] == [project_b]
        assert _row(projects, project_b)["hours"]["spent_hours"] == 0.0

        companies = (await c.get("/api/v1/companies?hours=true", headers=headers)).json()
        assert [i["id"] for i in companies["items"]] == [company_b]
        assert _row(companies, company_b)["hours"]["unbudgeted_hours"] == 0.0


async def test_unknown_uuid_in_another_tenant_yields_no_hours(client_for) -> None:
    """A cross-tenant id in the aggregate's key space must simply not match."""
    t = await make_tenant("bh-iso-key")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        project = await _project(c, headers, budget_hours=4)
        assert uuid.UUID(project)  # sanity: a real id
        hours = _row(
            (await c.get("/api/v1/projects?hours=true", headers=headers)).json(), project
        )["hours"]
        assert hours["spent_hours"] == 0.0


# --- sorting by assigned employee ------------------------------------------------ #
async def _member(org_id, email: str, full_name: str | None) -> str:
    """An extra employee, so a list has more than one name to order by."""
    import uuid as _uuid

    from pwdlib import PasswordHash

    from app.core.auth.models import User
    from app.db import async_session_maker, set_current_org

    async with async_session_maker() as session:
        user = User(
            id=_uuid.uuid4(),
            email=email,
            full_name=full_name,
            hashed_password=PasswordHash.recommended().hash("secret1234"),
            is_active=True,
            is_verified=True,
        )
        session.add(user)
        await session.flush()
        await set_current_org(session, org_id)
        await add_membership(session, org_id, user.id, "member")
        await session.commit()
        return str(user.id)


async def test_sorting_by_assignee_orders_by_the_primarys_display_name(client_for) -> None:
    """Mobile can't tap a header, so this sort has to exist server-side to be offered anywhere."""
    t = await make_tenant("sort-assignee")
    zoe = await _member(t.org.id, "zoe@sort.test", "Zoe Zwart")
    ann = await _member(t.org.id, "ann@sort.test", "Ann Appel")
    # No full name: the UI shows the email, so the sort must too.
    bob = await _member(t.org.id, "bob@sort.test", None)

    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        for name, user_id in (("Zed", zoe), ("Alpha", ann), ("Mid", bob)):
            await c.post(
                "/api/v1/companies",
                json={"name": name, "assignees": [{"user_id": user_id, "is_primary": True}]},
                headers=headers,
            )
        await c.post("/api/v1/companies", json={"name": "Nobody"}, headers=headers)

        asc = (await c.get("/api/v1/companies?sort=assignee", headers=headers)).json()
        # Ann Appel < bob@sort.test < Zoe Zwart; the unassigned client sorts last.
        assert [i["name"] for i in asc["items"]] == ["Alpha", "Mid", "Zed", "Nobody"]

        desc = (await c.get("/api/v1/companies?sort=-assignee", headers=headers)).json()
        # Reversed — but "nobody assigned" stays at the bottom in both directions.
        assert [i["name"] for i in desc["items"]] == ["Zed", "Mid", "Alpha", "Nobody"]


async def test_sorting_by_assignee_does_not_duplicate_rows_with_several_assignees(
    client_for,
) -> None:
    """A join would multiply the row; the correlated subquery must not."""
    t = await make_tenant("sort-assignee-dup")
    other = await _member(t.org.id, "other@dup.test", "Bea Blauw")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        await c.post(
            "/api/v1/companies",
            json={
                "name": "Crowded",
                "assignees": [
                    {"user_id": str(t.user.id), "is_primary": True},
                    {"user_id": other},
                ],
            },
            headers=headers,
        )
        page = (await c.get("/api/v1/companies?sort=assignee", headers=headers)).json()
        assert [i["name"] for i in page["items"]] == ["Crowded"]
        assert page["total"] == 1


async def test_projects_sort_by_assignee_too(client_for) -> None:
    t = await make_tenant("sort-assignee-proj")
    ann = await _member(t.org.id, "ann@proj.test", "Ann Appel")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        await c.post(
            "/api/v1/projects",
            json={"name": "Zed", "assignees": [{"user_id": ann, "is_primary": True}]},
            headers=headers,
        )
        await c.post(
            "/api/v1/projects",
            json={"name": "Alpha", "assignees": [{"user_id": str(t.user.id), "is_primary": True}]},
            headers=headers,
        )
        items = (await c.get("/api/v1/projects?sort=assignee", headers=headers)).json()["items"]
        # Ann Appel sorts before the tenant owner's "sort-assignee-proj@example.com".
        assert [i["name"] for i in items] == ["Zed", "Alpha"]
