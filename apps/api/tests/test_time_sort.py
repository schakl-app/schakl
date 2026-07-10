"""Server-side sorting for the Uren report, and the entries endpoint the project panel reads.

Two things are being pinned here (#42, #43):

* The report sorts by the **names it prints** — client, project, task, employee — not by the
  foreign keys behind them. Those name lookups are correlated subqueries onto tables this module
  deliberately does not import (CLAUDE.md §6), so "does it order right" and "does it duplicate a
  row" are both worth asserting.
* ``/time/entries?all_users=true`` is free when the query names an entity and manager-only when it
  doesn't. That asymmetry is the whole reason the Uren panel can exist on a project page for
  someone without the manager role, and it is exactly the kind of rule that rots silently.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from pwdlib import PasswordHash

from app.core.auth.models import User
from app.db import async_session_maker, set_current_org
from tests.conftest import add_membership, auth_cookie, make_tenant

_ph = PasswordHash.recommended()
_START = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


async def _member(org_id, email: str, full_name: str | None, role: str = "member") -> User:
    async with async_session_maker() as session:
        user = User(
            id=uuid.uuid4(),
            email=email,
            full_name=full_name,
            hashed_password=_ph.hash("secret1234"),
            is_active=True,
            is_verified=True,
        )
        session.add(user)
        await session.flush()
        await set_current_org(session, org_id)
        await add_membership(session, org_id, user.id, role)
        await session.commit()
        return User(id=user.id, email=user.email, hashed_password="", is_active=True)


async def _company(client, headers, name: str) -> str:
    res = await client.post("/api/v1/companies", json={"name": name}, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _project(client, headers, name: str, company_id: str | None = None, **fields) -> str:
    body = {"name": name, "company_id": company_id, **fields}
    res = await client.post("/api/v1/projects", json=body, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _entry(
    client,
    headers,
    *,
    minutes: int = 60,
    offset_days: int = 0,
    started_at: datetime | None = None,
    **fields,
) -> str:
    started = started_at or (_START + timedelta(days=offset_days))
    body = {"started_at": started.isoformat(), "minutes": minutes, **fields}
    res = await client.post("/api/v1/time/entries", json=body, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _descriptions(client, headers, path: str) -> list[str]:
    page = (await client.get(path, headers=headers)).json()
    return [item["description"] for item in page["items"]]


# --- report sorting ----------------------------------------------------------------------- #
async def test_report_sorts_by_client_and_project_name_not_by_id(client_for) -> None:
    t = await make_tenant("tm-names")
    async with client_for(t.host) as c:
        h = await auth_cookie(t.user)
        zeta = await _company(c, h, "Zeta BV")
        alpha = await _company(c, h, "Alpha BV")
        z_project = await _project(c, h, "Zebra", zeta)
        a_project = await _project(c, h, "Aardvark", alpha)

        await _entry(c, h, description="on zeta", company_id=zeta, project_id=z_project)
        await _entry(c, h, description="on alpha", company_id=alpha, project_id=a_project)

        assert await _descriptions(c, h, "/api/v1/time/report?sort=company") == [
            "on alpha",
            "on zeta",
        ]
        assert await _descriptions(c, h, "/api/v1/time/report?sort=-project") == [
            "on zeta",
            "on alpha",
        ]


async def test_report_sorting_by_a_name_files_entries_without_one_last(client_for) -> None:
    t = await make_tenant("tm-nulls")
    async with client_for(t.host) as c:
        h = await auth_cookie(t.user)
        company = await _company(c, h, "Alpha BV")
        await _entry(c, h, description="general work")  # no client at all
        await _entry(c, h, description="client work", company_id=company)

        for direction in ("company", "-company"):
            rows = await _descriptions(c, h, f"/api/v1/time/report?sort={direction}")
            assert rows[-1] == "general work", direction


async def test_report_sorting_by_a_name_never_duplicates_an_entry(client_for) -> None:
    """The name lookups are correlated subqueries; a join would multiply the row."""
    t = await make_tenant("tm-dup")
    async with client_for(t.host) as c:
        h = await auth_cookie(t.user)
        company = await _company(c, h, "Alpha BV")
        project = await _project(c, h, "Only", company)
        await _entry(c, h, description="once", company_id=company, project_id=project)

        page = (await c.get("/api/v1/time/report?sort=project", headers=h)).json()
        assert len(page["items"]) == 1
        assert page["total"] == 1
        assert page["totals"]["minutes"] == 60


async def test_report_sorts_by_employee_display_name(client_for) -> None:
    t = await make_tenant("tm-emp", email="owner@tm.test")
    ann = await _member(t.org.id, "ann@tm.test", "Ann Appel")
    zoe = await _member(t.org.id, "zoe@tm.test", "Zoe Zwart")
    async with client_for(t.host) as c:
        await _entry(c, await auth_cookie(zoe), description="zoe hours")
        await _entry(c, await auth_cookie(ann), description="ann hours")

        owner = await auth_cookie(t.user)
        assert await _descriptions(c, owner, "/api/v1/time/report?sort=employee") == [
            "ann hours",
            "zoe hours",
        ]


async def test_report_sorts_by_minutes(client_for) -> None:
    t = await make_tenant("tm-minutes")
    async with client_for(t.host) as c:
        h = await auth_cookie(t.user)
        await _entry(c, h, description="long", minutes=180)
        await _entry(c, h, description="short", minutes=30)
        assert await _descriptions(c, h, "/api/v1/time/report?sort=minutes") == ["short", "long"]
        assert await _descriptions(c, h, "/api/v1/time/report?sort=-minutes") == ["long", "short"]


async def test_report_totals_cover_the_whole_filtered_set_not_the_page(client_for) -> None:
    """The footer prints these; summing the page would print the total *of the page*."""
    t = await make_tenant("tm-totals")
    async with client_for(t.host) as c:
        h = await auth_cookie(t.user)
        await _entry(c, h, description="a", minutes=60)
        await _entry(c, h, description="b", minutes=90, offset_days=1)
        await _entry(c, h, description="c", minutes=30, offset_days=2, billable=False)

        page = (await c.get("/api/v1/time/report?limit=1&sort=minutes", headers=h)).json()
        assert len(page["items"]) == 1
        assert page["total"] == 3
        assert page["totals"]["minutes"] == 180
        assert page["totals"]["billable_minutes"] == 150


async def test_unknown_report_sort_key_is_refused(client_for) -> None:
    t = await make_tenant("tm-bad")
    async with client_for(t.host) as c:
        h = await auth_cookie(t.user)
        bad = await c.get("/api/v1/time/report?sort=hashed_password", headers=h)
        assert bad.status_code == 400
        assert bad.json()["error"]["message"] == "errors.invalid_sort"


async def test_report_sort_never_crosses_tenants(client_for) -> None:
    a = await make_tenant("tm-iso-a")
    b = await make_tenant("tm-iso-b")
    async with client_for(a.host) as c:
        h = await auth_cookie(a.user)
        await _entry(c, h, description="tenant a", company_id=await _company(c, h, "A BV"))
    async with client_for(b.host) as c:
        page = (
            await c.get("/api/v1/time/report?sort=company", headers=await auth_cookie(b.user))
        ).json()
        assert page["items"] == []
        assert page["totals"]["minutes"] == 0


# --- the entries endpoint the Uren panel reads (#43) ---------------------------------------- #
async def test_a_member_sees_the_whole_teams_hours_on_a_project(client_for) -> None:
    """The panel's reason to exist: the budget bar is team-wide, so the rows behind it are too."""
    t = await make_tenant("tm-panel", email="owner@panel.test")
    mate = await _member(t.org.id, "mate@panel.test", "Team Mate")
    async with client_for(t.host) as c:
        owner = await auth_cookie(t.user)
        project = await _project(c, owner, "Shared")
        await _entry(c, owner, description="owner hours", project_id=project)
        await _entry(c, await auth_cookie(mate), description="mate hours", project_id=project)

        rows = await _descriptions(
            c, await auth_cookie(mate), f"/api/v1/time/entries?project_id={project}&all_users=true"
        )
        assert sorted(rows) == ["mate hours", "owner hours"]


