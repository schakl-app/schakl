"""Productivity + revenue stats endpoints (manager-gated) and the brand-name toggle."""

from __future__ import annotations

from datetime import UTC, datetime

from tests.conftest import auth_cookie, make_tenant
from tests.test_task_subresources import add_member


async def test_productivity_stats(client_for) -> None:
    t = await make_tenant("stats-prod")
    owner_headers = await auth_cookie(t.user)
    member = await add_member(t)
    member_headers = await auth_cookie(member)
    today = datetime.now(UTC).date().isoformat()

    async with client_for(t.host) as c:
        now = datetime.now(UTC).isoformat()
        await c.post(
            "/api/v1/time/entries",
            json={"started_at": now, "minutes": 60},
            headers=owner_headers,
        )
        await c.post(
            "/api/v1/time/entries",
            json={"started_at": now, "minutes": 30, "billable": False},
            headers=member_headers,
        )

        params = {"date_from": today, "date_to": today}
        assert (
            await c.get("/api/v1/time/stats/productivity", params=params, headers=member_headers)
        ).status_code == 403

        stats = (
            await c.get("/api/v1/time/stats/productivity", params=params, headers=owner_headers)
        ).json()
        rows = {r["user_id"]: r for r in stats["rows"]}
        assert rows[str(t.user.id)]["minutes"] == 60
        assert rows[str(t.user.id)]["billable_minutes"] == 60
        assert rows[str(member.id)]["minutes"] == 30
        assert rows[str(member.id)]["billable_minutes"] == 0
        assert rows[str(member.id)]["active_days"] == 1
        # Nobody has a rate yet, so the hours are worth nothing rather than something invented.
        assert rows[str(t.user.id)]["revenue"] == 0.0

        # A rate prices the billable hour; the member's non-billable half hour stays at zero.
        await c.put(
            f"/api/v1/leave/rate/{t.user.id}",
            json={"hourly_rate": "120.00"},
            headers=owner_headers,
        )
        stats = (
            await c.get("/api/v1/time/stats/productivity", params=params, headers=owner_headers)
        ).json()
        rows = {r["user_id"]: r for r in stats["rows"]}
        assert rows[str(t.user.id)]["revenue"] == 120.0
        assert rows[str(member.id)]["revenue"] == 0.0


async def test_revenue_stats(client_for) -> None:
    """#226: revenue prices billable minutes at the logger's effective rate — a project is
    no longer part of the price, and a logger with no rate anywhere contributes nothing."""
    t = await make_tenant("stats-rev")
    headers = await auth_cookie(t.user)
    member = await add_member(t)
    member_headers = await auth_cookie(member)
    now = datetime.now(UTC)

    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Grote Klant"}, headers=headers)
        ).json()
        project = (
            await c.post(
                "/api/v1/projects",
                json={"name": "Retainer", "company_id": company["id"], "currency": "EUR"},
                headers=headers,
            )
        ).json()
        await c.put(
            f"/api/v1/leave/rate/{t.user.id}",
            json={"hourly_rate": "100.00"},
            headers=headers,
        )
        # 90 billable minutes at the owner's €100/h → €150; 60 non-billable minutes → nothing.
        await c.post(
            "/api/v1/time/entries",
            json={
                "started_at": now.isoformat(),
                "minutes": 90,
                "company_id": company["id"],
                "project_id": project["id"],
            },
            headers=headers,
        )
        await c.post(
            "/api/v1/time/entries",
            json={
                "started_at": now.isoformat(),
                "minutes": 60,
                "billable": False,
                "company_id": company["id"],
                "project_id": project["id"],
            },
            headers=headers,
        )
        # 30 billable minutes with no project at all → €50: the logger prices it, not the
        # project. And the member has no rate anywhere, so their hour is excluded.
        await c.post(
            "/api/v1/time/entries",
            json={
                "started_at": now.isoformat(),
                "minutes": 30,
                "company_id": company["id"],
            },
            headers=headers,
        )
        await c.post(
            "/api/v1/time/entries",
            json={
                "started_at": now.isoformat(),
                "minutes": 60,
                "company_id": company["id"],
            },
            headers=member_headers,
        )

        stats = (
            await c.get("/api/v1/time/stats/revenue", params={"year": now.year}, headers=headers)
        ).json()
        assert stats["months_current"][now.month - 1] == 200.0
        assert stats["total_current"] == 200.0
        assert stats["total_previous"] == 0.0
        assert stats["top_clients"][0]["company_id"] == company["id"]
        assert stats["top_clients"][0]["revenue"] == 200.0
        assert stats["other_revenue"] == 0.0


