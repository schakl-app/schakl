"""The repeat rule you can read, pin to a date, and see the next occurrence of (#335).

Three subjects, and each of them was a way the old rule lied by omission:

* **anchors** — ``{freq, interval, mode}`` could not say "on the 1st", so a monthly task drifted
  onto whatever the deadline happened to be, with nothing on screen admitting it;
* **the preview** — the next date was derivable only by the API and exposed by nobody, so a rule
  could not be checked before it was stored;
* **the copy set** — ``spawn_next`` silently dropped three fields nobody had *decided* about.
  The parity sweep here is what makes the next one a build break instead.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.db import async_session_maker, set_current_org
from app.modules.tasks.models import Task
from app.modules.tasks.recurrence import (
    COPIED_FIELDS,
    NOT_COPIED_FIELDS,
    advance,
    next_due,
    snap,
    spawn_scheduled_recurrences,
)
from tests.conftest import FAR_FUTURE_DUE, auth_cookie, make_tenant, org_today

# --------------------------------------------------------------------------- #
# Anchors: the date math, in isolation
# --------------------------------------------------------------------------- #


def test_unanchored_advance_is_unchanged() -> None:
    """Every rule stored before #335 keeps its behaviour: no anchor, no change."""
    assert advance(date(2026, 1, 31), "monthly", 1) == date(2026, 2, 28)
    assert advance(date(2026, 1, 1), "weekly", 2) == date(2026, 1, 15)
    assert advance(date(2026, 3, 10), "yearly", 2) == date(2028, 3, 10)


def test_monthly_day_anchor_clamps_to_short_months() -> None:
    # "elke maand op dag 31" is the 28th in February and the 30th in April — a rule the user
    # can write, and the only reading of it that lands every month.
    assert advance(date(2026, 1, 15), "monthly", 1, on_day=31) == date(2026, 2, 28)
    assert advance(date(2024, 1, 15), "monthly", 1, on_day=31) == date(2024, 2, 29)
    assert advance(date(2026, 3, 15), "monthly", 1, on_day=31) == date(2026, 4, 30)
    assert advance(date(2026, 5, 20), "monthly", 1, on_day=1) == date(2026, 6, 1)
    assert advance(date(2026, 1, 5), "quarterly", 1, on_day=10) == date(2026, 4, 10)


def test_yearly_anchor_names_the_whole_date() -> None:
    # 29 February is the leap-day case `app/core/periods.py` already paid for once: an anchor of
    # 29/2 lands on the 28th in a common year rather than raising.
    assert advance(date(2026, 11, 20), "yearly", 1, on_day=15, on_month=3) == date(2027, 3, 15)
    assert advance(date(2024, 2, 29), "yearly", 1, on_day=29, on_month=2) == date(2025, 2, 28)
    assert advance(date(2027, 2, 28), "yearly", 1, on_day=29, on_month=2) == date(2028, 2, 29)


def test_weekly_weekday_anchor_never_steps_backwards() -> None:
    """The invariant ``next_due``'s loop rests on: a step is always strictly after its input."""
    monday = date(2026, 1, 5)  # a Monday
    assert advance(monday, "weekly", 1, on_weekday=0) == date(2026, 1, 12)
    # Anchored to Sunday: the snap moves forward inside the stepped week.
    assert advance(monday, "weekly", 1, on_weekday=6) == date(2026, 1, 18)
    # Anchored to Monday from a Sunday: the snap moves *back* six days and still lands later.
    sunday = date(2026, 1, 11)
    assert advance(sunday, "weekly", 1, on_weekday=0) == date(2026, 1, 12)
    for start_offset in range(7):
        for weekday in range(7):
            d = monday + timedelta(days=start_offset)
            assert advance(d, "weekly", 1, on_weekday=weekday) > d


def test_anchored_rule_with_no_due_date_may_still_land_this_period() -> None:
    """"Op dag 20", written on the 15th, means the 20th — not a month from now."""
    today = date(2026, 8, 15)
    rule = {"freq": "monthly", "interval": 1, "on_day": 20}
    assert next_due(None, rule, today=today) == date(2026, 8, 20)
    # …but the 10th has passed, so that one steps.
    assert next_due(None, {**rule, "on_day": 10}, today=today) == date(2026, 9, 10)
    # An unanchored rule is untouched: it still steps a whole interval from today.
    assert next_due(None, {"freq": "monthly", "interval": 1}, today=today) == date(2026, 9, 15)


