"""Several employees on one task — one primary, the rest assigned (#375).

The third caller of ``app/core/assignees.py``, after clients and projects (#12,
``tests/test_assignees.py``), so the roster mechanics themselves are already covered there. What
is tested here is what is *specific to tasks* and what would break silently:

* the ``assignee_user_id`` mirror, which every unconverted reader still depends on;
* the hand-off rule — a bare ``assignee_user_id`` PATCH replaces the roster on a task, where the
  same field merely moves the star on a client;
* ``:own`` meaning **any** assignee, or a second assignee gets a task they can see, are expected
  to work, and cannot touch;
* the person filter and My Day matching any assignee (docs/UX.md);
* exclusivity with a client-contact assignee (#273) holding against the whole roster, not the star;
* a recurring task repeating onto everyone;
* a write path that does not go through the service (a template) leaving a roster behind it;
* tenant isolation (Golden Rule 1).
"""

from __future__ import annotations

import uuid

from pwdlib import PasswordHash

from app.core.auth.models import User
from app.db import async_session_maker, set_current_org
from tests.conftest import add_membership, auth_cookie, make_tenant

_ph = PasswordHash.recommended()


async def _add_member(org_id: uuid.UUID, email: str, role: str = "member") -> User:
    async with async_session_maker() as session:
        user = User(
            id=uuid.uuid4(),
            email=email,
            hashed_password=_ph.hash("secret1234"),
            is_active=True,
            is_verified=True,
        )
        session.add(user)
        await session.flush()
        await set_current_org(session, org_id)
        await add_membership(session, org_id, user.id, role)
        await session.commit()
        return User(id=user.id, email=user.email, hashed_password="", is_active=True)


def _ids(assignees: list[dict]) -> set[str]:
    return {a["user_id"] for a in assignees}


def _primary(assignees: list[dict]) -> str | None:
    return next((a["user_id"] for a in assignees if a["is_primary"]), None)