async def test_project_stats_are_one_grouped_query(client_for, count_queries) -> None:
    """``/stats/projects``: per project, the minutes split and the billable worth — what the
    projects report joins onto each budget instead of asking ``/cost`` per row."""
    t = await make_tenant("stats-projects")
    headers = await auth_cookie(t.user)
    member = await add_member(t)
    member_headers = await auth_cookie(member)
    now = datetime.now(UTC)

    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Klant"}, headers=headers)
        ).json()
        projects = []
        for name in ("Site", "Campagne", "Hosting"):
            projects.append(
                (
                    await c.post(
                        "/api/v1/projects",
                        json={"name": name, "company_id": company["id"], "currency": "EUR"},
                        headers=headers,
                    )
                ).json()
            )
        await c.put(
            f"/api/v1/leave/rate/{t.user.id}",
            json={"hourly_rate": "100.00"},
            headers=headers,
        )
        # Site: 90 billable minutes at € 100 (€ 150) + 60 non-billable; Campagne: 30 billable by
        # the unrated member (reported, not priced); Hosting: nothing. Plus an entry on the
        # client with no project, which no budget could read and so is left out.
        for body, who in (
            ({"minutes": 90, "project_id": projects[0]["id"]}, headers),
            ({"minutes": 60, "project_id": projects[0]["id"], "billable": False}, headers),
            ({"minutes": 30, "project_id": projects[1]["id"]}, member_headers),
            ({"minutes": 45}, headers),
        ):
            res = await c.post(
                "/api/v1/time/entries",
                json={"started_at": now.isoformat(), "company_id": company["id"], **body},
                headers=who,
            )
            assert res.status_code == 201, res.text

        assert (
            await c.get("/api/v1/time/stats/projects", headers=member_headers)
        ).status_code == 403

        with count_queries() as counter:
            res = await c.get("/api/v1/time/stats/projects", headers=headers)
        assert res.status_code == 200, res.text
        rows = {r["project_id"]: r for r in res.json()["rows"]}
        assert set(rows) == {projects[0]["id"], projects[1]["id"]}
        site = rows[projects[0]["id"]]
        assert site["minutes"] == 150
        assert site["billable_minutes"] == 90
        assert site["billable_amount"] == 150.0
        assert site["unrated_minutes"] == 0
        campagne = rows[projects[1]["id"]]
        assert campagne["minutes"] == 30
        assert campagne["billable_amount"] == 0.0
        assert campagne["unrated_minutes"] == 30
        # One grouped statement plus the request's context reads — never one per project.
        assert len(counter) <= 8, "\n".join(counter.statements)

        # A window that excludes today reads as empty, not as an error.
        empty = (
            await c.get(
                "/api/v1/time/stats/projects",
                params={"date_from": "2000-01-01", "date_to": "2000-01-31"},
                headers=headers,
            )
        ).json()
        assert empty["rows"] == []