def test_snap_stays_inside_its_own_period() -> None:
    assert snap(date(2026, 8, 15), "monthly", on_day=1) == date(2026, 8, 1)
    assert snap(date(2026, 8, 15), "daily") == date(2026, 8, 15)


# --------------------------------------------------------------------------- #
# Validation: an anchor a frequency cannot honour is refused, not ignored
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "rule",
    [
        {"freq": "weekly", "on_day": 15},
        {"freq": "monthly", "on_weekday": 2},
        {"freq": "daily", "on_day": 1},
        {"freq": "yearly", "on_day": 15},  # a day with no month is not a date
        {"freq": "yearly", "on_month": 3},
    ],
)
async def test_mismatched_anchor_is_refused(client_for, rule) -> None:
    t = await make_tenant(f"rec-anchor-{abs(hash(str(rule))) % 10000}")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        res = await c.post(
            "/api/v1/tasks",
            json={"due_date": FAR_FUTURE_DUE, "title": "Bad rule", "recurrence": rule},
            headers=headers,
        )
    assert res.status_code == 422


async def test_anchor_round_trips_and_drives_the_next_date(client_for) -> None:
    t = await make_tenant("rec-anchor-ok")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        task = (
            await c.post(
                "/api/v1/tasks",
                json={
                    "due_date": FAR_FUTURE_DUE,
                    "title": "Facturatie",
                    "recurrence": {
                        "freq": "monthly",
                        "interval": 1,
                        "mode": "schedule",
                        "on_day": 1,
                    },
                },
                headers=headers,
            )
        ).json()
    assert task["recurrence"]["on_day"] == 1
    # `recurrence_next_run` is on the read shape now, which is what lets a card say *when*.
    assert date.fromisoformat(task["recurrence_next_run"]).day == 1
    assert date.fromisoformat(task["recurrence_next_run"]) > org_today()


# --------------------------------------------------------------------------- #
# The preview
# --------------------------------------------------------------------------- #
async def test_preview_answers_with_dates_and_a_rhythm(client_for) -> None:
    t = await make_tenant("rec-preview")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        body = (
            await c.post(
                "/api/v1/tasks/recurrence/preview",
                json={
                    "recurrence": {"freq": "monthly", "interval": 1, "on_day": 1},
                    "due_date": None,
                },
                headers=headers,
            )
        ).json()
    assert date.fromisoformat(body["next_date"]).day == 1
    # Three dates, so a user can tell a monthly rule from a weekly one by reading them.
    assert len(body["following"]) == 2
    assert all(date.fromisoformat(d).day == 1 for d in body["following"])
    assert body["on_completion"] is True
    assert body["planned_start"] is None


async def test_preview_includes_the_planned_window(client_for) -> None:
    t = await make_tenant("rec-preview-plan")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        body = (
            await c.post(
                "/api/v1/tasks/recurrence/preview",
                json={
                    "recurrence": {
                        "freq": "weekly",
                        "interval": 1,
                        "mode": "schedule",
                        "on_weekday": 0,
                        "plan": {"start_time": "09:00:00", "duration_minutes": 90},
                    }
                },
                headers=headers,
            )
        ).json()
    assert date.fromisoformat(body["next_date"]).weekday() == 0
    assert body["on_completion"] is False
    assert body["planned_start"].startswith("09:00")
    assert body["planned_end"].startswith("10:30")


# --------------------------------------------------------------------------- #
# What repeats — the F4 class of bug, made structural
# --------------------------------------------------------------------------- #
def test_every_task_column_has_a_repeat_decision() -> None:
    """Adding a column to ``tasks`` without saying whether it repeats is a build break.

    This is the whole point of enumerating the copy set. ``visible_to_client``,
    ``assignee_contact_id`` and the task links were not *decided* against — they were added long
    after ``spawn_next`` was written and nobody was asked. A clone that is internal when its
    carrier was client-visible is a privacy-shaped surprise nobody would go looking for.
    """
    columns = {c.name for c in Task.__table__.columns}
    decided = COPIED_FIELDS | set(NOT_COPIED_FIELDS)
    assert columns - decided == set(), (
        "new tasks column(s) with no repeat decision — add them to COPIED_FIELDS or "
        "NOT_COPIED_FIELDS in app/modules/tasks/recurrence.py"
    )
    assert decided - columns == set(), "stale entry naming a column that no longer exists"
    assert not (COPIED_FIELDS & set(NOT_COPIED_FIELDS))


