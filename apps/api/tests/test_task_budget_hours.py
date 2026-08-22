"""A task's hour budget, where the hours are logged (#313).

``Task.allocated_minutes`` existed and was drawn exactly once, on the task's own card. The two
screens that need it — the entry form and the task list — could not have it, because the list
row carried no logged minutes at all.

What is pinned here is not that a number appears; it is every decision the enrichment took:
it is **opt-in** (a row carries only what its screen draws), it is **gated** on
``time.entry.read`` and **absent rather than refused** for a caller without it, a running timer
has not spent the budget, over budget reads negative, and the aggregate carries the company
horizon the raw table read it replaced did not.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.auth.models import User
from app.db import async_session_maker
from tests.conftest import FAR_FUTURE_DUE, auth_cookie, make_tenant


def _iso(dt: datetime) -> str:
    return dt.isoformat()


async def _company(client, headers, name: str = "Acme") -> str:
    res = await client.post("/api/v1/companies", json={"name": name}, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _task(
    client,
    headers,
    *,
    title: str = "Nieuwsbrief",
    company_id: str | None = None,
    allocated_minutes: int | None = None,
) -> str:
    res = await client.post(
        "/api/v1/tasks",
        json={
            "due_date": FAR_FUTURE_DUE,
            "title": title,
            "company_id": company_id,
            "allocated_minutes": allocated_minutes,
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _entry(
    client,
    headers,
    *,
    task_id: str | None = None,
    company_id: str | None = None,
    minutes: int,
    day: int = 0,
) -> str:
    started = datetime(2026, 3, 2, 9, 0, tzinfo=UTC) + timedelta(days=day)
    res = await client.post(
        "/api/v1/time/entries",
        json={
            "task_id": task_id,
            "company_id": company_id,
            "started_at": _iso(started),
            "ended_at": _iso(started + timedelta(minutes=minutes)),
            "billable": True,
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _row(payload: dict, entity_id: str) -> dict:
    return next(item for item in payload["items"] if item["id"] == entity_id)


# --- the list row --------------------------------------------------------------- #
async def test_the_burn_is_absent_until_asked_for(client_for) -> None:
    """``hours=true`` is opt-in: the ordinary list pays for no aggregate (§9)."""
    t = await make_tenant("tb-optin")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        task = await _task(c, headers, allocated_minutes=180)
        await _entry(c, headers, task_id=task, minutes=90)

        plain = _row((await c.get("/api/v1/tasks", headers=headers)).json(), task)
        assert plain["logged_minutes"] is None
        assert plain["remaining_minutes"] is None

        asked = _row((await c.get("/api/v1/tasks?hours=true", headers=headers)).json(), task)
        assert asked["logged_minutes"] == 90
        assert asked["remaining_minutes"] == 90


async def test_a_running_timer_has_not_spent_the_budget(client_for) -> None:
    """``ended_at IS NOT NULL``, like every other sum in the time module."""
    t = await make_tenant("tb-timer")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        task = await _task(c, headers, allocated_minutes=120)
        await _entry(c, headers, task_id=task, minutes=30)
        running = await c.post(
            "/api/v1/time/timer/start", json={"task_id": task}, headers=headers
        )
        assert running.status_code in (200, 201), running.text

        row = _row((await c.get("/api/v1/tasks?hours=true", headers=headers)).json(), task)
        assert row["logged_minutes"] == 30
        assert row["remaining_minutes"] == 90


async def test_over_budget_reads_negative_and_no_allocation_reads_nothing(client_for) -> None:
    """Unclamped, exactly like ``remaining_hours``: 40 % over must not look like "just landed".

    And a task with no allocation has ``remaining_minutes: None`` — there is nothing to remain
    *of nothing* — while still reporting what was spent.
    """
    t = await make_tenant("tb-over")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        over = await _task(c, headers, title="Uitgelopen", allocated_minutes=60)
        await _entry(c, headers, task_id=over, minutes=90)
        loose = await _task(c, headers, title="Geen budget")
        await _entry(c, headers, task_id=loose, minutes=45, day=1)

        payload = (await c.get("/api/v1/tasks?hours=true", headers=headers)).json()
        assert _row(payload, over)["remaining_minutes"] == -30
        assert _row(payload, loose)["logged_minutes"] == 45
        assert _row(payload, loose)["remaining_minutes"] is None


async def test_a_lookup_list_can_still_ask_for_the_burn(client_for) -> None:
    """``meta=false`` skips the label/checklist/comment chips, not the budget.

    The time module's task combobox *is* that lookup (`time/+layout.server.ts`), and it is the
    one place the burn is needed before an entry is saved.
    """
    t = await make_tenant("tb-lookup")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        task = await _task(c, headers, allocated_minutes=240)
        await _entry(c, headers, task_id=task, minutes=60)

        row = _row(
            (
                await c.get(
                    "/api/v1/tasks?hours=true&meta=false&count=false", headers=headers
                )
            ).json(),
            task,
        )
        assert row["logged_minutes"] == 60
        assert row["remaining_minutes"] == 180


# --- the card ------------------------------------------------------------------- #
async def test_the_detail_card_carries_the_same_two_numbers(client_for) -> None:
    t = await make_tenant("tb-card")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        task = await _task(c, headers, allocated_minutes=120)
        await _entry(c, headers, task_id=task, minutes=45)

        detail = (await c.get(f"/api/v1/tasks/{task}", headers=headers)).json()
        assert detail["logged_minutes"] == 45
        assert detail["remaining_minutes"] == 75


# --- who may see it ------------------------------------------------------------- #
async def _invite_client(c, headers, email: str) -> User:
    invited = await c.post(
        "/api/v1/members/invite", json={"email": email, "role": "client"}, headers=headers
    )
    assert invited.status_code in (200, 201), invited.text
    async with async_session_maker() as session:
        user = await session.scalar(select(User).where(User.email == email))
    assert user is not None
    return user


async def test_a_caller_without_time_entry_read_is_omitted_not_refused(client_for) -> None:
    """The gate is ``time.entry.read``, and it drops two fields — never the request.

    ``hours=true`` is an enrichment flag on a route the caller may otherwise call, so a 403
    would break the ordinary task list for someone who simply may not read hours. The seeded
    ``client`` role holds ``tasks.task.read`` (the portal reads tasks) and no time permission,
    which is exactly the caller this rule exists for: team-wide burned hours are not a client's
    business, on the list *or* on the card.
    """
    t = await make_tenant("tb-gate")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        company = await _company(c, headers, "Klant BV")
        task = await _task(c, headers, company_id=company, allocated_minutes=120)
        await _entry(c, headers, task_id=task, company_id=company, minutes=90)
        # Visible to the client, or the horizon would be doing this test's work for it.
        assert (
            await c.patch(
                f"/api/v1/tasks/{task}", json={"visible_to_client": True}, headers=headers
            )
        ).status_code == 200

        client_user = await _invite_client(c, headers, "extern-tb@example.com")
        members = (await c.get("/api/v1/members", headers=headers)).json()
        rows = members["items"] if isinstance(members, dict) else members
        membership_id = next(
            m["membership_id"] for m in rows if m["email"] == client_user.email
        )
        group = (
            await c.post(
                "/api/v1/companies/groups", json={"name": "Klantgroep"}, headers=headers
            )
        ).json()
        assert (
            await c.put(
                f"/api/v1/companies/groups/{group['id']}/companies",
                json={"company_ids": [company]},
                headers=headers,
            )
        ).status_code == 204
        assert (
            await c.put(
                f"/api/v1/companies/groups/{group['id']}/memberships",
                json={"membership_ids": [membership_id]},
                headers=headers,
            )
        ).status_code == 204
        client_headers = await auth_cookie(client_user, org_id=t.org.id)

        listed = await c.get("/api/v1/tasks?hours=true", headers=client_headers)
        assert listed.status_code == 200, listed.text
        row = _row(listed.json(), task)
        # Absent, never zero: "nobody may tell you" and "nothing logged" are different answers.
        assert row["logged_minutes"] is None
        assert row["remaining_minutes"] is None
        # The allocation itself is on the task and stays where it was.
        assert row["allocated_minutes"] == 120

        card = await c.get(f"/api/v1/tasks/{task}", headers=client_headers)
        assert card.status_code == 200, card.text
        assert card.json()["logged_minutes"] is None
        assert card.json()["remaining_minutes"] is None

        # The agency reads the same task and gets the burn.
        assert _row(
            (await c.get("/api/v1/tasks?hours=true", headers=headers)).json(), task
        )["logged_minutes"] == 90


# --- tenant isolation ------------------------------------------------------------ #
async def test_another_tenants_hours_never_reach_this_burn(client_for) -> None:
    """Golden Rule 1 on the aggregate: the sum is ``org_id``-bound, not just the task read."""
    a = await make_tenant("tb-iso-a")
    b = await make_tenant("tb-iso-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)

    async with client_for(a.host) as ca:
        task_a = await _task(ca, a_headers, title="Alpha", allocated_minutes=120)
        await _entry(ca, a_headers, task_id=task_a, minutes=30)
    async with client_for(b.host) as cb:
        task_b = await _task(cb, b_headers, title="Beta", allocated_minutes=120)
        await _entry(cb, b_headers, task_id=task_b, minutes=600)

    async with client_for(a.host) as ca:
        payload = (await ca.get("/api/v1/tasks?hours=true", headers=a_headers)).json()
        assert [r["title"] for r in payload["items"]] == ["Alpha"]
        assert _row(payload, task_a)["logged_minutes"] == 30