async def test_all_users_without_an_entity_filter_is_manager_only(client_for) -> None:
    """Otherwise `all_users` would be a back door around the manager-gated report."""
    t = await make_tenant("tm-gate", email="owner@gate.test")
    mate = await _member(t.org.id, "mate@gate.test", "Team Mate")
    async with client_for(t.host) as c:
        owner = await auth_cookie(t.user)
        await _entry(c, owner, description="owner hours")

        mate_headers = await auth_cookie(mate)
        refused = await c.get("/api/v1/time/entries?all_users=true", headers=mate_headers)
        assert refused.status_code == 403
        allowed = await c.get("/api/v1/time/entries?all_users=true", headers=owner)
        assert allowed.status_code == 200


async def test_without_all_users_a_member_still_only_sees_their_own_entries(client_for) -> None:
    t = await make_tenant("tm-own", email="owner@own.test")
    mate = await _member(t.org.id, "mate@own.test", "Team Mate")
    async with client_for(t.host) as c:
        owner = await auth_cookie(t.user)
        project = await _project(c, owner, "Shared")
        await _entry(c, owner, description="owner hours", project_id=project)
        await _entry(c, await auth_cookie(mate), description="mate hours", project_id=project)

        rows = await _descriptions(
            c, await auth_cookie(mate), f"/api/v1/time/entries?project_id={project}"
        )
        assert rows == ["mate hours"]