async def test_spawn_carries_visibility_contact_assignee_and_links(client_for) -> None:
    t = await make_tenant("rec-carries")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Klant BV"}, headers=headers)
        ).json()
        contact = (
            await c.post(
                "/api/v1/contacts",
                json={
                    "first_name": "Jan",
                    "last_name": "Klant",
                    "company_ids": [company["id"]],
                },
                headers=headers,
            )
        ).json()
        task = (
            await c.post(
                "/api/v1/tasks",
                json={
                    "title": "Maandrapport",
                    "company_id": company["id"],
                    "assignee_contact_id": contact["id"],
                    "visible_to_client": True,
                    "due_date": (org_today() - timedelta(days=1)).isoformat(),
                    "recurrence": {"freq": "monthly", "interval": 1, "mode": "after_completion"},
                },
                headers=headers,
            )
        ).json()
        assert task["visible_to_client"] is True
        await c.post(
            f"/api/v1/tasks/{task['id']}/links",
            json={"url": "https://example.com/briefing", "title": "Briefing"},
            headers=headers,
        )

        await c.patch(f"/api/v1/tasks/{task['id']}", json={"status": "done"}, headers=headers)
        listed = (await c.get("/api/v1/tasks", headers=headers)).json()["items"]
        clone_id = next(row["id"] for row in listed if row["id"] != task["id"])
        clone = (await c.get(f"/api/v1/tasks/{clone_id}", headers=headers)).json()

    assert clone["visible_to_client"] is True, "a client-visible job spawned an internal clone"
    assert clone["assignee_contact_id"] == contact["id"]
    assert [link["url"] for link in clone["links"]] == ["https://example.com/briefing"]


async def test_completion_records_the_hand_off_on_the_carrier(client_for) -> None:
    """The carrier used to say only "verplaatst naar Klaar" — the next occurrence existed and
    the screen that produced it was silent about it (#335 F5)."""
    t = await make_tenant("rec-handoff")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        task = (
            await c.post(
                "/api/v1/tasks",
                json={
                    "due_date": FAR_FUTURE_DUE,
                    "title": "Nieuwsbrief",
                    "recurrence": {"freq": "weekly", "interval": 1, "mode": "after_completion"},
                },
                headers=headers,
            )
        ).json()
        await c.patch(f"/api/v1/tasks/{task['id']}", json={"status": "done"}, headers=headers)
        carrier = (await c.get(f"/api/v1/tasks/{task['id']}", headers=headers)).json()

    handoff = next(a for a in carrier["activities"] if a["action"] == "recurrence_spawned_next")
    assert handoff["payload"]["next_task_id"]
    # Dated, so the trail line reads "volgende taak aangemaakt (13 sep)" without a second fetch.
    assert date.fromisoformat(handoff["payload"]["due_date"]) > org_today()


# --------------------------------------------------------------------------- #
# Herhaal ook de planning
# --------------------------------------------------------------------------- #
async def test_spawn_plans_the_occurrence_through_the_schedule_service(client_for) -> None:
    t = await make_tenant("rec-autoplan")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        task = (
            await c.post(
                "/api/v1/tasks",
                json={
                    "due_date": FAR_FUTURE_DUE,
                    "title": "Backup controleren",
                    "assignee_user_id": str(t.user.id),
                    "recurrence": {
                        "freq": "weekly",
                        "interval": 1,
                        "mode": "after_completion",
                        "plan": {"start_time": "09:00:00", "duration_minutes": 60},
                    },
                },
                headers=headers,
            )
        ).json()
        await c.patch(f"/api/v1/tasks/{task['id']}", json={"status": "done"}, headers=headers)
        listed = (await c.get("/api/v1/tasks", headers=headers)).json()["items"]
        clone_id = next(row["id"] for row in listed if row["id"] != task["id"])
        blocks = (
            await c.get(f"/api/v1/tasks/schedules?task_id={clone_id}", headers=headers)
        ).json()
        # The carrier keeps none: a plan is the occurrence's, and the clone is the occurrence now.
        carrier_blocks = (
            await c.get(f"/api/v1/tasks/schedules?task_id={task['id']}", headers=headers)
        ).json()

    assert len(blocks) == 1, "the rule promised a block and the occurrence arrived unplanned"
    assert blocks[0]["start"] == blocks[0]["end"]
    assert blocks[0]["user_id"] == str(t.user.id)
    assert carrier_blocks == []


