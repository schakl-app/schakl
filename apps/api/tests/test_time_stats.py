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


async def test_revenue_stats(client_for) -> None:
    t = await make_tenant("stats-rev")
    headers = await auth_cookie(t.user)
    now = datetime.now(UTC)

    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Grote Klant"}, headers=headers)
        ).json()
        project = (
            await c.post(
                "/api/v1/projects",
                json={
                    "name": "Retainer",
                    "company_id": company["id"],
                    "hourly_rate": 100,
                    "currency": "EUR",
                },
                headers=headers,
            )
        ).json()
        # 90 billable minutes at €100/h → €150; plus 60 non-billable minutes → no revenue.
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

        stats = (
            await c.get("/api/v1/time/stats/revenue", params={"year": now.year}, headers=headers)
        ).json()
        assert stats["months_current"][now.month - 1] == 150.0
        assert stats["total_current"] == 150.0
        assert stats["total_previous"] == 0.0
        assert stats["top_clients"][0]["company_id"] == company["id"]
        assert stats["top_clients"][0]["revenue"] == 150.0
        assert stats["other_revenue"] == 0.0


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