async def test_an_external_client_user_never_gets_the_teams_entries(client_for) -> None:
    t = await make_tenant("tm-client", email="owner@cl.test")
    outsider = await _member(t.org.id, "guest@cl.test", "Guest", role="client")
    async with client_for(t.host) as c:
        owner = await auth_cookie(t.user)
        project = await _project(c, owner, "Shared")
        await _entry(c, owner, description="owner hours", project_id=project)

        refused = await c.get(
            f"/api/v1/time/entries?project_id={project}&all_users=true",
            headers=await auth_cookie(outsider),
        )
        assert refused.status_code == 403


async def test_the_panel_excludes_running_timers(client_for) -> None:
    """A running timer is not an entry: it has logged nothing and burns no budget."""
    t = await make_tenant("tm-running")
    async with client_for(t.host) as c:
        h = await auth_cookie(t.user)
        project = await _project(c, h, "Shared")
        await _entry(c, h, description="finished", project_id=project)
        started = await c.post(
            "/api/v1/time/timer/start", json={"project_id": project}, headers=h
        )
        assert started.status_code == 201, started.text

        both = (await c.get(f"/api/v1/time/entries?project_id={project}", headers=h)).json()
        assert both["total"] == 2
        logged = (
            await c.get(f"/api/v1/time/entries?project_id={project}&running=false", headers=h)
        ).json()
        assert [i["description"] for i in logged["items"]] == ["finished"]
        assert logged["total"] == 1


async def test_the_panel_counts_the_whole_period_even_when_it_shows_a_page(client_for) -> None:
    """The panel says "8 of 23" from `total`; a truncation it cannot see it cannot announce."""
    t = await make_tenant("tm-truncate")
    async with client_for(t.host) as c:
        h = await auth_cookie(t.user)
        project = await _project(c, h, "Busy")
        for day in range(5):
            await _entry(c, h, description=f"day {day}", project_id=project, offset_days=day)

        page = (
            await c.get(f"/api/v1/time/entries?project_id={project}&limit=2&sort=-date", headers=h)
        ).json()
        assert len(page["items"]) == 2
        assert page["total"] == 5
        assert [i["description"] for i in page["items"]] == ["day 4", "day 3"]


async def test_the_panel_scopes_to_the_budget_period(client_for) -> None:
    t = await make_tenant("tm-period")
    async with client_for(t.host) as c:
        h = await auth_cookie(t.user)
        project = await _project(c, h, "Monthly", budget_period="monthly", budget_hours=10)
        await _entry(c, h, description="in period", project_id=project, offset_days=3)
        await _entry(c, h, description="last month", project_id=project, offset_days=-40)

        period_start = _START.date().replace(day=1).isoformat()
        rows = await _descriptions(
            c, h, f"/api/v1/time/entries?project_id={project}&date_from={period_start}"
        )
        assert rows == ["in period"]


async def test_entries_sort_and_filters_never_cross_tenants(client_for) -> None:
    a = await make_tenant("tm-e-iso-a")
    b = await make_tenant("tm-e-iso-b")
    async with client_for(a.host) as c:
        h = await auth_cookie(a.user)
        project_a = await _project(c, h, "A project")
        await _entry(c, h, description="tenant a", project_id=project_a)
    async with client_for(b.host) as c:
        h = await auth_cookie(b.user)
        # Even naming tenant A's project id — the org filter, not the id, decides.
        page = (
            await c.get(
                f"/api/v1/time/entries?project_id={project_a}&all_users=true&sort=employee",
                headers=h,
            )
        ).json()
        assert page["items"] == []
        assert page["total"] == 0


# --- the period start the panel and the budget bar must share (#43) ------------------------- #
async def test_project_detail_can_carry_the_budget_burn_and_its_period_start(client_for) -> None:
    t = await make_tenant("tm-hours")
    async with client_for(t.host) as c:
        h = await auth_cookie(t.user)
        project = await _project(c, h, "Monthly", budget_period="monthly", budget_hours=10)
        # A monthly budget burns down from *this* month's start, so the entry has to be now.
        now = datetime.now(UTC)
        await _entry(c, h, description="worked", project_id=project, minutes=120, started_at=now)

        plain = (await c.get(f"/api/v1/projects/{project}", headers=h)).json()
        assert plain["hours"] is None  # opt-in: a detail page that never asks never pays

        with_hours = (await c.get(f"/api/v1/projects/{project}?hours=true", headers=h)).json()
        assert with_hours["hours"]["period"] == "monthly"
        assert with_hours["hours"]["period_start"].endswith("-01")
        assert with_hours["hours"]["budget_hours"] == 10.0
        assert with_hours["hours"]["remaining_hours"] == 8.0