async def test_cron_spawn_also_plans(client_for) -> None:
    """The nightly sweep must book blocks too, and it has nobody behind it.

    ``system_context``'s user is a placeholder that exists in no table, so a block naively
    stamped with it would violate ``task_schedules.created_by_user_id``'s FK — the failure would
    be a rolled-back org in a log nobody reads, and every scheduled occurrence would arrive
    unplanned. A NULL scheduler *is* the system, which is what the snapshot pair exists to say.
    """
    t = await make_tenant("rec-cron-plan")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        task = (
            await c.post(
                "/api/v1/tasks",
                json={
                    "due_date": FAR_FUTURE_DUE,
                    "title": "Weekrapport",
                    "assignee_user_id": str(t.user.id),
                    "recurrence": {
                        "freq": "weekly",
                        "interval": 1,
                        "mode": "schedule",
                        "plan": {"start_time": "08:30:00", "duration_minutes": 45},
                    },
                },
                headers=headers,
            )
        ).json()

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        carrier = await session.scalar(select(Task).where(Task.id == uuid.UUID(task["id"])))
        carrier.recurrence_next_run = org_today() - timedelta(days=1)
        await session.commit()

    spawned = await spawn_scheduled_recurrences({})
    assert spawned >= 52  # the whole year, one occurrence a week

    async with client_for(t.host) as c:
        items = (await c.get("/api/v1/tasks?limit=200", headers=headers)).json()["items"]
        clone_ids = [row["id"] for row in items if row["id"] != task["id"]]
        assert len(clone_ids) == spawned
        blocks = (
            await c.get(f"/api/v1/tasks/schedules?task_id={clone_ids[0]}", headers=headers)
        ).json()
    assert len(blocks) == 1
    assert blocks[0]["created_by_user_id"] is None
    assert blocks[0]["starts_at"].endswith(("Z", "+00:00"))


async def test_a_late_completion_never_books_a_block_in_the_past(client_for) -> None:
    """``next_due`` guarantees a future date, so the occurrence's block is future too."""
    t = await make_tenant("rec-plan-past")
    headers = await auth_cookie(t.user)
    long_ago = (org_today() - timedelta(days=400)).isoformat()
    async with client_for(t.host) as c:
        task = (
            await c.post(
                "/api/v1/tasks",
                json={
                    "title": "Vergeten klusje",
                    "assignee_user_id": str(t.user.id),
                    "due_date": long_ago,
                    "recurrence": {
                        "freq": "monthly",
                        "interval": 1,
                        "plan": {"start_time": "09:00:00", "duration_minutes": 60},
                    },
                },
                headers=headers,
            )
        ).json()
        await c.patch(f"/api/v1/tasks/{task['id']}", json={"status": "done"}, headers=headers)
        items = (await c.get("/api/v1/tasks", headers=headers)).json()["items"]
        clone_id = next(row["id"] for row in items if row["id"] != task["id"])
        blocks = (
            await c.get(f"/api/v1/tasks/schedules?task_id={clone_id}", headers=headers)
        ).json()
    assert len(blocks) == 1
    assert date.fromisoformat(blocks[0]["start"]) > org_today()


async def test_plan_for_someone_else_needs_schedule_write_any(client_for) -> None:
    """The stored decision asks what pressing Inplannen would ask (#335 phase 5).

    A generator that executes it later runs as the system, so a permission checked only at
    execution time is no permission at all — it has to be asked when the rule is written.
    """
    t = await make_tenant("rec-plan-perm")
    member = await make_tenant("rec-plan-perm-colleague", email="colleague@example.com")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        res = await c.post(
            "/api/v1/tasks",
            json={
                "due_date": FAR_FUTURE_DUE,
                "title": "Niet van mij",
                "recurrence": {
                    "freq": "weekly",
                    "interval": 1,
                    "plan": {
                        "user_id": str(member.user.id),
                        "start_time": "09:00:00",
                        "duration_minutes": 60,
                    },
                },
            },
            headers=headers,
        )
    # The owner holds `:any`, so the refusal is about membership, not permission: that user is
    # not in this org, and a stale id spent by a cron months later would just skip in silence.
    assert res.status_code == 422


