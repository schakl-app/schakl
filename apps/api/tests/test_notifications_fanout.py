"""Fan-out semantics (issue #16).

These drive the event bus directly rather than through a module's REST endpoint: the rules
under test (who is a recipient, when do they see it, what is worth recording) belong to the
notifications subscriber, and every emitting module inherits them. The emit *sites* are
covered by each module's own tests.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, time, timedelta

from sqlalchemy import func, select

from app.core.auth.models import User
from app.core.events import emit
from app.core.roles import Role
from app.core.tenancy import RequestContext
from app.db import async_session_maker, set_current_org
from app.modules.notifications.defaults import ResolvedPref
from app.modules.notifications.models import (
    Notification,
    NotificationEvent,
    NotificationPreference,
    NotificationWatcher,
)
from app.modules.notifications.prefs import compute_visible_at
from tests.conftest import Tenant, add_membership, make_tenant


@asynccontextmanager
async def _ctx(tenant: Tenant, user: User, role: Role = Role.OWNER):
    """A RequestContext bound to the tenant, the way ``require_context`` builds one."""
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        yield RequestContext(user=user, org=tenant.org, role=role, session=session)
        await session.commit()


async def _emit(tenant: Tenant, actor: User, event: str, payload: dict) -> None:
    async with _ctx(tenant, actor) as ctx:
        await emit(event, ctx, payload)


async def _member(tenant: Tenant, email: str) -> User:
    """A second person in the org — the one who receives what the actor does."""
    async with async_session_maker() as session:
        user = User(
            id=uuid.uuid4(), email=email, hashed_password="", is_active=True, is_verified=True
        )
        session.add(user)
        await session.flush()
        await set_current_org(session, tenant.org.id)
        await add_membership(session, tenant.org.id, user.id, Role.MEMBER.value)
        await session.commit()
        return User(id=user.id, email=email, hashed_password="", is_active=True)


async def _notifications(tenant: Tenant, user_id: uuid.UUID) -> list[Notification]:
    """Read the inbox rows directly — digest rows are not visible yet, so the API hides them."""
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        return list(
            (
                await session.execute(
                    select(Notification).where(
                        Notification.org_id == tenant.org.id,
                        Notification.user_id == user_id,
                    )
                )
            ).scalars()
        )


async def _events(tenant: Tenant, event_type: str | None = None) -> list[NotificationEvent]:
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        stmt = select(NotificationEvent).where(NotificationEvent.org_id == tenant.org.id)
        if event_type is not None:
            stmt = stmt.where(NotificationEvent.event_type == event_type)
        return list((await session.execute(stmt)).scalars())


async def _add_watcher(tenant: Tenant, user_id: uuid.UUID, entity_id: uuid.UUID, muted: bool):
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        session.add(
            NotificationWatcher(
                org_id=tenant.org.id,
                user_id=user_id,
                entity_type="task",
                entity_id=entity_id,
                muted=muted,
            )
        )
        await session.commit()


async def _set_pref(tenant: Tenant, user_id: uuid.UUID | None, **values) -> None:
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        session.add(NotificationPreference(org_id=tenant.org.id, user_id=user_id, **values))
        await session.commit()


# --------------------------------------------------------------------------- #
# Who gets notified
# --------------------------------------------------------------------------- #
async def test_actor_is_never_notified_about_their_own_action() -> None:
    t = await make_tenant("notif-actor")
    task_id = uuid.uuid4()

    # The owner assigns the task to themselves: nothing to tell them.
    await _emit(t, t.user, "task.assigned", {"task_id": task_id, "_recipients": [t.user.id]})
    assert await _notifications(t, t.user.id) == []
    # The action still happened, so the activity feed records it.
    assert len(await _events(t, "task.assigned")) == 1


async def test_hinted_recipient_is_notified() -> None:
    t = await make_tenant("notif-hinted")
    other = await _member(t, "other@example.com")
    task_id = uuid.uuid4()

    await _emit(t, t.user, "task.assigned", {"task_id": task_id, "_recipients": [other.id]})
    assert len(await _notifications(t, other.id)) == 1
    assert await _notifications(t, t.user.id) == []


async def test_watcher_is_added_and_mute_wins_over_a_hint() -> None:
    t = await make_tenant("notif-watch")
    watcher = await _member(t, "watcher@example.com")
    muted = await _member(t, "muted@example.com")
    task_id = uuid.uuid4()

    await _add_watcher(t, watcher.id, task_id, muted=False)
    await _add_watcher(t, muted.id, task_id, muted=True)

    # ``muted`` is explicitly hinted and still hears nothing; ``watcher`` never was and does.
    await _emit(
        t, t.user, "task.status_changed",
        {"task_id": task_id, "_recipients": [muted.id], "from": "open", "to": "done"},
    )
    assert len(await _notifications(t, watcher.id)) == 1
    assert await _notifications(t, muted.id) == []


async def test_commenting_auto_watches_so_the_thread_keeps_reaching_you() -> None:
    t = await make_tenant("notif-autowatch")
    commenter = await _member(t, "commenter@example.com")
    task_id = uuid.uuid4()

    # The commenter is the actor here, so this event notifies them about nothing…
    await _emit(t, commenter, "task.commented", {"task_id": task_id, "excerpt": "hi"})
    assert await _notifications(t, commenter.id) == []

    # …but it made them a watcher, so the next event on the task reaches them.
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        watchers = list(
            (
                await session.execute(
                    select(NotificationWatcher).where(
                        NotificationWatcher.org_id == t.org.id,
                        NotificationWatcher.user_id == commenter.id,
                    )
                )
            ).scalars()
        )
    assert len(watchers) == 1 and watchers[0].muted is False

    await _emit(
        t, t.user, "task.status_changed",
        {"task_id": task_id, "from": "open", "to": "done"},
    )
    assert len(await _notifications(t, commenter.id)) == 1


async def test_a_disabled_preference_drops_the_recipient_but_keeps_the_history() -> None:
    t = await make_tenant("notif-disabled")
    other = await _member(t, "quiet@example.com")
    task_id = uuid.uuid4()
    await _set_pref(
        t, other.id, event_type="task.assigned", channel="in_app", enabled=False,
        delay_minutes=0, digest="immediate",
    )

    await _emit(t, t.user, "task.assigned", {"task_id": task_id, "_recipients": [other.id]})
    assert await _notifications(t, other.id) == []
    assert len(await _events(t, "task.assigned")) == 1  # the feed still shows it happened


# --------------------------------------------------------------------------- #
# What is worth recording
# --------------------------------------------------------------------------- #
async def test_a_reminder_nobody_wants_is_not_history() -> None:
    t = await make_tenant("notif-system-empty")
    task_id = uuid.uuid4()

    # System (cron) event, no recipients → no row at all.
    await _emit(t, t.user, "task.overdue", {"task_id": task_id, "_recipients": []})
    assert await _events(t, "task.overdue") == []

    # A person's action with no recipients is still history.
    await _emit(
        t, t.user, "task.status_changed",
        {"task_id": task_id, "_recipients": [], "from": "open", "to": "done"},
    )
    assert len(await _events(t, "task.status_changed")) == 1


async def test_dedup_key_makes_a_repeated_cron_emit_idempotent() -> None:
    t = await make_tenant("notif-dedup")
    other = await _member(t, "assignee@example.com")
    task_id = uuid.uuid4()
    payload = {
        "task_id": task_id,
        "_recipients": [other.id],
        "_dedup_key": f"task.overdue:{task_id}",
    }

    await _emit(t, t.user, "task.overdue", dict(payload))
    await _emit(t, t.user, "task.overdue", dict(payload))  # tomorrow's tick

    assert len(await _events(t, "task.overdue")) == 1
    assert len(await _notifications(t, other.id)) == 1


async def test_the_payload_reaches_other_subscribers_unmutated() -> None:
    """The reserved keys are routing, not content — popping them must not corrupt the dict
    the *next* subscriber (the tasks module's company-status automation) reads."""
    t = await make_tenant("notif-payload")
    company_id = uuid.uuid4()
    payload = {"company_id": company_id, "status": "active", "_recipients": [t.user.id]}
    await _emit(t, t.user, "company.status_changed", payload)
    assert payload["_recipients"] == [t.user.id]
    assert payload["company_id"] == company_id


# --------------------------------------------------------------------------- #
# When they see it
# --------------------------------------------------------------------------- #
async def test_due_soon_fires_only_on_the_recipients_threshold_day() -> None:
    t = await make_tenant("notif-duesoon")
    other = await _member(t, "duesoon@example.com")
    far, near = uuid.uuid4(), uuid.uuid4()

    # Default threshold is 3 days: five days out is not "soon" for this person.
    await _emit(
        t, t.user, "task.due_soon",
        {"task_id": far, "_recipients": [other.id], "days_left": 5, "_dedup_key": f"a:{far}"},
    )
    assert await _events(t, "task.due_soon") == []

    await _emit(
        t, t.user, "task.due_soon",
        {"task_id": near, "_recipients": [other.id], "days_left": 3, "_dedup_key": f"b:{near}"},
    )
    assert len(await _notifications(t, other.id)) == 1


async def test_a_burst_collapses_into_the_pending_row() -> None:
    t = await make_tenant("notif-burst")
    other = await _member(t, "burst@example.com")
    task_id = uuid.uuid4()
    # Hold status changes for an hour, so a flurry of edits lands as one line.
    await _set_pref(
        t, other.id, event_type="task.status_changed", channel="in_app", enabled=True,
        delay_minutes=60, digest="immediate",
    )

    await _emit(
        t, t.user, "task.status_changed",
        {"task_id": task_id, "_recipients": [other.id], "from": "open", "to": "in_progress"},
    )
    await _emit(
        t, t.user, "task.status_changed",
        {"task_id": task_id, "_recipients": [other.id], "from": "in_progress", "to": "done"},
    )

    events = await _events(t, "task.status_changed")
    inbox = await _notifications(t, other.id)
    assert len(events) == 2, "both changes are recorded in the feed"
    assert len(inbox) == 1, "but the recipient sees one pending line, not two"
    newest = max(events, key=lambda e: e.created_at)
    assert inbox[0].event_id == newest.id, "collapsed onto the newest content"
    assert inbox[0].visible_at > datetime.now(UTC), "still pending — the delay is honoured"


def test_daily_digest_lands_at_the_next_local_0800_across_dst() -> None:
    pref = ResolvedPref(
        enabled=True, delay_minutes=0, digest="daily",
        digest_time=time(8, 0), digest_weekday=None,
    )
    # Summer (CEST, UTC+2): 07:00 local is 05:00Z → next 08:00 local is 06:00Z.
    summer = compute_visible_at(pref, datetime(2026, 7, 10, 5, 0, tzinfo=UTC))
    assert summer == datetime(2026, 7, 10, 6, 0, tzinfo=UTC)
    # Winter (CET, UTC+1): the same wall clock is one hour earlier in UTC.
    winter = compute_visible_at(pref, datetime(2026, 1, 15, 5, 0, tzinfo=UTC))
    assert winter == datetime(2026, 1, 15, 7, 0, tzinfo=UTC)
    # Past today's slot → tomorrow, not an hour that already went by.
    tomorrow = compute_visible_at(pref, datetime(2026, 7, 10, 9, 0, tzinfo=UTC))
    assert tomorrow == datetime(2026, 7, 11, 6, 0, tzinfo=UTC)


def test_immediate_honours_a_delay_and_digests_ignore_it() -> None:
    now = datetime(2026, 7, 10, 5, 0, tzinfo=UTC)
    immediate = ResolvedPref(
        enabled=True, delay_minutes=15, digest="immediate",
        digest_time=None, digest_weekday=None,
    )
    assert compute_visible_at(immediate, now) == now + timedelta(minutes=15)

    weekly = ResolvedPref(
        enabled=True, delay_minutes=15, digest="weekly",
        digest_time=time(8, 0), digest_weekday=0,  # Monday
    )
    # 2026-07-10 is a Friday; the next Monday 08:00 CEST is 2026-07-13 06:00Z.
    assert compute_visible_at(weekly, now) == datetime(2026, 7, 13, 6, 0, tzinfo=UTC)


async def test_fan_out_is_bounded_not_n_plus_one(count_queries) -> None:
    """Ten recipients must not cost ten preference lookups (docs/PERFORMANCE.md)."""
    t = await make_tenant("notif-nplus1")
    people = [await _member(t, f"p{i}@example.com") for i in range(10)]
    task_id = uuid.uuid4()

    with count_queries() as counter:
        await _emit(
            t, t.user, "task.assigned",
            {"task_id": task_id, "_recipients": [p.id for p in people]},
        )
    assert len(counter.matching("notification_preferences")) == 1
    assert len(counter.matching("notification_watchers")) == 1
    assert len(counter.matching("FROM memberships")) == 1
    for person in people:
        assert len(await _notifications(t, person.id)) == 1


# --------------------------------------------------------------------------- #
# Tenant isolation (Golden Rule 1)
# --------------------------------------------------------------------------- #
async def test_a_recipient_hint_cannot_notify_a_non_member() -> None:
    """The hint is data from another module, not an authorization decision."""
    a = await make_tenant("notif-org-a")
    b = await make_tenant("notif-org-b")
    task_id = uuid.uuid4()

    # Org A emits, hinting org B's owner — who is not a member of A.
    await _emit(a, a.user, "task.assigned", {"task_id": task_id, "_recipients": [b.user.id]})

    # A recorded the action, but nobody was told: the outsider was filtered at fan-out.
    assert len(await _events(a, "task.assigned")) == 1
    assert await _notifications(a, b.user.id) == []

    async with async_session_maker() as session:
        await set_current_org(session, b.org.id)
        # And B's tenant sees nothing at all: RLS + the explicit org_id filter both hold.
        assert (
            await session.scalar(select(func.count()).select_from(NotificationEvent))
        ) == 0
        assert (await session.scalar(select(func.count()).select_from(Notification))) == 0
