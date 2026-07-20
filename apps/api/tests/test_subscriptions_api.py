"""Subscriptions module (#30): CRUD, price history, links, usage, summary, cron, isolation."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.core.jobs import run_per_org  # noqa: F401  (import proves the cron seam wires up)
from app.modules.subscriptions.service import add_months
from tests.conftest import auth_cookie, make_tenant


def _iso(d: date) -> str:
    return d.isoformat()


async def test_subscription_crud_price_history_and_links(client_for) -> None:
    t = await make_tenant("subs-crud")
    headers = await auth_cookie(t.user)
    today = datetime.now(UTC).date()
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Retainer BV"}, headers=headers)
        ).json()
        project = (
            await c.post(
                "/api/v1/projects",
                json={"name": "Onderhoud", "company_id": company["id"]},
                headers=headers,
            )
        ).json()

        created = await c.post(
            "/api/v1/subscriptions",
            json={
                "company_id": company["id"],
                "name": "Onderhoudscontract",
                "status": "active",
                "interval": "monthly",
                "start_date": _iso(today - timedelta(days=40)),
                "next_invoice_date": _iso(today + timedelta(days=10)),
                "amount": "500.00",
                "included_hours": "10",
                "rollover": {"mode": "carry", "expires_after_periods": 3},
                "lines": [
                    {"description": "Hosting & onderhoud", "quantity": "1", "unit_amount": "500.00"}
                ],
                "links": [{"entity_type": "project", "entity_id": project["id"]}],
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        sub = created.json()
        assert sub["company_name"] == "Retainer BV"
        assert sub["amount"] == "500.00"
        assert sub["monthly_equivalent"] == 500.0
        assert sub["rollover"] == {"mode": "carry", "expires_after_periods": 3}
        assert len(sub["lines"]) == 1 and len(sub["links"]) == 1

        # A price change appends history; the past price survives.
        updated = await c.patch(
            f"/api/v1/subscriptions/{sub['id']}",
            json={"amount": "550.00"},
            headers=headers,
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["amount"] == "550.00"
        prices = (
            await c.get(f"/api/v1/subscriptions/{sub['id']}/prices", headers=headers)
        ).json()
        assert [p["amount"] for p in prices] == ["550.00", "500.00"]

        # Usage measures the linked project's logged time in the current period (#25's numbers).
        await c.post(
            "/api/v1/time/entries",
            json={
                "started_at": datetime.now(UTC).isoformat(),
                "minutes": 90,
                "project_id": project["id"],
            },
            headers=headers,
        )
        with_usage = (
            await c.get(
                f"/api/v1/subscriptions/{sub['id']}", params={"usage": True}, headers=headers
            )
        ).json()
        assert with_usage["usage"]["used_hours"] == 1.5
        assert with_usage["usage"]["overage_hours"] == 0.0

        # A link to another tenant's (or nonexistent) project is refused.
        bad = await c.patch(
            f"/api/v1/subscriptions/{sub['id']}",
            json={"links": [{"entity_type": "project", "entity_id": company["id"]}]},
            headers=headers,
        )
        assert bad.status_code == 400

        # The company panel data path: subscriptions for the company.
        for_company = (
            await c.get(
                "/api/v1/subscriptions",
                params={"company_id": company["id"]},
                headers=headers,
            )
        ).json()
        assert for_company["total"] == 1

        # The record carries an activity trail (§16).
        trail = (
            await c.get(
                "/api/v1/activity",
                params={"entity_type": "subscription", "entity_id": sub["id"]},
                headers=headers,
            )
        ).json()
        actions = [row["action"] for row in trail]
        assert "created" in actions


async def test_summary_reports_mrr_and_upcoming(client_for) -> None:
    t = await make_tenant("subs-mrr")
    headers = await auth_cookie(t.user)
    today = datetime.now(UTC).date()
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Klant"}, headers=headers)
        ).json()
        for name, interval, amount in (("A", "monthly", "100.00"), ("B", "yearly", "1200.00")):
            await c.post(
                "/api/v1/subscriptions",
                json={
                    "company_id": company["id"],
                    "name": name,
                    "status": "active",
                    "interval": interval,
                    "start_date": _iso(today),
                    "next_invoice_date": _iso(today + timedelta(days=5)),
                    "amount": amount,
                },
                headers=headers,
            )
        summary = (await c.get("/api/v1/subscriptions/summary", headers=headers)).json()
        assert summary["mrr"] == 200.0  # 100 + 1200/12
        assert summary["arr"] == 2400.0
        assert summary["active_count"] == 2
        assert len(summary["upcoming"]) == 2


async def test_due_cron_emits_and_advances(client_for) -> None:
    from app.modules.subscriptions.jobs import advance_subscriptions

    t = await make_tenant("subs-cron")
    headers = await auth_cookie(t.user)
    today = datetime.now(UTC).date()
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Cyclus"}, headers=headers)
        ).json()
        sub = (
            await c.post(
                "/api/v1/subscriptions",
                json={
                    "company_id": company["id"],
                    "name": "Maandelijks",
                    "status": "active",
                    "interval": "monthly",
                    "start_date": _iso(add_months(today, -2)),
                    "next_invoice_date": _iso(today),
                    "amount": "250.00",
                },
                headers=headers,
            )
        ).json()

    fired: list[dict] = []

    async def listener(ctx, payload) -> None:
        fired.append(payload)

    from app.core import events

    events.subscribe("subscription.due", listener)
    try:
        await advance_subscriptions({})
    finally:
        events._handlers["subscription.due"].remove(listener)

    assert len(fired) == 1
    assert fired[0]["amount"] == "250.00"
    assert fired[0]["period_end"] == _iso(today)

    async with client_for(t.host) as c:
        after = (
            await c.get(f"/api/v1/subscriptions/{sub['id']}", headers=headers)
        ).json()
        assert after["next_invoice_date"] == _iso(add_months(today, 1))


async def test_activation_derives_next_invoice_date(client_for) -> None:
    """#223: the create form no longer asks for the first invoice date — the service derives
    it (``start_date`` + one period) on the first transition into ``active``, so the cron
    never silently skips a new agreement."""
    t = await make_tenant("subs-derive")
    headers = await auth_cookie(t.user)
    today = datetime.now(UTC).date()
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Derive BV"}, headers=headers)
        ).json()

        def body(**extra) -> dict:
            return {"company_id": company["id"], "amount": "300.00", **extra}

        # Created active without a date: the first cycle boundary is derived, per interval.
        sub = (
            await c.post(
                "/api/v1/subscriptions",
                json=body(name="Kwartaal", status="active", interval="quarterly",
                          start_date=_iso(today)),
                headers=headers,
            )
        ).json()
        assert sub["next_invoice_date"] == _iso(add_months(today, 3))

        # An explicit date is the operator's call — never overwritten.
        explicit = (
            await c.post(
                "/api/v1/subscriptions",
                json=body(name="Handmatig", status="active", interval="monthly",
                          start_date=_iso(today),
                          next_invoice_date=_iso(today + timedelta(days=3))),
                headers=headers,
            )
        ).json()
        assert explicit["next_invoice_date"] == _iso(today + timedelta(days=3))

        # A draft has nothing to invoice yet; the date arrives on its first activation
        # (the same seam the edit modal and the bulk status action go through).
        draft = (
            await c.post(
                "/api/v1/subscriptions",
                json=body(name="Concept", interval="monthly", start_date=_iso(today)),
                headers=headers,
            )
        ).json()
        assert draft["next_invoice_date"] is None
        activated = (
            await c.patch(
                f"/api/v1/subscriptions/{draft['id']}",
                json={"status": "active"},
                headers=headers,
            )
        ).json()
        assert activated["next_invoice_date"] == _iso(add_months(today, 1))

        # An agreement already over before its first boundary stays uninvoiced — the cron's
        # own past-the-end rule.
        ended = (
            await c.post(
                "/api/v1/subscriptions",
                json=body(name="Kort", status="active", interval="monthly",
                          start_date=_iso(today), end_date=_iso(today + timedelta(days=14))),
                headers=headers,
            )
        ).json()
        assert ended["next_invoice_date"] is None


async def test_subscriptions_tenant_isolation_and_permission(client_for) -> None:
    a = await make_tenant("subs-iso-a")
    b = await make_tenant("subs-iso-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)
    async with client_for(a.host) as ca:
        company = (
            await ca.post("/api/v1/companies", json={"name": "A"}, headers=a_headers)
        ).json()
        sub_id = (
            await ca.post(
                "/api/v1/subscriptions",
                json={
                    "company_id": company["id"],
                    "name": "Iso",
                    "start_date": "2026-01-01",
                    "amount": "10.00",
                },
                headers=a_headers,
            )
        ).json()["id"]
    async with client_for(b.host) as cb:
        assert (
            await cb.get(f"/api/v1/subscriptions/{sub_id}", headers=b_headers)
        ).status_code == 404
        assert (await cb.get("/api/v1/subscriptions", headers=b_headers)).json()["total"] == 0


async def test_list_filters_by_linked_entity_with_usage(client_for) -> None:
    """The project panel's question: which agreements cover this project, and how full is
    the bundle?"""
    t = await make_tenant("subs-entity")
    headers = await auth_cookie(t.user)
    today = datetime.now(UTC).date()
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Bundel BV"}, headers=headers)
        ).json()
        linked = (
            await c.post(
                "/api/v1/projects",
                json={"name": "Binnen bundel", "company_id": company["id"]},
                headers=headers,
            )
        ).json()
        unlinked = (
            await c.post(
                "/api/v1/projects",
                json={"name": "Los werk", "company_id": company["id"]},
                headers=headers,
            )
        ).json()
        await c.post(
            "/api/v1/subscriptions",
            json={
                "company_id": company["id"],
                "name": "SLA",
                "status": "active",
                "interval": "monthly",
                "start_date": _iso(today - timedelta(days=40)),
                "next_invoice_date": _iso(today + timedelta(days=10)),
                "amount": "250.00",
                "included_hours": "8",
                "links": [{"entity_type": "project", "entity_id": linked["id"]}],
            },
            headers=headers,
        )

        hit = (
            await c.get(
                f"/api/v1/subscriptions?entity_type=project&entity_id={linked['id']}&usage=true",
                headers=headers,
            )
        ).json()
        assert [s["name"] for s in hit["items"]] == ["SLA"]
        usage = hit["items"][0]["usage"]
        assert usage is not None and float(usage["included_hours"]) == 8.0

        miss = (
            await c.get(
                f"/api/v1/subscriptions?entity_type=project&entity_id={unlinked['id']}",
                headers=headers,
            )
        ).json()
        assert miss["items"] == []