# --------------------------------------------------------------------------- #
# Placements: a plan is several blocks, each on a day stated relative to the occurrence
# --------------------------------------------------------------------------- #
def test_nth_weekday_and_the_last_one() -> None:
    from app.modules.tasks.recurrence import nth_weekday

    # September 2026: Tuesdays are 1, 8, 15, 22, 29; Fridays 4, 11, 18, 25.
    assert nth_weekday(2026, 9, 1, 2) == date(2026, 9, 8)
    assert nth_weekday(2026, 9, 1, 1) == date(2026, 9, 1)
    assert nth_weekday(2026, 9, 4, -1) == date(2026, 9, 25)
    assert nth_weekday(2026, 9, 1, -1) == date(2026, 9, 29)


def test_monthly_nth_weekday_anchor_steps_and_snaps() -> None:
    """"Elke maand op de tweede dinsdag" — and the last Friday, which no day number can say."""
    assert advance(date(2026, 9, 3), "monthly", 1, on_weekday=1, on_week=2) == date(2026, 10, 13)
    assert advance(date(2026, 9, 3), "monthly", 1, on_weekday=4, on_week=-1) == date(2026, 10, 30)
    assert snap(date(2026, 9, 3), "monthly", on_weekday=1, on_week=2) == date(2026, 9, 8)
    # Yearly: the last Friday of November, whatever month the rule was written in.
    assert advance(date(2026, 3, 3), "yearly", 1, on_month=11, on_weekday=4, on_week=-1) == date(
        2027, 11, 26
    )
    rule = {"freq": "monthly", "on_weekday": 1, "on_week": 2}
    assert next_due(None, rule, today=date(2026, 9, 3)) == date(2026, 9, 8)


def test_place_block_resolves_every_placement() -> None:
    from app.modules.tasks.recurrence import place_block

    due = date(2026, 9, 25)  # a Friday
    assert place_block({"on": "due"}, due) == due
    assert place_block({"on": "offset", "days": -2}, due) == date(2026, 9, 23)
    assert place_block({"on": "offset", "days": 3}, due) == date(2026, 9, 28)
    # The Tuesday of the deadline's own week is *before* a Friday deadline.
    assert place_block({"on": "weekday", "weekday": 1}, due) == date(2026, 9, 22)
    assert place_block({"on": "weekday", "weekday": 1, "week": 1}, due) == date(2026, 9, 1)
    assert place_block({"on": "weekday", "weekday": 4, "week": -1}, due) == date(2026, 9, 25)
    assert place_block({"on": "day", "day": 31}, due) == date(2026, 9, 30)


def test_legacy_plan_reads_as_one_block_on_the_due_date() -> None:
    from app.modules.tasks.recurrence import plan_blocks, planned_blocks

    rec = {"freq": "weekly", "plan": {"start_time": "09:00:00", "duration_minutes": 60}}
    assert plan_blocks(rec) == [
        {
            "on": "due",
            "user_ids": None,
            "start_time": "09:00:00",
            "duration_minutes": 60,
            "note": None,
        }
    ]
    assert planned_blocks(rec, date(2026, 9, 25)) == [(date(2026, 9, 25), plan_blocks(rec)[0])]
    assert plan_blocks({"freq": "weekly"}) == []


async def test_preview_resolves_each_block_to_its_day(client_for) -> None:
    t = await make_tenant("rec-preview-blocks")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        res = await c.post(
            "/api/v1/tasks/recurrence/preview",
            json={
                "due_date": FAR_FUTURE_DUE,
                "recurrence": {
                    "freq": "monthly",
                    "interval": 1,
                    "mode": "schedule",
                    "on_day": 25,
                    "plan": {
                        "blocks": [
                            {"on": "due", "start_time": "14:00:00", "duration_minutes": 30},
                            {
                                "on": "offset",
                                "days": -3,
                                "start_time": "09:00:00",
                                "duration_minutes": 120,
                                "note": "concept",
                            },
                        ]
                    },
                },
            },
            headers=headers,
        )
    assert res.status_code == 200, res.text
    body = res.json()
    blocks = body["blocks"]
    assert [b["on"] for b in blocks] == ["offset", "due"], "calendar order, not typing order"
    assert blocks[1]["day"] == body["next_date"]
    assert (date.fromisoformat(body["next_date"]) - date.fromisoformat(blocks[0]["day"])).days == 3
    assert blocks[0]["end_time"].startswith("11:00")
    assert body["planned_start"].startswith("09:00")


