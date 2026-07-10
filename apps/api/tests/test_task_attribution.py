"""The activity trail: what it records (#61) and who it says did it (#64).

The two issues meet in the same table. #61 is about the trail *under-reporting* — checklist
work never reached it and a comment row said only "commented". #64 is about the trail
*mis-attributing* — every FK to ``users.id`` here is ``ON DELETE SET NULL``, so deleting an
account silently handed that person's history to the system, which is what a NULL actor has
always meant (the recurrence cron writes one deliberately).
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select

from app.core.auth.models import User
from app.db import async_session_maker, set_current_org
from app.modules.tasks.models import TaskActivity
from tests.conftest import add_membership, auth_cookie, make_tenant


def _actions(detail: dict) -> list[str]:
    return [a["action"] for a in detail["activities"]]


def _one(detail: dict, action: str) -> dict:
    return next(a for a in detail["activities"] if a["action"] == action)


# --------------------------------------------------------------------------- #
# #61 — the trail records checklist work, and comment rows carry their detail
# --------------------------------------------------------------------------- #
async def test_checklist_lifecycle_lands_in_the_activity_trail(client_for) -> None:
    t = await make_tenant("trail-checklist")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        task = (
            await c.post("/api/v1/tasks", json={"title": "Onboarding"}, headers=headers)
        ).json()
        tid = task["id"]

        checklist = (
            await c.post(
                f"/api/v1/tasks/{tid}/checklists", json={"title": "Kickoff"}, headers=headers
            )
        ).json()
        cid = checklist["id"]

        item = (
            await c.post(
                f"/api/v1/tasks/{tid}/checklists/{cid}/items",
                json={"title": "Domein overzetten"},
                headers=headers,
            )
        ).json()
        iid = item["id"]

        # Ticking an item off — the single most routine action on a task — used to log nothing.
        await c.patch(
            f"/api/v1/tasks/{tid}/checklists/{cid}/items/{iid}",
            json={"done": True},
            headers=headers,
        )
        await c.patch(
            f"/api/v1/tasks/{tid}/checklists/{cid}/items/{iid}",
            json={"done": False},
            headers=headers,
        )
        await c.patch(
            f"/api/v1/tasks/{tid}/checklists/{cid}", json={"title": "Kick-off"}, headers=headers
        )

        detail = (await c.get(f"/api/v1/tasks/{tid}", headers=headers)).json()
        actions = _actions(detail)
        for expected in (
            "checklist_created",
            "checklist_item_added",
            "checklist_item_completed",
            "checklist_item_reopened",
            "checklist_renamed",
        ):
            assert expected in actions, f"{expected} missing from {actions}"

        assert _one(detail, "checklist_created")["payload"]["title"] == "Kickoff"
        assert _one(detail, "checklist_item_completed")["payload"]["title"] == "Domein overzetten"
        renamed = _one(detail, "checklist_renamed")["payload"]
        assert (renamed["from"], renamed["to"]) == ("Kickoff", "Kick-off")


async def test_reordering_a_checklist_is_not_activity(client_for) -> None:
    """A rename changes what the list *is*; a drag does not — `position` is noise, the way
    `_TRACKED_FIELDS` already excludes it on the task itself."""
    t = await make_tenant("trail-reorder")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        task = (await c.post("/api/v1/tasks", json={"title": "T"}, headers=headers)).json()
        tid = task["id"]
        cid = (
            await c.post(
                f"/api/v1/tasks/{tid}/checklists", json={"title": "A"}, headers=headers
            )
        ).json()["id"]

        await c.patch(
            f"/api/v1/tasks/{tid}/checklists/{cid}", json={"position": 5}, headers=headers
        )

        detail = (await c.get(f"/api/v1/tasks/{tid}", headers=headers)).json()
        assert "checklist_renamed" not in _actions(detail)


async def test_comment_activity_carries_an_excerpt_and_the_comment_id(client_for) -> None:
    t = await make_tenant("trail-comment")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        task = (await c.post("/api/v1/tasks", json={"title": "T"}, headers=headers)).json()
        tid = task["id"]

        body = "Klant wil de nieuwe huisstijl vóór vrijdag zien"
        comment = (
            await c.post(f"/api/v1/tasks/{tid}/comments", json={"body": body}, headers=headers)
        ).json()

        detail = (await c.get(f"/api/v1/tasks/{tid}", headers=headers)).json()
        commented = _one(detail, "commented")
        assert commented["payload"]["excerpt"] == body
        assert commented["payload"]["comment_id"] == comment["id"]

        await c.patch(
            f"/api/v1/tasks/{tid}/comments/{comment['id']}",
            json={"body": "Donderdag al"},
            headers=headers,
        )
        detail = (await c.get(f"/api/v1/tasks/{tid}", headers=headers)).json()
        edited = _one(detail, "comment_edited")
        assert edited["payload"]["excerpt"] == "Donderdag al"
        assert edited["payload"]["comment_id"] == comment["id"]

        # A deleted comment has no id to link to, but the excerpt is what was said.
        await c.delete(f"/api/v1/tasks/{tid}/comments/{comment['id']}", headers=headers)
        detail = (await c.get(f"/api/v1/tasks/{tid}", headers=headers)).json()
        deleted = _one(detail, "comment_deleted")
        assert deleted["payload"]["excerpt"] == "Donderdag al"
        assert "comment_id" not in deleted["payload"]


async def test_a_long_comment_is_excerpted_not_stored_whole(client_for) -> None:
    t = await make_tenant("trail-excerpt")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        task = (await c.post("/api/v1/tasks", json={"title": "T"}, headers=headers)).json()
        tid = task["id"]
        await c.post(
            f"/api/v1/tasks/{tid}/comments", json={"body": "x" * 500}, headers=headers
        )
        detail = (await c.get(f"/api/v1/tasks/{tid}", headers=headers)).json()
        excerpt = _one(detail, "commented")["payload"]["excerpt"]
        assert len(excerpt) == 140 and excerpt.endswith("…")


# --------------------------------------------------------------------------- #
# #64 — attribution outlives the account
# --------------------------------------------------------------------------- #
async def test_activity_and_comments_snapshot_the_actor_name(client_for) -> None:
    t = await make_tenant("attr-snapshot")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        task = (await c.post("/api/v1/tasks", json={"title": "T"}, headers=headers)).json()
        await c.post(f"/api/v1/tasks/{task['id']}/comments", json={"body": "hoi"}, headers=headers)
        detail = (await c.get(f"/api/v1/tasks/{task['id']}", headers=headers)).json()

    # The live account has no full_name, so the display name is its email — and it is stored,
    # not merely resolved, which is the whole point.
    assert _one(detail, "created")["actor_name"] == t.user.email
    assert _one(detail, "created")["actor_deleted"] is False
    assert detail["comments"][0]["author_name"] == t.user.email
    assert detail["comments"][0]["author_deleted"] is False


async def test_a_deleted_user_keeps_their_name_and_is_marked_deleted(client_for) -> None:
    """The bug: hard-deleting a user nulled the FK, and the live join then said "System".

    Afterwards the row still names them, and ``actor_deleted`` tells the UI to mark it — read
    back through the ordinary API, by a colleague who is still around to look.
    """
    t = await make_tenant("attr-deleted")
    author_email = t.user.email
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        task = (await c.post("/api/v1/tasks", json={"title": "T"}, headers=headers)).json()
        tid = task["id"]
        await c.post(f"/api/v1/tasks/{tid}/comments", json={"body": "hoi"}, headers=headers)

    # A colleague in the same org, who will outlive the author and read the trail.
    survivor = User(
        id=uuid.uuid4(),
        email="survivor@example.com",
        hashed_password="",
        is_active=True,
        is_verified=True,
    )
    async with async_session_maker() as session:
        session.add(survivor)
        await session.flush()
        await set_current_org(session, t.org.id)
        await add_membership(session, t.org.id, survivor.id, "admin")
        await session.commit()

    async with async_session_maker() as session:
        await session.execute(delete(User).where(User.id == t.user.id))  # `users` carries no RLS
        await session.commit()

    survivor_headers = await auth_cookie(survivor)
    async with client_for(t.host) as c:
        detail = (await c.get(f"/api/v1/tasks/{tid}", headers=survivor_headers)).json()

    created = _one(detail, "created")
    assert created["actor_user_id"] is None, "the FK is SET NULL — that is the premise"
    assert created["actor_name"] == author_email, "…and the snapshot is what survives it"
    assert created["actor_deleted"] is True

    commented = _one(detail, "commented")
    assert commented["actor_name"] == author_email
    assert commented["actor_deleted"] is True

    comment = detail["comments"][0]
    assert comment["author_user_id"] is None
    assert comment["author_name"] == author_email, "a comment must never render as a bare '—'"
    assert comment["author_deleted"] is True

    # The row is still there and still readable; nothing was lost but the account.
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        rows = (
            await session.execute(
                select(TaskActivity).where(TaskActivity.task_id == uuid.UUID(tid))
            )
        ).scalars().all()
    assert {r.actor_name for r in rows} == {author_email}


async def test_a_null_actor_with_no_snapshot_is_still_the_system(client_for) -> None:
    """The cron writes ``actor_user_id=None`` on purpose. It must stay distinguishable from a
    departed human, which is exactly what a *missing* snapshot means."""
    t = await make_tenant("attr-system")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        task = (await c.post("/api/v1/tasks", json={"title": "T"}, headers=headers)).json()
        tid = task["id"]

        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            session.add(
                TaskActivity(
                    org_id=t.org.id,
                    task_id=uuid.UUID(tid),
                    actor_user_id=None,
                    actor_name=None,
                    action="recurrence_spawned",
                    payload={"source_task_id": str(uuid.uuid4())},
                )
            )
            await session.commit()

        detail = (await c.get(f"/api/v1/tasks/{tid}", headers=headers)).json()

    system = _one(detail, "recurrence_spawned")
    assert system["actor_name"] is None
    assert system["actor_deleted"] is False, "no name at all is the system, not a deleted user"

    # …while the human row beside it is named and not flagged.
    human = _one(detail, "created")
    assert human["actor_name"] == t.user.email
    assert human["actor_deleted"] is False
