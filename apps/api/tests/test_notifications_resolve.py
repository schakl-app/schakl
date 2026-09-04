"""#170: acting on the underlying item auto-marks its pending-action notifications read.

A "pending your approval" / "waiting on your review" notification stops asking the moment
the thing it is about gets resolved — for *every* recipient, not only the person who acted.
Outcome notifications (``leave.approved`` to the requester) are new information and stay
unread.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.auth.models import User
from app.core.events import SystemContext
from app.db import async_session_maker, set_current_org
from app.modules.interactions import system as interactions_system
from app.modules.notifications.models import Notification, NotificationEvent
from app.modules.notifications.service import NotificationService
from app.modules.tasks.reminders import remind_for_org as remind_tasks
from tests.conftest import auth_cookie, default_company, leave_workday, make_tenant

_NOW = datetime(2026, 7, 10, 14, 30, tzinfo=UTC)


async def _invite(client, headers, email: str, role: str) -> User:
    res = await client.post(
        "/api/v1/members/invite",
        json={"email": email, "full_name": email.split("@")[0], "role": role},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return User(
        id=uuid.UUID(res.json()["user_id"]), email=email, hashed_password="", is_active=True
    )


async def _inbox(tenant, user_id: uuid.UUID, event_type: str) -> list[Notification]:
    """One user's notifications of one event type, straight from the table."""
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        rows = (
            await session.execute(
                select(Notification)
                .join(NotificationEvent, NotificationEvent.id == Notification.event_id)
                .where(
                    Notification.org_id == tenant.org.id,
                    Notification.user_id == user_id,
                    NotificationEvent.event_type == event_type,
                )
            )
        ).scalars()
        return list(rows)


async def test_deciding_leave_resolves_every_approvers_notification(client_for) -> None:
    t = await make_tenant("resolve-leave")
    owner_headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        second = await _invite(c, owner_headers, "tweede-approver@example.com", "admin")
        member = await _invite(c, owner_headers, "employee@example.com", "member")
        member_headers = await auth_cookie(member)

        types = (await c.get("/api/v1/leave/types", headers=owner_headers)).json()
        statutory = next(lt for lt in types if lt["key"] == "vacation_statutory")
        day = leave_workday(0).isoformat()
        res = await c.post(
            "/api/v1/leave/requests",
            json={"leave_type_id": statutory["id"], "start_date": day, "end_date": day},
            headers=member_headers,
        )
        assert res.status_code == 201, res.text
        request_id = res.json()["id"]

        # Both approvers were asked; neither has opened it.
        for approver in (t.user, second):
            rows = await _inbox(t, approver.id, "leave.requested")
            assert len(rows) == 1 and rows[0].read_at is None

        res = await c.post(
            f"/api/v1/leave/requests/{request_id}/decide",
            json={"approved": True},
            headers=owner_headers,
        )
        assert res.status_code == 200

        # The decision retires the ask for *every* approver, not only the decider (#170)...
        for approver in (t.user, second):
            rows = await _inbox(t, approver.id, "leave.requested")
            assert len(rows) == 1 and rows[0].read_at is not None
        # ...while the requester's outcome notification is new and stays unread.
        outcome = await _inbox(t, member.id, "leave.approved")
        assert len(outcome) == 1 and outcome[0].read_at is None


async def test_finishing_a_task_retires_its_overdue_reminder(client_for) -> None:
    """A reminder describes a state, so it stops being information when the state stops.

    ``task.overdue`` deduplicates on the deadline it first fired for, so nothing else was ever
    going to clear it: an inbox kept "X is te laat" for the rest of the year about a task
    finished the same afternoon. Finishing it now resolves the reminder for **every** recipient,
    exactly as a decided leave request already did.
    """
    t = await make_tenant("resolve-task")
    owner_headers = await auth_cookie(t.user)

    async with client_for(t.host) as c:
        member = await _invite(c, owner_headers, "assignee@example.com", "member")
        res = await c.post(
            "/api/v1/tasks",
            json={
                "company_id": await default_company(c, owner_headers),
                "title": "Offerte nabellen",
                "assignee_user_id": str(member.id),
                "due_date": (_NOW.date() - timedelta(days=3)).isoformat(),
            },
            headers=owner_headers,
        )
        assert res.status_code == 201, res.text
        task_id = res.json()["id"]

        # The nightly cron announces it as late; the assignee has not opened it.
        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            await remind_tasks(t.org, session, today=_NOW.date())
            await session.commit()

        rows = await _inbox(t, member.id, "task.overdue")
        assert len(rows) == 1 and rows[0].read_at is None

        # A status change that is *not* finishing leaves it exactly where it was: half the
        # point of the predicate is that a task moving across the board still nags.
        res = await c.patch(
            f"/api/v1/tasks/{task_id}", json={"status": "in_progress"}, headers=owner_headers
        )
        assert res.status_code == 200, res.text
        rows = await _inbox(t, member.id, "task.overdue")
        assert len(rows) == 1 and rows[0].read_at is None

        res = await c.patch(
            f"/api/v1/tasks/{task_id}", json={"status": "done"}, headers=owner_headers
        )
        assert res.status_code == 200, res.text

    rows = await _inbox(t, member.id, "task.overdue")
    assert len(rows) == 1 and rows[0].read_at is not None
    # ...while "de taak is afgerond" is news and stays unread.
    told = await _inbox(t, member.id, "task.status_changed")
    assert told and all(row.read_at is None for row in told)


async def test_reviewing_email_resolves_pending_notification(client_for) -> None:
    t = await make_tenant("resolve-email")
    headers = await auth_cookie(t.user)

    # Seed a pending gmail row + its notification the way the poller does (#146).
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        ctx = SystemContext(org=t.org, session=session)
        row = await interactions_system.record_email(
            ctx,
            owner_user_id=t.user.id,
            owner_name="Mailbox Owner",
            occurred_at=_NOW,
            subject="Offerte akkoord",
            snippet="Bij deze akkoord...",
            direction="inbound",
            participants=[{"email": "klant@client.nl", "role": "from"}],
            gmail_message_id="msg-resolve-1",
            gmail_thread_id="thr-resolve-1",
            rfc822_message_id="<msg-resolve-1@mail.example>",
            deep_link="https://mail.google.com/mail/u/0/#all/abc",
            pending=True,
            mappings={},
        )
        await NotificationService(ctx).ingest(
            "interactions.email_pending",
            "interaction",
            row.id,
            {"subject": "Offerte akkoord", "_recipients": [t.user.id]},
        )
        await session.commit()
        interaction_id = str(row.id)

    rows = await _inbox(t, t.user.id, "interactions.email_pending")
    assert len(rows) == 1 and rows[0].read_at is None

    async with client_for(t.host) as c:
        res = await c.post(f"/api/v1/interactions/{interaction_id}/approve", headers=headers)
        assert res.status_code == 200, res.text

    rows = await _inbox(t, t.user.id, "interactions.email_pending")
    assert len(rows) == 1 and rows[0].read_at is not None
