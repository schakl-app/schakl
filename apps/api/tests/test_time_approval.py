"""Hours approval / invoicing flow: manager gating, locking, report filters, isolation."""

from __future__ import annotations

from datetime import UTC, datetime

from tests.conftest import auth_cookie, make_tenant
from tests.test_task_subresources import add_member


async def _entry(client, headers, **overrides) -> dict:
    body = {"started_at": datetime.now(UTC).isoformat(), "minutes": 60, **overrides}
    r = await client.post("/api/v1/time/entries", json=body, headers=headers)
    assert r.status_code == 201
    return r.json()


async def test_approve_locks_entry_for_member(client_for) -> None:
    t = await make_tenant("appr-lock")
    owner_headers = await auth_cookie(t.user)
    member = await add_member(t)
    member_headers = await auth_cookie(member)

    async with client_for(t.host) as c:
        entry = await _entry(c, member_headers)
        assert entry["approved_at"] is None

        # Members cannot approve…
        assert (
            await c.post(
                "/api/v1/time/entries/approve",
                json={"entry_ids": [entry["id"]], "approved": True},
                headers=member_headers,
            )
        ).status_code == 403

        # …managers can.
        approved = await c.post(
            "/api/v1/time/entries/approve",
            json={"entry_ids": [entry["id"]], "approved": True},
            headers=owner_headers,
        )
        assert approved.json() == {"updated": 1}

        fetched = (
            await c.get(f"/api/v1/time/entries/{entry['id']}", headers=member_headers)
        ).json()
        assert fetched["approved_at"] is not None
        assert fetched["approved_by_user_id"] == str(t.user.id)

        # The owner of the hours can no longer edit or delete them…
        locked = await c.patch(
            f"/api/v1/time/entries/{entry['id']}",
            json={"minutes": 90},
            headers=member_headers,
        )
        assert locked.status_code == 403
        assert locked.json()["error"]["message"] == "errors.approved_locked"
        assert (
            await c.delete(f"/api/v1/time/entries/{entry['id']}", headers=member_headers)
        ).status_code == 403

        # …but a manager still can.
        assert (
            await c.patch(
                f"/api/v1/time/entries/{entry['id']}",
                json={"minutes": 90},
                headers=owner_headers,
            )
        ).status_code == 200


async def test_unapprove_clears_invoiced(client_for) -> None:
    t = await make_tenant("appr-clear")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        entry = await _entry(c, headers)
        ids = {"entry_ids": [entry["id"]]}
        approve = {**ids, "approved": True}
        invoice = {**ids, "invoiced": True}
        await c.post("/api/v1/time/entries/approve", json=approve, headers=headers)
        await c.post("/api/v1/time/entries/invoice", json=invoice, headers=headers)

        fetched = (await c.get(f"/api/v1/time/entries/{entry['id']}", headers=headers)).json()
        assert fetched["invoiced_at"] is not None

        unapprove = {**ids, "approved": False}
        await c.post("/api/v1/time/entries/approve", json=unapprove, headers=headers)
        fetched = (await c.get(f"/api/v1/time/entries/{entry['id']}", headers=headers)).json()
        assert fetched["approved_at"] is None
        assert fetched["invoiced_at"] is None  # unapproving clears the invoiced mark


async def test_report_filters_and_totals(client_for) -> None:
    t = await make_tenant("appr-report")
    owner_headers = await auth_cookie(t.user)
    member = await add_member(t)
    member_headers = await auth_cookie(member)

    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Rep Co"}, headers=owner_headers)
        ).json()
        e1 = await _entry(c, owner_headers, minutes=60, company_id=company["id"])
        await _entry(c, member_headers, minutes=30, billable=False)

        # Members may not see the org-wide report.
        assert (await c.get("/api/v1/time/report", headers=member_headers)).status_code == 403

        report = (await c.get("/api/v1/time/report", headers=owner_headers)).json()
        assert report["totals"]["count"] == 2
        assert report["totals"]["minutes"] == 90
        assert report["totals"]["billable_minutes"] == 60
        assert report["totals"]["open_minutes"] == 90
        assert report["totals"]["to_invoice_minutes"] == 0

        await c.post(
            "/api/v1/time/entries/approve",
            json={"entry_ids": [e1["id"]], "approved": True},
            headers=owner_headers,
        )
        report = (await c.get("/api/v1/time/report", headers=owner_headers)).json()
        assert report["totals"]["approved_minutes"] == 60
        assert report["totals"]["to_invoice_minutes"] == 60  # approved + billable + not invoiced

        # Filters: by company, by approved flag, by user.
        by_company = (
            await c.get(
                "/api/v1/time/report",
                params={"company_id": company["id"]},
                headers=owner_headers,
            )
        ).json()
        assert by_company["totals"]["count"] == 1
        open_only = (
            await c.get("/api/v1/time/report", params={"approved": False}, headers=owner_headers)
        ).json()
        assert open_only["totals"]["count"] == 1
        by_user = (
            await c.get(
                "/api/v1/time/report",
                params={"user_id": str(member.id)},
                headers=owner_headers,
            )
        ).json()
        assert by_user["totals"]["minutes"] == 30


async def test_approval_tenant_isolation(client_for) -> None:
    a = await make_tenant("appr-iso-a")
    b = await make_tenant("appr-iso-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)

    async with client_for(a.host) as ca:
        entry = await _entry(ca, a_headers)

    async with client_for(b.host) as cb:
        # Approving another tenant's entry ids silently touches nothing.
        r = await cb.post(
            "/api/v1/time/entries/approve",
            json={"entry_ids": [entry["id"]], "approved": True},
            headers=b_headers,
        )
        assert r.json() == {"updated": 0}
        assert (await cb.get("/api/v1/time/report", headers=b_headers)).json()["totals"][
            "count"
        ] == 0

    async with client_for(a.host) as ca:
        fetched = (await ca.get(f"/api/v1/time/entries/{entry['id']}", headers=a_headers)).json()
        assert fetched["approved_at"] is None