async def test_team_summary_is_one_bounded_dashboard_payload(client_for) -> None:
    t = await make_tenant("stats-team-summary")
    headers = await auth_cookie(t.user)
    now = datetime.now(UTC)

    async with client_for(t.host) as c:
        await c.put(
            f"/api/v1/leave/rate/{t.user.id}",
            json={"hourly_rate": "80.00"},
            headers=headers,
        )
        await c.post(
            "/api/v1/time/entries",
            json={"started_at": now.isoformat(), "minutes": 90},
            headers=headers,
        )
        await c.post(
            "/api/v1/time/entries",
            json={"started_at": now.isoformat(), "minutes": 30, "billable": False},
            headers=headers,
        )

        params = {
            "date_from": now.date().replace(day=1).isoformat(),
            "date_to": now.date().isoformat(),
        }
        summary = (
            await c.get("/api/v1/time/stats/team-summary", params=params, headers=headers)
        ).json()
        assert summary["minutes"] == 120
        assert summary["billable_minutes"] == 90
        assert summary["open_minutes"] == 120
        assert summary["revenue"] == 120.0


async def test_show_brand_name_toggle(client_for) -> None:
    t = await make_tenant("brandname")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        assert (await c.get("/api/v1/meta/tenant")).json()["show_brand_name"] is True
        updated = await c.patch(
            "/api/v1/meta/tenant", json={"show_brand_name": False}, headers=headers
        )
        assert updated.json()["show_brand_name"] is False
        assert (await c.get("/api/v1/meta/tenant")).json()["show_brand_name"] is False


async def test_project_cost_from_employee_rates(client_for) -> None:
    """#111: cost = Σ minutes × the employee's *effective* rate (#113) — personal rate first,
    org default next, and unrated time reported instead of silently priced at €0."""
    t = await make_tenant("stats-cost")
    owner_headers = await auth_cookie(t.user)
    member = await add_member(t)
    member_headers = await auth_cookie(member)
    now = datetime.now(UTC)

    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Bouwer"}, headers=owner_headers)
        ).json()
        project = (
            await c.post(
                "/api/v1/projects",
                json={"name": "Bouw", "company_id": company["id"], "currency": "EUR"},
                headers=owner_headers,
            )
        ).json()
        # Owner at a personal €120/h: 60 billable + 30 non-billable minutes; the member
        # logs 30 billable minutes with no rate anywhere.
        await c.put(
            f"/api/v1/leave/rate/{t.user.id}",
            json={"hourly_rate": "120.00"},
            headers=owner_headers,
        )
        await c.post(
            "/api/v1/time/entries",
            json={
                "started_at": now.isoformat(),
                "minutes": 30,
                "billable": False,
                "project_id": project["id"],
            },
            headers=owner_headers,
        )
        for headers, minutes in ((owner_headers, 60), (member_headers, 30)):
            await c.post(
                "/api/v1/time/entries",
                json={
                    "started_at": now.isoformat(),
                    "minutes": minutes,
                    "project_id": project["id"],
                },
                headers=headers,
            )

        # Salary-derived money: a plain member is refused.
        assert (
            await c.get(
                "/api/v1/time/cost",
                params={"project_id": project["id"]},
                headers=member_headers,
            )
        ).status_code == 403

        cost = (
            await c.get(
                "/api/v1/time/cost",
                params={"project_id": project["id"]},
                headers=owner_headers,
            )
        ).json()
        assert cost["cost"] == 180.0  # 90 min × €120/h; the member's 30 min carry no rate
        assert cost["billable_amount"] == 120.0  # only the owner's billable hour (#226)
        assert cost["unrated_minutes"] == 30

        # The org default (#113) picks the member up; unrated drops to zero.
        await c.put(
            "/api/v1/leave/settings",
            json={"default_hourly_rate": "60.00"},
            headers=owner_headers,
        )
        cost = (
            await c.get(
                "/api/v1/time/cost",
                params={"project_id": project["id"]},
                headers=owner_headers,
            )
        ).json()
        assert cost["cost"] == 210.0  # 180 + 30 min × €60/h
        assert cost["billable_amount"] == 150.0  # 120 + the member's billable half hour
        assert cost["unrated_minutes"] == 0