async def test_roster_round_trip_and_mirrors_the_assignee_column(client_for) -> None:
    t = await make_tenant("task-asg-create")
    owner = str(t.user.id)
    other = str((await _add_member(t.org.id, "other@task-asg-create.test")).id)
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)

        created = await c.post(
            "/api/v1/tasks",
            json={
                "title": "Samen doen",
                "assignees": [
                    {"user_id": other, "is_primary": False},
                    {"user_id": owner, "is_primary": True},
                ],
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert _ids(body["assignees"]) == {owner, other}
        # The star comes back first, and the column mirrors it — that is what every reader this
        # release did not convert (reminders, scheduling, impex, automation) still reads.
        assert body["assignees"][0]["user_id"] == owner
        assert body["assignee_user_id"] == owner

        detail = await c.get(f"/api/v1/tasks/{body['id']}", headers=headers)
        assert _ids(detail.json()["assignees"]) == {owner, other}

        listed = await c.get("/api/v1/tasks", headers=headers)
        row = next(i for i in listed.json()["items"] if i["id"] == body["id"])
        assert _ids(row["assignees"]) == {owner, other}


async def test_an_unstarred_roster_promotes_its_first_entry(client_for) -> None:
    t = await make_tenant("task-asg-unstarred")
    other = str((await _add_member(t.org.id, "other@task-asg-unstarred.test")).id)
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        created = await c.post(
            "/api/v1/tasks",
            json={"title": "Niemand gemarkeerd", "assignees": [{"user_id": other}]},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        assert _primary(created.json()["assignees"]) == other
        assert created.json()["assignee_user_id"] == other


async def test_patching_assignees_replaces_the_roster(client_for) -> None:
    t = await make_tenant("task-asg-replace")
    owner = str(t.user.id)
    other = str((await _add_member(t.org.id, "other@task-asg-replace.test")).id)
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        task = (
            await c.post(
                "/api/v1/tasks",
                json={
                    "title": "Wisselen",
                    "assignees": [{"user_id": owner, "is_primary": True}, {"user_id": other}],
                },
                headers=headers,
            )
        ).json()["id"]

        patched = await c.patch(
            f"/api/v1/tasks/{task}",
            json={"assignees": [{"user_id": other, "is_primary": True}]},
            headers=headers,
        )
        assert patched.status_code == 200, patched.text
        assert _ids(patched.json()["assignees"]) == {other}
        assert patched.json()["assignee_user_id"] == other

        # An explicit empty roster is a sentence of its own: assign nobody.
        cleared = await c.patch(
            f"/api/v1/tasks/{task}", json={"assignees": []}, headers=headers
        )
        assert cleared.json()["assignees"] == []
        assert cleared.json()["assignee_user_id"] is None


async def test_a_bare_assignee_column_hands_the_task_over(client_for) -> None:
    """The one place tasks differ from clients: reassigning is a hand-off, not a co-assignment.

    ``CompanyUpdate.responsible_user_id`` moves the star and keeps the old primary on as an
    ordinary assignee. On a task that would leave the previous assignee holding it forever —
    still in their "mijn taken", still notified — which is not what anyone means by "geef dit
    aan Sanne". Every pre-roster caller (the bulk editor, an import, an automation rule) means
    the same thing.
    """
    t = await make_tenant("task-asg-handoff")
    owner = str(t.user.id)
    other = str((await _add_member(t.org.id, "other@task-asg-handoff.test")).id)
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        task = (
            await c.post(
                "/api/v1/tasks",
                json={
                    "title": "Overdragen",
                    "assignees": [{"user_id": owner, "is_primary": True}, {"user_id": other}],
                },
                headers=headers,
            )
        ).json()["id"]

        patched = await c.patch(
            f"/api/v1/tasks/{task}", json={"assignee_user_id": other}, headers=headers
        )
        assert patched.status_code == 200, patched.text
        assert _ids(patched.json()["assignees"]) == {other}

        # And an update that never mentions an assignee leaves the roster alone.
        renamed = await c.patch(
            f"/api/v1/tasks/{task}", json={"title": "Andere titel"}, headers=headers
        )
        assert _ids(renamed.json()["assignees"]) == {other}


async def test_own_write_means_any_assignee(client_for) -> None:
    """``tasks.task.write:own`` is the seeded member grant, and ``own`` means assignee (#12).

    Reading only the mirrored star would hand the second assignee a task on their own list that
    answers 403 to every edit — including ticking it off.
    """
    t = await make_tenant("task-asg-own")
    owner = str(t.user.id)
    helper = await _add_member(t.org.id, "helper@task-asg-own.test")
    bystander = await _add_member(t.org.id, "bystander@task-asg-own.test")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        task = (
            await c.post(
                "/api/v1/tasks",
                json={
                    "title": "Gedeeld",
                    "assignees": [
                        {"user_id": owner, "is_primary": True},
                        {"user_id": str(helper.id)},
                    ],
                },
                headers=headers,
            )
        ).json()["id"]

        helper_headers = await auth_cookie(helper)
        edited = await c.patch(
            f"/api/v1/tasks/{task}", json={"title": "Ik pak dit op"}, headers=helper_headers
        )
        assert edited.status_code == 200, edited.text

        # Someone who is on neither end of it still cannot.
        refused = await c.patch(
            f"/api/v1/tasks/{task}",
            json={"title": "Niet van mij"},
            headers=await auth_cookie(bystander),
        )
        assert refused.status_code == 403


async def test_the_person_filter_and_my_day_match_any_assignee(client_for) -> None:
    t = await make_tenant("task-asg-mine")
    owner = str(t.user.id)
    helper = await _add_member(t.org.id, "helper@task-asg-mine.test")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        await c.post(
            "/api/v1/tasks",
            json={
                "title": "Gedeeld",
                "assignees": [
                    {"user_id": owner, "is_primary": True},
                    {"user_id": str(helper.id)},
                ],
            },
            headers=headers,
        )
        await c.post(
            "/api/v1/tasks",
            json={"title": "Alleen ik", "assignees": [{"user_id": owner, "is_primary": True}]},
            headers=headers,
        )

        filtered = await c.get(
            f"/api/v1/tasks?assignee_user_id={helper.id}", headers=headers
        )
        assert [i["title"] for i in filtered.json()["items"]] == ["Gedeeld"]

        helper_headers = await auth_cookie(helper)
        mine = await c.get("/api/v1/tasks/mine", headers=helper_headers)
        assert [i["title"] for i in mine.json()] == ["Gedeeld"]

        # The owner, primary on both, still sees both.
        assert len((await c.get("/api/v1/tasks/mine", headers=headers)).json()) == 2


async def test_a_client_contact_assignee_excludes_the_whole_roster(client_for) -> None:
    """#273's exclusivity is a claim about employees, not about the starred one (#375).

    A roster that stars nobody has a ``None`` mirror, so a check reading only the column would
    have waved through a task assigned to a client contact *and* two colleagues.
    """
    t = await make_tenant("task-asg-contact")
    other = str((await _add_member(t.org.id, "other@task-asg-contact.test")).id)
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        company = (
            await c.post("/api/v1/companies", json={"name": "Klant"}, headers=headers)
        ).json()["id"]
        contact = (
            await c.post(
                "/api/v1/contacts",
                json={"first_name": "Jan", "company_ids": [company]},
                headers=headers,
            )
        ).json()["id"]

        refused = await c.post(
            "/api/v1/tasks",
            json={
                "title": "Beide",
                "company_id": company,
                "assignee_contact_id": contact,
                "assignees": [{"user_id": other}],
            },
            headers=headers,
        )
        assert refused.status_code == 422, refused.text
        assert (
            refused.json()["error"]["fields"]["assignee_contact_id"]
            == "errors.tasks_assignee_conflict"
        )

        # The contact alone is fine, and leaves no employees behind.
        ok = await c.post(
            "/api/v1/tasks",
            json={
                "title": "Bij de klant",
                "company_id": company,
                "assignee_contact_id": contact,
            },
            headers=headers,
        )
        assert ok.status_code == 201, ok.text
        assert ok.json()["assignees"] == []

        # And a colleague cannot be added to it afterwards either.
        conflict = await c.patch(
            f"/api/v1/tasks/{ok.json()['id']}",
            json={"assignees": [{"user_id": other}]},
            headers=headers,
        )
        assert conflict.status_code == 422, conflict.text


async def test_a_recurring_task_repeats_onto_everyone(client_for) -> None:
    t = await make_tenant("task-asg-recur")
    owner = str(t.user.id)
    helper = str((await _add_member(t.org.id, "helper@task-asg-recur.test")).id)
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        task = (
            await c.post(
                "/api/v1/tasks",
                json={
                    "title": "Maandrapport",
                    "due_date": "2026-07-01",
                    "recurrence": {"freq": "monthly", "interval": 1, "mode": "after_completion"},
                    "assignees": [
                        {"user_id": owner, "is_primary": True},
                        {"user_id": helper},
                    ],
                },
                headers=headers,
            )
        ).json()["id"]

        statuses = (await c.get("/api/v1/tasks/statuses", headers=headers)).json()
        done = next(s["key"] for s in statuses if s["is_terminal"])
        assert (
            await c.patch(f"/api/v1/tasks/{task}", json={"status": done}, headers=headers)
        ).status_code == 200

        rows = (await c.get("/api/v1/tasks?q=Maandrapport", headers=headers)).json()["items"]
        spawned = next(r for r in rows if r["id"] != task)
        # Not just the mirrored star: the colleague who does half of it every month is still on it.
        assert _ids(spawned["assignees"]) == {owner, helper}
        assert _primary(spawned["assignees"]) == owner


async def test_a_template_leaves_a_roster_behind_it(client_for) -> None:
    """A write path that never touches ``TaskService`` still has to mirror (#375).

    ``mirror_primary_assignee`` exists because a mirror with no link is not cosmetic: the roster
    is what "mijn taken" reads, so such a task is assigned to somebody and invisible to them.
    """
    t = await make_tenant("task-asg-template")
    owner = str(t.user.id)
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        company = (
            await c.post("/api/v1/companies", json={"name": "Nieuwe klant"}, headers=headers)
        ).json()["id"]
        template = (
            await c.post(
                "/api/v1/tasks/templates",
                json={
                    "name": "Onboarding",
                    "items": [{"title": "Kick-off inplannen", "assignee_user_id": owner}],
                },
                headers=headers,
            )
        ).json()["id"]

        applied = await c.post(
            f"/api/v1/tasks/templates/{template}/apply",
            json={"company_id": company},
            headers=headers,
        )
        assert applied.status_code == 201, applied.text

        mine = await c.get("/api/v1/tasks/mine", headers=headers)
        assert "Kick-off inplannen" in [i["title"] for i in mine.json()]


async def test_task_assignees_never_cross_tenants(client_for) -> None:
    a = await make_tenant("task-asg-iso-a")
    b = await make_tenant("task-asg-iso-b")

    async with client_for(a.host) as c:
        headers = await auth_cookie(a.user)
        task = (
            await c.post(
                "/api/v1/tasks",
                json={
                    "title": "A's werk",
                    "assignees": [{"user_id": str(a.user.id), "is_primary": True}],
                },
                headers=headers,
            )
        ).json()["id"]

    async with client_for(b.host) as c:
        headers = await auth_cookie(b.user)
        assert (await c.get(f"/api/v1/tasks/{task}", headers=headers)).status_code == 404
        assert (
            await c.patch(
                f"/api/v1/tasks/{task}",
                json={"assignees": [{"user_id": str(b.user.id)}]},
                headers=headers,
            )
        ).status_code == 404
        # B's own board never surfaces A's row, and B cannot put A's employee on a task of theirs.
        assert (await c.get("/api/v1/tasks", headers=headers)).json()["items"] == []
        foreign = await c.post(
            "/api/v1/tasks",
            json={"title": "B's werk", "assignees": [{"user_id": str(a.user.id)}]},
            headers=headers,
        )
        assert foreign.status_code == 400, foreign.text
        assert foreign.json()["error"]["code"] == "invalid_assignee"
