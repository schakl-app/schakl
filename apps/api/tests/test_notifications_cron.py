"""The reminder crons (issue #16).

Each per-org function takes an injectable "today", so these pin the *rules* — fires once, on
the right day, for the right person — instead of racing the wall clock. The ARQ entry points
themselves are covered structurally by ``test_tenancy_seams`` (every cron binds ``run_per_org``).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from app.db import async_session_maker, set_current_org
from app.modules.notifications.models import Notification, NotificationEvent
from app.modules.projects.budget_watch import watch_for_org
from app.modules.projects.models import Project, ProjectAssignee
from app.modules.tasks.models import Task
from app.modules.tasks.reminders import remind_for_org as remind_tasks
from app.modules.time.models import TimeEntry
from app.modules.time.reminders import previous_week_start
from app.modules.time.reminders import remind_for_org as remind_timesheets
from tests.conftest import Tenant, make_tenant
from tests.test_notifications_fanout import _events, _member, _set_pref

_TODAY = date(2026, 7, 10)  # a Friday


async def _inbox_events(tenant: Tenant, user_id: uuid.UUID) -> list[str]:
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        return list(
            (
                await session.execute(
                    select(NotificationEvent.event_type)
                    .join(Notification, Notification.event_id == NotificationEvent.id)
                    .where(
                        Notification.org_id == tenant.org.id,
                        Notification.user_id == user_id,
                    )
                )
            ).scalars()
        )


async def _inbox_payloads(tenant: Tenant, user_id: uuid.UUID) -> list[dict]:
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        return list(
            (
                await session.execute(
                    select(NotificationEvent.payload)
                    .join(Notification, Notification.event_id == NotificationEvent.id)
                    .where(
                        Notification.org_id == tenant.org.id,
                        Notification.user_id == user_id,
                    )
                )
            ).scalars()
        )


async def _add_task(tenant: Tenant, assignee: uuid.UUID, due: date, title="Ship") -> uuid.UUID:
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        task = Task(
            org_id=tenant.org.id, title=title, assignee_user_id=assignee, due_date=due
        )
        session.add(task)
        await session.commit()
        return task.id


async def _reschedule(tenant: Tenant, task_id: uuid.UUID, due: date) -> None:
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        task = await session.get(Task, task_id)
        task.due_date = due
        await session.commit()


async def _run_task_reminders(tenant: Tenant, today: date) -> int:
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        count = await remind_tasks(tenant.org, session, today=today)
        await session.commit()
        return count


# --------------------------------------------------------------------------- #
# tasks: due_soon / overdue
# --------------------------------------------------------------------------- #
async def test_overdue_fires_once_per_deadline_and_again_after_a_reschedule() -> None:
    t = await make_tenant("cron-overdue")
    member = await _member(t, "late@example.com")
    task_id = await _add_task(t, member.id, _TODAY - timedelta(days=2))

    await _run_task_reminders(t, _TODAY)
    # Tomorrow's tick re-announces the same still-late task; the dedup key swallows it.
    await _run_task_reminders(t, _TODAY + timedelta(days=1))
    assert len(await _events(t, "task.overdue")) == 1
    assert await _inbox_events(t, member.id) == ["task.overdue"]

    # Moving the deadline is a new deadline: it may go late again.
    await _reschedule(t, task_id, _TODAY - timedelta(days=1))
    await _run_task_reminders(t, _TODAY + timedelta(days=2))
    assert await _inbox_events(t, member.id) == ["task.overdue", "task.overdue"]


async def test_due_soon_fires_only_on_the_default_threshold_day() -> None:
    t = await make_tenant("cron-duesoon")
    member = await _member(t, "soon@example.com")
    await _add_task(t, member.id, _TODAY + timedelta(days=3))  # exactly the 3-day default

    # Two days earlier it is not yet "soon".
    await _run_task_reminders(t, _TODAY - timedelta(days=2))
    assert await _events(t, "task.due_soon") == []

    await _run_task_reminders(t, _TODAY)
    assert await _inbox_events(t, member.id) == ["task.due_soon"]

    payload = (await _events(t, "task.due_soon"))[0].payload
    assert payload["days_left"] == 3
    assert payload["due_date"] == (_TODAY + timedelta(days=3)).isoformat()


async def test_due_soon_respects_a_personal_threshold() -> None:
    """The cron asks notifications what 'soon' means to *this* person."""
    t = await make_tenant("cron-duesoon-pref")
    eager = await _member(t, "eager@example.com")
    await _set_pref(t, eager.id, event_type=None, channel="in_app", due_soon_days=7)
    await _add_task(t, eager.id, _TODAY + timedelta(days=7))

    # The org default (3 days) would say nothing today; their own setting says otherwise.
    await _run_task_reminders(t, _TODAY)
    assert await _inbox_events(t, eager.id) == ["task.due_soon"]


async def test_a_finished_or_unassigned_task_reminds_nobody() -> None:
    t = await make_tenant("cron-quiet")
    member = await _member(t, "done@example.com")

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        session.add(
            Task(
                org_id=t.org.id, title="Finished", assignee_user_id=member.id,
                due_date=_TODAY - timedelta(days=5), status="done",
            )
        )
        session.add(
            Task(org_id=t.org.id, title="Nobody's", due_date=_TODAY - timedelta(days=5))
        )
        await session.commit()

    assert await _run_task_reminders(t, _TODAY) == 0, "no candidate is even announced"
    assert await _events(t) == []


# --------------------------------------------------------------------------- #
# projects: budget threshold
# --------------------------------------------------------------------------- #
async def _add_project(tenant: Tenant, assignee: uuid.UUID, budget_hours: float) -> uuid.UUID:
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        project = Project(
            org_id=tenant.org.id, name="Rebuild", status="active", budget_hours=budget_hours
        )
        session.add(project)
        await session.flush()
        session.add(
            ProjectAssignee(
                org_id=tenant.org.id, project_id=project.id, user_id=assignee, is_primary=True
            )
        )
        await session.commit()
        return project.id


async def _log_minutes(tenant: Tenant, user_id, project_id, minutes: int) -> None:
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        started = datetime.now(UTC) - timedelta(hours=2)
        session.add(
            TimeEntry(
                org_id=tenant.org.id,
                user_id=user_id,
                project_id=project_id,
                started_at=started,
                ended_at=started + timedelta(minutes=minutes),
                minutes=minutes,
            )
        )
        await session.commit()


async def _run_budget_watch(tenant: Tenant) -> int:
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        count = await watch_for_org(tenant.org, session)
        await session.commit()
        return count


async def test_budget_warns_at_each_threshold_once() -> None:
    t = await make_tenant("cron-budget")
    member = await _member(t, "burner@example.com")
    project_id = await _add_project(t, member.id, budget_hours=10)

    # 40% — nothing to say yet.
    await _log_minutes(t, member.id, project_id, 240)
    await _run_budget_watch(t)
    assert await _events(t) == []

    # 80% — crosses 75. A second tick re-announces it and the dedup key swallows the repeat.
    await _log_minutes(t, member.id, project_id, 240)
    await _run_budget_watch(t)
    await _run_budget_watch(t)
    assert len(await _events(t)) == 1, "the same threshold must not warn twice"

    # 110% — crosses 100; 75 already spoke, so only the new threshold lands.
    await _log_minutes(t, member.id, project_id, 180)
    await _run_budget_watch(t)

    events = await _events(t)
    assert sorted(e.payload["threshold"] for e in events) == [75, 100]
    assert sorted(e.payload["percent"] for e in events) == [80, 110]

    # Both are daily-digest, so the 100% event collapsed onto the still-pending 75% row:
    # one budget line about this project, carrying the newest number.
    inbox = await _inbox_payloads(t, member.id)
    assert len(inbox) == 1
    assert inbox[0]["threshold"] == 100 and inbox[0]["percent"] == 110


async def test_a_project_without_a_budget_or_a_roster_is_ignored() -> None:
    t = await make_tenant("cron-budget-quiet")
    member = await _member(t, "nobody@example.com")

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        # Budgeted, but nobody is on it.
        orphan = Project(org_id=t.org.id, name="Orphan", status="active", budget_hours=1)
        # Staffed, but unbudgeted — a zero budget is "no budget", not "instantly over".
        session.add(orphan)
        unbudgeted = Project(org_id=t.org.id, name="Free", status="active", budget_hours=None)
        zero = Project(org_id=t.org.id, name="Zero", status="active", budget_hours=0)
        session.add_all([unbudgeted, zero])
        await session.flush()
        for project in (unbudgeted, zero):
            session.add(
                ProjectAssignee(
                    org_id=t.org.id, project_id=project.id, user_id=member.id, is_primary=True
                )
            )
        await session.commit()
        orphan_id, zero_id = orphan.id, zero.id

    await _log_minutes(t, member.id, orphan_id, 600)
    await _log_minutes(t, member.id, zero_id, 600)
    assert await _run_budget_watch(t) == 0, "no candidate is even announced"
    assert await _events(t) == []


# --------------------------------------------------------------------------- #
# time: timesheet reminder
# --------------------------------------------------------------------------- #
async def _run_timesheet_reminders(tenant: Tenant, week_start: date) -> int:
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        count = await remind_timesheets(tenant.org, session, week_start=week_start)
        await session.commit()
        return count


def test_previous_week_start_is_the_monday_before() -> None:
    assert previous_week_start(date(2026, 7, 10)) == date(2026, 6, 29)  # Friday → prev Monday
    assert previous_week_start(date(2026, 7, 6)) == date(2026, 6, 29)  # Monday → prev Monday


async def test_only_an_empty_timesheet_is_nudged_and_only_once() -> None:
    t = await make_tenant("cron-timesheet")
    diligent = await _member(t, "diligent@example.com")
    forgetful = await _member(t, "forgetful@example.com")
    week_start = date(2026, 6, 29)

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        started = datetime(2026, 6, 30, 9, 0, tzinfo=UTC)
        session.add(
            TimeEntry(
                org_id=t.org.id, user_id=diligent.id, started_at=started,
                ended_at=started + timedelta(minutes=60), minutes=60,
            )
        )
        await session.commit()

    # The owner logged nothing either, so they are nudged too — clients never are.
    assert await _run_timesheet_reminders(t, week_start) == 2
    assert await _inbox_events(t, forgetful.id) == ["time.timesheet_reminder"]
    assert await _inbox_events(t, diligent.id) == []

    # A re-run (worker restart, retry) must not nag twice for the same week.
    await _run_timesheet_reminders(t, week_start)
    assert await _inbox_events(t, forgetful.id) == ["time.timesheet_reminder"]
    assert len(await _events(t, "time.timesheet_reminder")) == 2  # owner + forgetful, once each

    payload = (await _events(t, "time.timesheet_reminder"))[0].payload
    assert payload["week_start"] == week_start.isoformat()


async def test_the_reminder_crons_are_registered_and_bind_tenant_context() -> None:
    """Belt-and-braces alongside the structural seam test: the jobs are actually wired."""
    import inspect

    from app.registry import registry

    jobs = {
        job.coroutine.__name__: job
        for module in registry.all()
        for job in module.cron_jobs
    }
    for name in ("send_task_reminders", "watch_project_budgets", "send_timesheet_reminders"):
        assert name in jobs, f"{name} is not contributed by any module"
        assert "run_per_org" in inspect.getsource(jobs[name].coroutine)
