"""A linked subscription is the source of truth for a project's hours (issue #225).

The decisions the issue demanded be made explicitly, pinned here:

- **Which agreements count**: active subscriptions with ``included_hours`` set, linked via
  ``SubscriptionLink(entity_type="project")``. Draft/paused/cancelled, or hours-less, source
  nothing and lock nothing.
- **Multiple covering agreements sum** their monthly-equivalent hours.
- **Period mismatch**: a subscription-backed budget is forced to ``monthly``; a quarterly or
  yearly ``included_hours`` contributes its monthly equivalent (the ``monthly_equivalent``
  rule the money side already uses for MRR).
- **The stored ``budget_hours`` is a dormant fallback**: kept, ignored while linked, back in
  force when the link is removed. A write that would *change* it while linked is refused;
  echoing the stored value back (clients that PATCH whole objects) is a no-op and passes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.conftest import auth_cookie, make_tenant


async def _company(client, headers, name: str = "Retainer BV") -> str:
    res = await client.post("/api/v1/companies", json={"name": name}, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _project(client, headers, company_id: str, **overrides) -> str:
    payload = {"name": "Onderhoud", "company_id": company_id, **overrides}
    res = await client.post("/api/v1/projects", json=payload, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _subscription(
    client,
    headers,
    company_id: str,
    *,
    project_id: str | None = None,
    name: str = "SLA",
    status: str = "active",
    interval: str = "monthly",
    included_hours: str | None = "10",
) -> str:
    today = datetime.now(UTC).date()
    payload = {
        "company_id": company_id,
        "name": name,
        "status": status,
        "interval": interval,
        "start_date": (today - timedelta(days=40)).isoformat(),
        "next_invoice_date": (today + timedelta(days=10)).isoformat(),
        "amount": "250.00",
        "included_hours": included_hours,
    }
    if project_id is not None:
        payload["links"] = [{"entity_type": "project", "entity_id": project_id}]
    res = await client.post("/api/v1/subscriptions", json=payload, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _get_project(client, headers, project_id: str) -> dict:
    res = await client.get(f"/api/v1/projects/{project_id}?hours=true", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


async def test_linked_subscription_sources_the_projects_hours(client_for) -> None:
    """The derived figure replaces the stored one; the stored one stays visible, dormant."""
    t = await make_tenant("psh-source")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        project = await _project(c, headers, company, budget_hours=40, budget_period="total")
        await _subscription(c, headers, company, project_id=project, included_hours="10")

        await c.post(
            "/api/v1/time/entries",
            json={
                "started_at": datetime.now(UTC).isoformat(),
                "minutes": 90,
                "project_id": project,
            },
            headers=headers,
        )

        body = await _get_project(c, headers, project)
        # The burn calc reads the subscription, not the stored column…
        assert body["hours"]["budget_hours"] == 10.0
        assert body["hours"]["period"] == "monthly"
        assert body["hours"]["spent_hours"] == 1.5
        assert body["hours"]["remaining_hours"] == 8.5
        # …the connection is nameable…
        assert [s["name"] for s in body["budget_sources"]] == ["SLA"]
        assert body["budget_sources"][0]["monthly_hours"] == 10.0
        # …and the stored value survives as the dormant fallback.
        assert body["budget_hours"] == 40.0


async def test_interval_maps_to_a_monthly_equivalent(client_for) -> None:
    """A quarterly bundle of 30 h is 10 h per month — the MRR rule, applied to hours."""
    t = await make_tenant("psh-interval")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        project = await _project(c, headers, company)
        await _subscription(
            c, headers, company, project_id=project, interval="quarterly", included_hours="30"
        )
        body = await _get_project(c, headers, project)
        assert body["hours"]["budget_hours"] == 10.0
        assert body["budget_sources"][0]["included_hours"] == 30.0
        assert body["budget_sources"][0]["monthly_hours"] == 10.0


async def test_multiple_covering_subscriptions_sum(client_for) -> None:
    t = await make_tenant("psh-multi")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        project = await _project(c, headers, company)
        await _subscription(
            c, headers, company, project_id=project, name="Hosting", included_hours="5"
        )
        await _subscription(
            c, headers, company, project_id=project, name="Onderhoud", included_hours="10"
        )
        body = await _get_project(c, headers, project)
        assert body["hours"]["budget_hours"] == 15.0
        assert [s["name"] for s in body["budget_sources"]] == ["Hosting", "Onderhoud"]


async def test_only_active_hours_bearing_subscriptions_source(client_for) -> None:
    """A draft agreement, or one without included hours, neither sources nor locks."""
    t = await make_tenant("psh-inactive")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        project = await _project(c, headers, company, budget_hours=40)
        await _subscription(
            c, headers, company, project_id=project, name="Concept", status="draft"
        )
        await _subscription(
            c, headers, company, project_id=project, name="Zonder uren", included_hours=None
        )

        body = await _get_project(c, headers, project)
        assert body["budget_sources"] == []
        assert body["hours"]["budget_hours"] == 40.0

        # And the project's own hours stay writable.
        res = await c.patch(
            f"/api/v1/projects/{project}", json={"budget_hours": 50}, headers=headers
        )
        assert res.status_code == 200, res.text
        assert res.json()["budget_hours"] == 50.0


async def test_budget_hours_write_is_refused_while_linked(client_for) -> None:
    """The API guards the rule, not just the form — MCP or a script can't create drift."""
    t = await make_tenant("psh-guard")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        project = await _project(c, headers, company, budget_hours=40)
        await _subscription(c, headers, company, project_id=project)

        # Changing the stored value is refused with the dedicated error…
        res = await c.patch(
            f"/api/v1/projects/{project}", json={"budget_hours": 50}, headers=headers
        )
        assert res.status_code == 409, res.text
        assert res.json()["error"]["message"] == "errors.projects_budget_hours_locked"

        # …clearing it counts as a change too…
        res = await c.patch(
            f"/api/v1/projects/{project}", json={"budget_hours": None}, headers=headers
        )
        assert res.status_code == 409

        # …echoing the stored value back is a no-op and passes…
        res = await c.patch(
            f"/api/v1/projects/{project}",
            json={"budget_hours": 40, "name": "Onderhoud 2.0"},
            headers=headers,
        )
        assert res.status_code == 200, res.text
        assert res.json()["name"] == "Onderhoud 2.0"
        assert res.json()["budget_hours"] == 40.0
        # (the write response also names its sources, so a client sees why)
        assert len(res.json()["budget_sources"]) == 1

        # …and a PATCH that never mentions the field is untouched by the guard.
        res = await c.patch(
            f"/api/v1/projects/{project}", json={"description": "vast"}, headers=headers
        )
        assert res.status_code == 200, res.text