async def test_a_plan_refuses_a_placement_that_is_not_whole(client_for) -> None:
    t = await make_tenant("rec-plan-shape")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        for plan_block, anchors in (
            ({"on": "offset", "start_time": "09:00:00", "duration_minutes": 30}, {}),
            ({"on": "weekday", "week": 2, "start_time": "09:00:00", "duration_minutes": 30}, {}),
            ({"on": "due", "day": 3, "start_time": "09:00:00", "duration_minutes": 30}, {}),
            ({"on": "due", "start_time": "09:00:00", "duration_minutes": 30}, {"on_week": 2}),
        ):
            res = await c.post(
                "/api/v1/tasks/recurrence/preview",
                json={
                    "due_date": FAR_FUTURE_DUE,
                    "recurrence": {
                        "freq": "weekly",
                        "plan": {"blocks": [plan_block]},
                        **anchors,
                    },
                },
                headers=headers,
            )
            assert res.status_code == 422, (plan_block, anchors, res.text)


async def test_spawn_books_every_block_for_the_roster_and_skips_the_past(client_for) -> None:
    """Two blocks, three calendars: the review on the deadline for everyone on the task, the
    draft two days earlier for one named colleague — and a placement that lands before today
    is left unbooked rather than booked in the past."""
    from tests.conftest import add_membership

    t = await make_tenant("rec-spawn-blocks")
    colleague = await make_tenant("rec-spawn-blocks-c", email="c@example.com")
    headers = await auth_cookie(t.user)
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        await add_membership(session, t.org.id, colleague.user.id, "member")
        await session.commit()
    due = org_today() + timedelta(days=10)
    async with client_for(t.host) as c:
        task = (
            await c.post(
                "/api/v1/tasks",
                json={
                    "due_date": due.isoformat(),
                    "title": "Nieuwsbrief",
                    "assignees": [
                        {"user_id": str(t.user.id), "is_primary": True},
                        {"user_id": str(colleague.user.id), "is_primary": False},
                    ],
                    "recurrence": {
                        "freq": "monthly",
                        "interval": 1,
                        "mode": "after_completion",
                        "plan": {
                            "blocks": [
                                {"on": "due", "start_time": "14:00:00", "duration_minutes": 30},
                                {
                                    "on": "offset",
                                    "days": -2,
                                    "user_ids": [str(colleague.user.id)],
                                    "start_time": "09:00:00",
                                    "duration_minutes": 60,
                                    "note": "concept",
                                },
                                # Two months before the due date is behind today for the very
                                # next occurrence, which is due ~40 days out: skipped, not booked.
                                {
                                    "on": "offset",
                                    "days": -60,
                                    "start_time": "09:00:00",
                                    "duration_minutes": 15,
                                },
                            ]
                        },
                    },
                },
                headers=headers,
            )
        ).json()
        await c.patch(f"/api/v1/tasks/{task['id']}", json={"status": "done"}, headers=headers)
        listed = (await c.get("/api/v1/tasks", headers=headers)).json()["items"]
        clone_id = next(row["id"] for row in listed if row["id"] != task["id"])
        clone = (await c.get(f"/api/v1/tasks/{clone_id}", headers=headers)).json()
        blocks = (
            await c.get(f"/api/v1/tasks/schedules?task_id={clone_id}", headers=headers)
        ).json()
    clone_due = date.fromisoformat(clone["due_date"])
    by_start = sorted(blocks, key=lambda b: b["starts_at"])
    assert [b["start"] for b in by_start] == [
        (clone_due - timedelta(days=2)).isoformat(),
        clone_due.isoformat(),
        clone_due.isoformat(),
    ]
    assert by_start[0]["user_id"] == str(colleague.user.id)
    assert by_start[0]["note"] == "concept"
    assert {b["user_id"] for b in by_start[1:]} == {str(t.user.id), str(colleague.user.id)}
