"""The org-wide uninvoiced report (issue #277).

``GET /api/v1/invoicing/uninvoiced`` is the read-only backlog of approved + billable +
not-yet-invoiced hours: the ``/unbilled`` predicate without its company scope, bucketed
server-side. Covers the population filter, every grouping family, the #226 rate chain with
the invoicing default folded in, org-local calendar buckets, the entry cap, and — like every
module — tenant isolation.
"""

from __future__ import annotations

from datetime import UTC, datetime

from tests.conftest import Tenant, auth_cookie, make_tenant
from tests.test_invoicing_api import _company, _setup_org


async def _entry(
    client,
    headers,
    *,
    company_id: str | None,
    minutes: int,
    started_at: datetime,
    project_id: str | None = None,
    billable: bool = True,
    description: str = "Werkzaamheden",
) -> str:
    payload = {
        "minutes": minutes,
        "started_at": started_at.isoformat(),
        "billable": billable,
        "description": description,
    }
    if company_id:
        payload["company_id"] = company_id
    if project_id:
        payload["project_id"] = project_id
    resp = await client.post("/api/v1/time/entries", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _approve(client, headers, entry_ids: list[str]) -> None:
    resp = await client.post(
        "/api/v1/time/entries/approve",
        json={"entry_ids": entry_ids, "approved": True},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text


async def test_uninvoiced_report_population_groupings_and_rates(client_for) -> None:
    """Two companies, two loggers, two rates: the report prices like ``/unbilled`` (#226),
    counts only approved + billable + uninvoiced, and buckets per grouping."""
    from tests.test_task_subresources import add_member

    tenant: Tenant = await make_tenant("uninv-report")
    headers = await auth_cookie(tenant.user)
    member = await add_member(tenant)
    member_headers = await auth_cookie(member)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        alpha = await _company(client, headers, name="Alpha BV")
        beta = await _company(client, headers, name="Beta BV")
        # Owner bills at a personal €90/h; the member falls back to the leave org default
        # of €60/h — the invoicing default of €10 must never win while those exist.
        await client.put(
            f"/api/v1/leave/rate/{tenant.user.id}",
            json={"hourly_rate": "90.00"},
            headers=headers,
        )
        await client.put(
            "/api/v1/leave/settings",
            json={"default_hourly_rate": "60.00"},
            headers=headers,
        )
        await client.put(
            "/api/v1/invoicing/settings",
            json={"default_hourly_rate": "10.00"},
            headers=headers,
        )
        project = (
            await client.post(
                "/api/v1/projects",
                json={"name": "Retainer", "company_id": alpha},
                headers=headers,
            )
        ).json()

        start = datetime(2026, 3, 10, 9, 0, tzinfo=UTC)
        owner_alpha = await _entry(
            client, headers, company_id=alpha, minutes=60, started_at=start
        )
        member_alpha = await _entry(
            client,
            member_headers,
            company_id=alpha,
            minutes=90,
            started_at=start,
            project_id=project["id"],
        )
        owner_beta = await _entry(
            client, headers, company_id=beta, minutes=30, started_at=start
        )
        # Excluded: not approved / not billable / already invoiced.
        await _entry(client, headers, company_id=alpha, minutes=45, started_at=start)
        unbillable = await _entry(
            client, headers, company_id=alpha, minutes=45, started_at=start, billable=False
        )
        invoiced = await _entry(
            client, headers, company_id=beta, minutes=45, started_at=start
        )
        await _approve(
            client, headers, [owner_alpha, member_alpha, owner_beta, unbillable, invoiced]
        )
        stamped = await client.post(
            "/api/v1/time/entries/invoice",
            json={"entry_ids": [invoiced], "invoiced": True},
            headers=headers,
        )
        assert stamped.status_code == 200

        # Grouped by client (the default): alphabetical, exact money per group.
        report = (
            await client.get("/api/v1/invoicing/uninvoiced", headers=headers)
        ).json()
        assert report["group"] == "company"
        assert [g["label"] for g in report["groups"]] == ["Alpha BV", "Beta BV"]
        by_label = {g["label"]: g for g in report["groups"]}
        # Alpha: 60 min × €90 + 90 min × €60 = €180; Beta: 30 min × €90 = €45.
        assert by_label["Alpha BV"]["minutes"] == 150
        assert by_label["Alpha BV"]["amount"] == "180.00"
        assert by_label["Alpha BV"]["count"] == 2
        assert by_label["Alpha BV"]["key"] == alpha
        assert by_label["Beta BV"]["minutes"] == 30
        assert by_label["Beta BV"]["amount"] == "45.00"
        assert report["total_minutes"] == 180
        assert report["total_amount"] == "225.00"
        assert report["total_count"] == 3
        assert report["truncated"] is False
        # Every entry carries the key of the bucket it was summed under.
        assert {e["group_key"] for e in report["entries"]} == {alpha, beta}
        assert {e["id"] for e in report["entries"]} == {
            owner_alpha, member_alpha, owner_beta,
        }
        rates = sorted(e["rate"] for e in report["entries"])
        assert rates == ["60.00", "90.00", "90.00"]

        # Grouped by employee: one bucket per logger, ordered by name.
        by_user = (
            await client.get("/api/v1/invoicing/uninvoiced?group=user", headers=headers)
        ).json()
        assert len(by_user["groups"]) == 2
        assert by_user["total_amount"] == "225.00"
        assert {g["count"] for g in by_user["groups"]} == {1, 2}

        # Grouped by project: the retainer plus the "no project" stray bucket last.
        by_project = (
            await client.get(
                "/api/v1/invoicing/uninvoiced?group=project", headers=headers
            )
        ).json()
        assert [g["label"] for g in by_project["groups"]] == ["Retainer", None]
        assert by_project["groups"][0]["key"] == project["id"]
        assert by_project["groups"][1]["key"] == ""
        assert by_project["groups"][1]["minutes"] == 90

        # The cap truncates the detail, never the totals.
        capped = (
            await client.get("/api/v1/invoicing/uninvoiced?limit=1", headers=headers)
        ).json()
        assert len(capped["entries"]) == 1
        assert capped["truncated"] is True
        assert capped["total_minutes"] == 180
        assert capped["total_amount"] == "225.00"

        # A plain member holds no invoicing read (reads default to admins): 403, and the
        # page never renders for them (nav + server guard mirror this).
        refused = await client.get("/api/v1/invoicing/uninvoiced", headers=member_headers)
        assert refused.status_code == 403


async def test_uninvoiced_report_buckets_in_org_local_calendar(client_for) -> None:
    """23:30 UTC on 31 January is 1 February in Amsterdam: the bucket follows the org's
    calendar (§8), for day, week, month and year alike."""
    tenant: Tenant = await make_tenant("uninv-tz")
    headers = await auth_cookie(tenant.user)
    async with client_for(tenant.host) as client:
        await _setup_org(client, headers)
        company_id = await _company(client, headers)
        await client.put(
            "/api/v1/invoicing/settings",
            json={"default_hourly_rate": "80.00"},
            headers=headers,
        )
        entry = await _entry(
            client,
            headers,
            company_id=company_id,
            minutes=120,
            started_at=datetime(2026, 1, 31, 23, 30, tzinfo=UTC),
        )
        await _approve(client, headers, [entry])

        for group, key in (
            ("day", "2026-02-01"),
            ("week", "2026-W05"),
            ("month", "2026-02"),
            ("year", "2026"),
        ):
            report = (
                await client.get(
                    f"/api/v1/invoicing/uninvoiced?group={group}", headers=headers
                )
            ).json()
            assert [g["key"] for g in report["groups"]] == [key], group
            assert report["groups"][0]["label"] is None
            # No leave rates anywhere: the invoicing default is the last resort (#226).
            assert report["groups"][0]["amount"] == "160.00"
            assert report["entries"][0]["group_key"] == key
            assert report["entries"][0]["entry_date"] == "2026-02-01"


async def test_uninvoiced_report_tenant_isolation(client_for) -> None:
    a: Tenant = await make_tenant("uninv-iso-a")
    b: Tenant = await make_tenant("uninv-iso-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)

    async with client_for(a.host) as ca:
        await _setup_org(ca, a_headers)
        company_id = await _company(ca, a_headers)
        entry = await _entry(
            ca,
            a_headers,
            company_id=company_id,
            minutes=60,
            started_at=datetime(2026, 3, 2, 9, 0, tzinfo=UTC),
        )
        await _approve(ca, a_headers, [entry])
        mine = (await ca.get("/api/v1/invoicing/uninvoiced", headers=a_headers)).json()
        assert mine["total_count"] == 1

    async with client_for(b.host) as cb:
        theirs = (await cb.get("/api/v1/invoicing/uninvoiced", headers=b_headers)).json()
        assert theirs["groups"] == []
        assert theirs["entries"] == []
        assert theirs["total_count"] == 0
        assert theirs["total_minutes"] == 0