async def test_link_and_unlink_flip_the_effective_hours(client_for) -> None:
    """The dormant fallback returns, and the field unlocks, the moment the link goes."""
    t = await make_tenant("psh-flip")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        project = await _project(c, headers, company, budget_hours=40, budget_period="total")

        body = await _get_project(c, headers, project)
        assert body["hours"]["budget_hours"] == 40.0
        assert body["hours"]["period"] == "total"
        assert body["budget_sources"] == []

        sub = await _subscription(c, headers, company, project_id=project, included_hours="10")
        body = await _get_project(c, headers, project)
        assert body["hours"]["budget_hours"] == 10.0
        assert body["hours"]["period"] == "monthly"

        # Unlink: the subscription stops covering the project.
        res = await c.patch(
            f"/api/v1/subscriptions/{sub}", json={"links": []}, headers=headers
        )
        assert res.status_code == 200, res.text

        body = await _get_project(c, headers, project)
        assert body["hours"]["budget_hours"] == 40.0
        assert body["hours"]["period"] == "total"
        assert body["budget_sources"] == []

        res = await c.patch(
            f"/api/v1/projects/{project}", json={"budget_hours": 50}, headers=headers
        )
        assert res.status_code == 200, res.text


async def test_sources_are_one_grouped_query_however_many_rows(
    client_for, count_queries
) -> None:
    t = await make_tenant("psh-n1")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        for i in range(5):
            project = await _project(c, headers, company, name=f"P{i}")
            await _subscription(
                c, headers, company, project_id=project, name=f"S{i}", included_hours="4"
            )

        with count_queries() as counter:
            payload = (await c.get("/api/v1/projects?hours=true", headers=headers)).json()
        assert len(payload["items"]) == 5
        assert all(item["hours"]["budget_hours"] == 4.0 for item in payload["items"])
        # Five rows, one links lookup — not five.
        assert len(counter.matching("from subscription_links")) == 1


async def test_subscription_sources_never_cross_tenants(client_for) -> None:
    """Golden Rule 1: another tenant's identically-shaped link must change nothing here."""
    a = await make_tenant("psh-iso-a")
    b = await make_tenant("psh-iso-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)

    async with client_for(a.host) as ca:
        company_a = await _company(ca, a_headers, name="A BV")
        project_a = await _project(ca, a_headers, company_a, budget_hours=40)
        await _subscription(ca, a_headers, company_a, project_id=project_a)

    async with client_for(b.host) as cb:
        company_b = await _company(cb, b_headers, name="B BV")
        project_b = await _project(cb, b_headers, company_b, budget_hours=40)

        body = await _get_project(cb, b_headers, project_b)
        assert body["budget_sources"] == []
        assert body["hours"]["budget_hours"] == 40.0

        # B's own hours are not locked by A's agreement.
        res = await cb.patch(
            f"/api/v1/projects/{project_b}", json={"budget_hours": 50}, headers=b_headers
        )
        assert res.status_code == 200, res.text
