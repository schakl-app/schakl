"""Work a retainer pays for is not billable a second time (#284).

Two rules meet here and both used to be missing. A project a subscription covers had no idea it
was covered, and ``projects.billable_default`` — documented since day one as "seeds the billable
flag on new time entries" — was read by nothing at all: whatever the client posted became the
answer, and every client posted ``true``.

So the tests pin the seam end to end: linking clears the project's default, the API resolves a
new entry's flag from it, an explicit flag still wins, and neither the tenant's own override nor
a re-saved agreement is quietly undone.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from tests.conftest import auth_cookie, make_tenant


def _iso(d: date) -> str:
    return d.isoformat()


async def _company_and_project(c, headers, *, project: str = "Onderhoud") -> tuple[dict, dict]:
    company = (
        await c.post("/api/v1/companies", json={"name": "Retainer BV"}, headers=headers)
    ).json()
    made = (
        await c.post(
            "/api/v1/projects",
            json={"name": project, "company_id": company["id"]},
            headers=headers,
        )
    ).json()
    assert made["billable_default"] is True  # the platform default, until an agreement says no
    return company, made


async def _subscription(c, headers, company_id: str, *, links: list[dict]) -> dict:
    today = datetime.now(UTC).date()
    res = await c.post(
        "/api/v1/subscriptions",
        json={
            "company_id": company_id,
            "name": "Onderhoudscontract",
            "status": "active",
            "interval": "monthly",
            "start_date": _iso(today - timedelta(days=40)),
            "next_invoice_date": _iso(today + timedelta(days=10)),
            "amount": "500.00",
            "included_hours": "10",
            "links": links,
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()


async def _entry(c, headers, *, project_id: str | None, billable: bool | None = None) -> dict:
    """One closed hour. ``billable`` left out is the point of the whole issue."""
    started = datetime.now(UTC).replace(hour=9, minute=0, second=0, microsecond=0)
    body: dict = {
        "project_id": project_id,
        "started_at": started.isoformat(),
        "ended_at": (started + timedelta(hours=1)).isoformat(),
    }
    if billable is not None:
        body["billable"] = billable
    res = await c.post("/api/v1/time/entries", json=body, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


async def test_linking_a_project_to_a_subscription_clears_its_billable_default(client_for) -> None:
    t = await make_tenant("subs-billable-link")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company, project = await _company_and_project(c, headers)

        links = [{"entity_type": "project", "entity_id": project["id"]}]
        await _subscription(c, headers, company["id"], links=links)

        after = (await c.get(f"/api/v1/projects/{project['id']}", headers=headers)).json()
        assert after["billable_default"] is False

        # And the project's own trail says why it stopped being billable (§16), rather than
        # leaving the change to be discovered on the agreement that caused it.
        trail = (
            await c.get(
                "/api/v1/activity",
                params={"entity_type": "project", "entity_id": project["id"]},
                headers=headers,
            )
        ).json()
        changes = [
            item["payload"]["changes"]
            for item in trail
            if item["action"] == "updated" and "billable_default" in item["payload"].get(
                "changes", {}
            )
        ]
        assert changes and changes[0]["billable_default"] == {"from": True, "to": False}


async def test_a_new_time_entry_inherits_the_projects_billable_default(client_for) -> None:
    t = await make_tenant("subs-billable-entry")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company, covered = await _company_and_project(c, headers)
        _, ordinary = await _company_and_project(c, headers, project="Los project")

        links = [{"entity_type": "project", "entity_id": covered["id"]}]
        await _subscription(c, headers, company["id"], links=links)

        # Saying nothing: the project answers.
        assert (await _entry(c, headers, project_id=covered["id"]))["billable"] is False
        assert (await _entry(c, headers, project_id=ordinary["id"]))["billable"] is True
        # No project at all is still plainly billable — nothing says otherwise.
        assert (await _entry(c, headers, project_id=None))["billable"] is True

        # Meerwerk outside the retainer: an explicit flag is the caller's call, and stands.
        assert (await _entry(c, headers, project_id=covered["id"], billable=True))["billable"]

        # A timer inherits it the same way — the timer bar has no billable control to state one.
        started = await c.post(
            "/api/v1/time/timer/start", json={"project_id": covered["id"]}, headers=headers
        )
        assert started.status_code == 201, started.text
        assert started.json()["billable"] is False
        await c.post("/api/v1/time/timer/stop", headers=headers)


async def test_the_tenants_own_override_survives_a_resaved_agreement(client_for) -> None:
    """It is a default, not a lock: re-posting the same links must not keep re-deciding."""
    t = await make_tenant("subs-billable-override")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company, project = await _company_and_project(c, headers)
        links = [{"entity_type": "project", "entity_id": project["id"]}]
        sub = await _subscription(c, headers, company["id"], links=links)

        # The agency agrees this project's hours *are* invoiced on top of the retainer.
        back = await c.patch(
            f"/api/v1/projects/{project['id']}",
            json={"billable_default": True},
            headers=headers,
        )
        assert back.status_code == 200 and back.json()["billable_default"] is True

        # An unrelated edit to the agreement re-posts the same links, and leaves that alone.
        again = await c.patch(
            f"/api/v1/subscriptions/{sub['id']}",
            json={"name": "Onderhoudscontract 2027", "links": links},
            headers=headers,
        )
        assert again.status_code == 200, again.text
        after = (await c.get(f"/api/v1/projects/{project['id']}", headers=headers)).json()
        assert after["billable_default"] is True

        # Adding a *second*, genuinely new project does clear that one — and only that one.
        _, second = await _company_and_project(c, headers, project="Tweede project")
        added = await c.patch(
            f"/api/v1/subscriptions/{sub['id']}",
            json={
                "links": [*links, {"entity_type": "project", "entity_id": second["id"]}],
            },
            headers=headers,
        )
        assert added.status_code == 200, added.text
        assert (
            await c.get(f"/api/v1/projects/{project['id']}", headers=headers)
        ).json()["billable_default"] is True
        assert (
            await c.get(f"/api/v1/projects/{second['id']}", headers=headers)
        ).json()["billable_default"] is False
