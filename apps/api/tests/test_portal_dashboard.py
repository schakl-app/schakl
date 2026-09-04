"""The client's homepage, and what a client login is told (portal dashboard review).

Six rules, each pinned here because each was found wrong on a real portal login:

* ``?assigned_to=`` splits "asked of you" from "done for you" — one predicate, both tiles.
* the org's dashboard template is the **staff** board; a client inherits nothing from it.
* a client reads the planned blocks on a task they may open, and never a block's note or budget.
* a client reads their own companies' agreements (never a draft, never the agency's notes), and
  nothing the module keeps at ``:any`` — the MRR summary, the preset library.
* a client never reads a task's repeat rule.
* a client's contact person is told when they are named in a comment, or when a task assigned
  to them is commented on — and mails by default, because they are not in the app to see a bell.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.auth.models import User
from app.core.models import DashboardPref
from app.db import async_session_maker, set_current_org
from app.modules.notifications.models import Notification, NotificationDelivery, NotificationEvent
from tests.conftest import FAR_FUTURE_DUE, auth_cookie, make_tenant, org_today
from tests.test_notifications_emits import _inbox


async def _portal(client_for, slug: str):
    """A tenant, a client company, a contact person on it with a portal login — and a second
    company the contact is *not* on, so "theirs" has something to be measured against."""
    t = await make_tenant(slug)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        mine = (await c.post("/api/v1/companies", json={"name": "Mijn BV"}, headers=headers)).json()
        other = (
            await c.post("/api/v1/companies", json={"name": "Andere BV"}, headers=headers)
        ).json()
        contact = (
            await c.post(
                "/api/v1/contacts",
                json={
                    "first_name": "Piet",
                    "last_name": "Klant",
                    "email": f"piet-{slug}@example.com",
                    "company_ids": [mine["id"]],
                },
                headers=headers,
            )
        ).json()
        await c.post(f"/api/v1/portal/logins/contact/{contact['id']}", headers=headers)
    async with async_session_maker() as session:
        portal_user = await session.scalar(select(User).where(User.email == contact["email"]))
    assert portal_user is not None
    portal_headers = await auth_cookie(portal_user)
    return t, headers, portal_headers, contact, mine["id"], other["id"], portal_user


async def _task(c, headers, **payload) -> dict:
    r = await c.post(
        "/api/v1/tasks",
        json={"due_date": FAR_FUTURE_DUE, "visible_to_client": True, **payload},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


# --------------------------------------------------------------------------- #
# assigned_to: the two tiles
# --------------------------------------------------------------------------- #
async def test_assigned_to_splits_the_clients_work_from_the_agencys(client_for) -> None:
    t, headers, portal_headers, contact, mine, _other, _ = await _portal(client_for, "pd-split")
    async with client_for(t.host) as c:
        await _task(
            c,
            headers,
            title="Aanleveren teksten",
            company_id=mine,
            assignee_contact_id=contact["id"],
        )
        await _task(c, headers, title="Site bouwen", company_id=mine)
        await _task(
            c, headers, title="Intern, onzichtbaar", company_id=mine, visible_to_client=False
        )

        theirs = (
            await c.get("/api/v1/tasks?open=1&assigned_to=contact", headers=portal_headers)
        ).json()
        ours = (
            await c.get("/api/v1/tasks?open=1&assigned_to=agency", headers=portal_headers)
        ).json()
        assert [r["title"] for r in theirs["items"]] == ["Aanleveren teksten"]
        assert theirs["items"][0]["assignee_contact_name"] == "Piet Klant"
        assert [r["title"] for r in ours["items"]] == ["Site bouwen"]
        # A task appears in exactly one of the two, and the invisible one in neither.
        assert theirs["total"] + ours["total"] == 2
        # Absent means both — the filter narrows, it never widens what the horizon allows.
        both = (await c.get("/api/v1/tasks?open=1", headers=portal_headers)).json()
        assert both["total"] == 2


# --------------------------------------------------------------------------- #
# the org template is the staff board
# --------------------------------------------------------------------------- #
async def test_portal_login_does_not_inherit_the_staff_dashboard_template(client_for) -> None:
    t, headers, portal_headers, *_ = await _portal(client_for, "pd-prefs")
    async with client_for(t.host) as c:
        # The agency curates its staff board: keys no portal gallery has.
        r = await c.put(
            "/api/v1/dashboard/prefs/default",
            json={"widgets": ["time.today", "tasks.my_open"]},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert (await c.get("/api/v1/dashboard/prefs", headers=headers)).json()["source"] in (
            "default",
            "user",
        )
        # A client with no layout of their own opens on the whole portal gallery, not on a
        # template whose every key is unknown to it (which resolved to an empty homepage).
        prefs = (await c.get("/api/v1/dashboard/prefs", headers=portal_headers)).json()
        assert prefs == {"widgets": None, "columns": None, "source": "none"}
        # Their own arrangement still wins once they make one.
        saved = await c.put(
            "/api/v1/dashboard/prefs",
            json={"columns": [["tasks.portal", "invoicing.portal"]]},
            headers=portal_headers,
        )
        assert saved.status_code == 200, saved.text
        own = (await c.get("/api/v1/dashboard/prefs", headers=portal_headers)).json()
        assert own["source"] == "user"
        assert own["widgets"] == ["tasks.portal", "invoicing.portal"]
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        rows = list((await session.execute(select(DashboardPref.user_id))).scalars())
        assert None in rows  # the template row is still there for staff


# --------------------------------------------------------------------------- #
# planned blocks on the client's own task
# --------------------------------------------------------------------------- #
async def test_portal_reads_the_blocks_on_a_visible_task_and_nothing_private(client_for) -> None:
    t, headers, portal_headers, _contact, mine, other, _ = await _portal(client_for, "pd-blocks")
    day = (org_today() + timedelta(days=3)).isoformat()
    async with client_for(t.host) as c:
        visible = await _task(c, headers, title="Zichtbaar", company_id=mine, allocated_minutes=120)
        hidden = await _task(c, headers, title="Verborgen", company_id=other)
        for task in (visible, hidden):
            r = await c.post(
                "/api/v1/tasks/schedules",
                json={
                    "task_id": task["id"],
                    "day": day,
                    "start_time": "09:00:00",
                    "duration_minutes": 90,
                    "note": "eerst koffie",
                },
                headers=headers,
            )
            assert r.status_code == 201, r.text

        # The seeded client role holds `tasks.schedule.read:own`; for a client "own" is the
        # blocks on a task they may read.
        blocks = await c.get(
            f"/api/v1/tasks/schedules?task_id={visible['id']}", headers=portal_headers
        )
        assert blocks.status_code == 200, blocks.text
        rows = blocks.json()
        assert len(rows) == 1
        assert rows[0]["task_title"] == "Zichtbaar"
        assert rows[0]["user_name"]  # who is coming is the point
        # The planner's note, the hour budget and the time entry are the agency's desk.
        assert rows[0]["note"] is None
        assert rows[0]["allocated_minutes"] is None
        assert rows[0]["time_entry_id"] is None
        # Staff still read the note on the same block.
        staff = (
            await c.get(f"/api/v1/tasks/schedules?task_id={visible['id']}", headers=headers)
        ).json()
        assert staff[0]["note"] == "eerst koffie"

        # A task outside the client's horizon answers as if it had no blocks.
        none = await c.get(
            f"/api/v1/tasks/schedules?task_id={hidden['id']}", headers=portal_headers
        )
        assert none.status_code == 200
        assert none.json() == []
        # And the personal feed (no task) is what it is for everyone: the caller's own — none.
        feed = await c.get(
            f"/api/v1/tasks/schedules?date_from={day}&date_to={day}", headers=portal_headers
        )
        assert feed.status_code == 200
        assert feed.json() == []


# --------------------------------------------------------------------------- #
# subscriptions: the client's own agreements
# --------------------------------------------------------------------------- #
async def test_portal_reads_own_agreements_never_drafts_notes_or_the_book(client_for) -> None:
    t, headers, portal_headers, _contact, mine, other, _ = await _portal(client_for, "pd-subs")
    start = (datetime.now(UTC).date() - timedelta(days=60)).isoformat()

    async def make(c, company_id: str, name: str, status: str = "active") -> dict:
        r = await c.post(
            "/api/v1/subscriptions",
            json={
                "company_id": company_id,
                "name": name,
                "status": status,
                "interval": "monthly",
                "start_date": start,
                "amount": "99.00",
                "notes": "korting afgesproken met Piet",
            },
            headers=headers,
        )
        assert r.status_code == 201, r.text
        return r.json()

    async with client_for(t.host) as c:
        own = await make(c, mine, "Hosting")
        draft = await make(c, mine, "Nog in de maak", status="draft")
        theirs = await make(c, other, "Andermans SEO")

        listed = (await c.get("/api/v1/subscriptions", headers=portal_headers)).json()
        assert [row["name"] for row in listed["items"]] == ["Hosting"]
        assert listed["total"] == 1
        assert listed["items"][0]["notes"] is None
        assert listed["items"][0]["auto_invoice_mode"] is None
        assert listed["items"][0]["amount"] == "99.00"

        detail = await c.get(f"/api/v1/subscriptions/{own['id']}", headers=portal_headers)
        assert detail.status_code == 200
        assert detail.json()["notes"] is None
        for hidden in (draft, theirs):
            r = await c.get(f"/api/v1/subscriptions/{hidden['id']}", headers=portal_headers)
            assert r.status_code == 404, hidden["name"]
        # The module's own surfaces stay behind `:any`.
        assert (
            await c.get("/api/v1/subscriptions/summary", headers=portal_headers)
        ).status_code == 403
        assert (
            await c.get("/api/v1/subscriptions/templates", headers=portal_headers)
        ).status_code == 403
        # The staff read is unchanged by the scope arriving: the owner still reads the book.
        assert (await c.get("/api/v1/subscriptions/summary", headers=headers)).status_code == 200
        assert (await c.get(f"/api/v1/subscriptions/{draft['id']}", headers=headers)).json()[
            "notes"
        ] == "korting afgesproken met Piet"


# --------------------------------------------------------------------------- #
# the repeat rule is the agency's machinery
# --------------------------------------------------------------------------- #
async def test_portal_never_reads_a_tasks_repeat_rule(client_for) -> None:
    t, headers, portal_headers, _contact, mine, _other, _ = await _portal(client_for, "pd-repeat")
    async with client_for(t.host) as c:
        task = await _task(
            c,
            headers,
            title="Maandelijkse check",
            company_id=mine,
            recurrence={"freq": "monthly", "interval": 1, "on_day": 1},
        )
        assert task["recurrence"] is not None
        listed = (await c.get("/api/v1/tasks?open=1", headers=portal_headers)).json()
        assert listed["items"][0]["recurrence"] is None
        assert listed["items"][0]["recurrence_next_run"] is None
        detail = (await c.get(f"/api/v1/tasks/{task['id']}", headers=portal_headers)).json()
        assert detail["recurrence"] is None
        assert detail["recurrence_next_run"] is None
        # Nor the series a schedule-mode rule lays out — the same machinery, one level up.
        assert detail.get("series") is None
        # Staff read it as stored.
        assert (await c.get(f"/api/v1/tasks/{task['id']}", headers=headers)).json()[
            "recurrence"
        ] is not None


# --------------------------------------------------------------------------- #
# the contact person hears about the comment addressed to them
# --------------------------------------------------------------------------- #
async def _email_deliveries(t, user_id: uuid.UUID) -> list[str]:
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        rows = (
            await session.execute(
                select(NotificationEvent.event_type)
                .join(Notification, Notification.event_id == NotificationEvent.id)
                .join(NotificationDelivery, NotificationDelivery.notification_id == Notification.id)
                .where(
                    Notification.org_id == t.org.id,
                    Notification.user_id == user_id,
                    NotificationDelivery.channel == "email",
                )
            )
        ).all()
    return [row[0] for row in rows]


async def test_a_mentioned_contact_person_is_notified_through_their_login(client_for) -> None:
    t, headers, _portal_headers, contact, mine, _other, portal_user = await _portal(
        client_for, "pd-mention"
    )
    async with client_for(t.host) as c:
        task = await _task(c, headers, title="Teksten", company_id=mine)
        body = f"@[Piet](mention:contact:{contact['id']}) kun je hiernaar kijken?"
        r = await c.post(
            f"/api/v1/tasks/{task['id']}/comments", json={"body": body}, headers=headers
        )
        assert r.status_code == 201, r.text
        assert r.json()["mentioned_contact_ids"] == [contact["id"]]

    inbox = await _inbox(t, portal_user.id)
    assert [event_type for event_type, _ in inbox] == ["task.mentioned"]
    # Named once, told once: nothing rode along as "commented".
    assert "task.commented" not in [event_type for event_type, _ in inbox]
    # And it mails, without anybody having opened a preferences screen for them.
    assert await _email_deliveries(t, portal_user.id) == ["task.mentioned"]


async def test_the_assigned_contact_person_hears_a_comment_on_their_task(client_for) -> None:
    t, headers, portal_headers, contact, mine, _other, portal_user = await _portal(
        client_for, "pd-assigned"
    )
    async with client_for(t.host) as c:
        assigned = await _task(
            c, headers, title="Aanleveren", company_id=mine, assignee_contact_id=contact["id"]
        )
        unrelated = await _task(c, headers, title="Los", company_id=mine)
        for task in (assigned, unrelated):
            r = await c.post(
                f"/api/v1/tasks/{task['id']}/comments",
                json={"body": "Wanneer kunnen we dit verwachten?"},
                headers=headers,
            )
            assert r.status_code == 201, r.text

        inbox = await _inbox(t, portal_user.id)
        assert [(event_type, payload["title"]) for event_type, payload in inbox] == [
            ("task.commented", "Aanleveren")
        ]
        assert await _email_deliveries(t, portal_user.id) == ["task.commented"]

        # The client answering on their own task is the actor — never told about themselves —
        # and the staff conversation still hears it the ordinary way.
        r = await c.post(
            f"/api/v1/tasks/{assigned['id']}/comments",
            json={"body": "Morgen!"},
            headers=portal_headers,
        )
        assert r.status_code == 201, r.text
    assert len(await _inbox(t, portal_user.id)) == 1
    assert any(event_type == "task.commented" for event_type, _ in await _inbox(t, t.user.id))
