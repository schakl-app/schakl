"""A contactmoment names every task it is about, and a pending email every colleague on it.

Two rosters, one shape (``interaction_contacts``, #300), and the review-set rule they came
with: a pending gmail row is private to its mailbox owner **and** to the colleagues whose
address was on the message — whoever of them decides first decides for all, and the others'
queue entries and notifications go with the decision.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, text

from app.core.auth.models import User
from app.core.events import SystemContext
from app.db import async_session_maker, set_current_org
from app.modules.interactions import system as interactions_system
from app.modules.interactions.models import (
    Interaction,
    InteractionReviewer,
    InteractionTask,
)
from app.modules.notifications.models import Notification
from tests.conftest import auth_cookie, default_company, make_tenant
from tests.test_google_gmail import _message, _poll, _StubGmail
from tests.test_google_gmail import _seed as _seed_connection

_NOW = datetime(2026, 7, 10, 9, 0, tzinfo=UTC)
_DUE = "2030-01-15"


async def _member(client, headers, email: str) -> User:
    res = await client.post(
        "/api/v1/members/invite",
        json={"email": email, "full_name": "Collega", "role": "member"},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return User(
        id=uuid.UUID(res.json()["user_id"]), email=email, hashed_password="", is_active=True
    )


async def _task(client, headers, title: str, **over) -> dict:
    body = {"title": title, "due_date": _DUE, **over}
    body.setdefault("company_id", await default_company(client, headers))
    res = await client.post("/api/v1/tasks", json=body, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


async def _task_trail(org_id: uuid.UUID, task_id: str) -> list[str]:
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        rows = await session.execute(
            text(
                "SELECT action FROM activity_log WHERE org_id = :oid"
                " AND entity_type = 'task' AND entity_id = :eid ORDER BY created_at, action"
            ),
            {"oid": org_id, "eid": task_id},
        )
        return [row[0] for row in rows]


# --- the task roster --------------------------------------------------------------------- #


async def test_a_moment_names_several_tasks_and_each_task_lists_it(client_for) -> None:
    """``task_ids`` is the roster and ``task_id`` its lead; every task on it filters the
    timeline, counts the moment, and hears about it on its own trail — and re-filing moves
    the chips *and* the lead, with the tasks that were dropped told so."""
    t = await make_tenant("inter-task-roster")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Client NL"}, headers=headers)
        ).json()
        launch = await _task(c, headers, "Site launch", company_id=company["id"])
        invoice = await _task(c, headers, "Invoice question", company_id=company["id"])
        other = await _task(c, headers, "Unrelated", company_id=company["id"])

        created = await c.post(
            "/api/v1/interactions",
            json={
                "kind": "call",
                "occurred_at": _NOW.isoformat(),
                "subject": "Belafspraak",
                "task_ids": [launch["id"], invoice["id"]],
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        row = created.json()
        assert row["task_id"] == launch["id"]
        assert [task["id"] for task in row["tasks"]] == [launch["id"], invoice["id"]]
        assert row["task_title"] == "Site launch"
        assert row["tasks"][1]["title"] == "Invoice question"
        # The client is derived from the lead task, exactly as it was from the single link.
        assert row["company_id"] == company["id"]

        # Every task on the roster filters the timeline, not only the lead.
        for task in (launch, invoice):
            page = (
                await c.get(f"/api/v1/interactions?task_id={task['id']}", headers=headers)
            ).json()
            assert [item["id"] for item in page["items"]] == [row["id"]]
        assert (await c.get(f"/api/v1/interactions?task_id={other['id']}", headers=headers)).json()[
            "items"
        ] == []
        assert "interaction.logged" in await _task_trail(t.org.id, invoice["id"])

        # Move it: the invoice ticket becomes the lead, the launch drops off and is told.
        moved = await c.patch(
            f"/api/v1/interactions/{row['id']}",
            json={"task_ids": [invoice["id"], other["id"]]},
            headers=headers,
        )
        assert moved.status_code == 200, moved.text
        assert moved.json()["task_id"] == invoice["id"]
        assert [task["id"] for task in moved.json()["tasks"]] == [invoice["id"], other["id"]]
        assert (
            await c.get(f"/api/v1/interactions?task_id={launch['id']}", headers=headers)
        ).json()["items"] == []
        assert "interaction.unlinked" in await _task_trail(t.org.id, launch["id"])
        assert "interaction.linked" in await _task_trail(t.org.id, other["id"])

        # The trail on the moment itself records the roster change as a field diff.
        trail = (
            await c.get(
                f"/api/v1/activity?entity_type=interaction&entity_id={row['id']}",
                headers=headers,
            )
        ).json()
        edits = [e for e in trail if e["action"] == "updated"]
        assert edits and "task_ids" in edits[-1]["payload"]["changes"]

        # An empty roster clears the lead too — the two are never written apart.
        cleared = await c.patch(
            f"/api/v1/interactions/{row['id']}", json={"task_ids": []}, headers=headers
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["task_id"] is None and cleared.json()["tasks"] == []


async def test_a_bare_task_id_still_writes_a_one_task_roster(client_for) -> None:
    """Every pre-roster caller — an older web build, the generated MCP tool — keeps writing
    exactly what it wrote: one task, which is now a one-chip roster."""
    t = await make_tenant("inter-task-lead")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        task = await _task(c, headers, "Only task")
        created = await c.post(
            "/api/v1/interactions",
            json={"kind": "note", "occurred_at": _NOW.isoformat(), "task_id": task["id"]},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        assert [x["id"] for x in created.json()["tasks"]] == [task["id"]]
        # ``task_ids`` wins over ``task_id`` when both arrive (schemas.py).
        moved = await c.patch(
            f"/api/v1/interactions/{created.json()['id']}",
            json={"task_id": task["id"], "task_ids": []},
            headers=headers,
        )
        assert moved.json()["task_id"] is None

        unknown = await c.post(
            "/api/v1/interactions",
            json={
                "kind": "note",
                "occurred_at": _NOW.isoformat(),
                "task_ids": [str(uuid.uuid4())],
            },
            headers=headers,
        )
        assert unknown.status_code == 422
        assert unknown.json()["error"]["fields"] == {"task_ids": "errors.not_found"}


async def test_any_task_on_the_roster_may_be_closed_by_the_moment(client_for) -> None:
    """The closing-moment check reads the roster, not only the lead column."""
    t = await make_tenant("inter-task-close")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        lead = await _task(c, headers, "Lead")
        second = await _task(c, headers, "Second")
        row = (
            await c.post(
                "/api/v1/interactions",
                json={
                    "kind": "call",
                    "occurred_at": _NOW.isoformat(),
                    "task_ids": [lead["id"], second["id"]],
                },
                headers=headers,
            )
        ).json()
        statuses = (await c.get("/api/v1/tasks/statuses", headers=headers)).json()
        done = next(s["key"] for s in statuses if s["is_terminal"])
        closed = await c.patch(
            f"/api/v1/tasks/{second['id']}",
            json={"status": done, "closing_interaction_id": row["id"]},
            headers=headers,
        )
        assert closed.status_code == 200, closed.text
        assert closed.json()["closing_interaction_id"] == row["id"]


async def test_a_thread_follow_up_inherits_the_whole_task_roster(client_for) -> None:
    """``thread_mappings`` carries ``task_ids``, and ``record_email`` writes them as chips."""
    t = await make_tenant("inter-task-thread")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        a = await _task(c, headers, "A")
        b = await _task(c, headers, "B")
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        ctx = SystemContext(org=t.org, session=session)
        common = {
            "owner_user_id": t.user.id,
            "owner_name": "Owner",
            "occurred_at": _NOW,
            "subject": "Thread",
            "snippet": None,
            "direction": "inbound",
            "participants": [{"email": "klant@client.nl", "name": "Klant", "role": "from"}],
            "gmail_thread_id": "thr-roster",
            "deep_link": None,
        }
        await interactions_system.record_email(
            ctx,
            gmail_message_id="m1",
            rfc822_message_id="<m1@x>",
            pending=False,
            mappings={"task_ids": [uuid.UUID(a["id"]), uuid.UUID(b["id"])]},
            **common,
        )
        inherited = await interactions_system.thread_mappings(ctx, "thr-roster")
        assert inherited is not None
        assert inherited["task_id"] == uuid.UUID(a["id"])
        assert inherited["task_ids"] == [uuid.UUID(a["id"]), uuid.UUID(b["id"])]
        follow_up = await interactions_system.record_email(
            ctx,
            gmail_message_id="m2",
            rfc822_message_id="<m2@x>",
            pending=False,
            mappings=inherited,
            **common,
        )
        await session.commit()
        # The RLS GUC is transaction-local: rebind it, or the read below sees nothing.
        await set_current_org(session, t.org.id)
        chips = list(
            await session.scalars(
                select(InteractionTask.task_id)
                .where(InteractionTask.interaction_id == follow_up.id)
                .order_by(InteractionTask.position)
            )
        )
        assert chips == [uuid.UUID(a["id"]), uuid.UUID(b["id"])]


# --- shared review ------------------------------------------------------------------------ #


async def _pending_via_poll(client_for, monkeypatch, *, slug: str, cc: str):
    """One pending email into the owner's mailbox with ``cc`` on it — through the real poller,
    so the reviewer set is what the ingest computes and not what a test wrote."""
    t = await make_tenant(slug)
    connection_id = await _seed_connection(t)
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        colleague = await _member(c, headers, cc)
        bystander = await _member(c, headers, f"ander-{slug}@example.com")
        company = (
            await c.post("/api/v1/companies", json={"name": "Client NL"}, headers=headers)
        ).json()
        await c.post(
            "/api/v1/contacts",
            json={
                "first_name": "Klant",
                "email": "klant@client.nl",
                "company_ids": [company["id"]],
            },
            headers=headers,
        )
    stub = _StubGmail(
        history=["msg-1"],
        messages={"msg-1": _message("msg-1", sender="Klant <klant@client.nl>", cc=cc)},
        history_id="9000",
    )
    assert await _poll(t, connection_id, stub, monkeypatch) == 1
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        row = (await session.execute(select(Interaction))).scalar_one()
        reviewers = set(
            await session.scalars(
                select(InteractionReviewer.user_id).where(
                    InteractionReviewer.interaction_id == row.id
                )
            )
        )
        assert reviewers == {colleague.id}
        return t, headers, colleague, bystander, str(row.id)


async def _unread(org_id: uuid.UUID, user_id: uuid.UUID) -> int:
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        rows = await session.scalars(
            select(Notification.id).where(
                Notification.user_id == user_id, Notification.read_at.is_(None)
            )
        )
        return len(list(rows))


async def test_a_colleague_on_the_email_reviews_it_and_the_owner_queue_clears(
    client_for, monkeypatch
) -> None:
    """The colleague in Cc sees the pending row in *their* queue, is notified, may open it and
    approve it (filing it on a task in the same step); the owner's queue and notification are
    cleared by that approval. A member who was not on the email sees nothing."""
    t, owner_h, colleague, bystander, row_id = await _pending_via_poll(
        client_for, monkeypatch, slug="gmail-shared-review", cc="collega@example.com"
    )
    colleague_h = await auth_cookie(colleague)
    bystander_h = await auth_cookie(bystander)
    assert await _unread(t.org.id, t.user.id) == 1
    assert await _unread(t.org.id, colleague.id) == 1
    assert await _unread(t.org.id, bystander.id) == 0

    async with client_for(t.host) as c:
        queue = "/api/v1/interactions?status=pending&mine=true"
        for h in (owner_h, colleague_h):
            page = (await c.get(queue, headers=h)).json()
            assert [item["id"] for item in page["items"]] == [row_id]
            assert page["items"][0]["reviewable"] is True
        assert (await c.get(queue, headers=bystander_h)).json()["items"] == []
        one = f"/api/v1/interactions/{row_id}"
        assert (await c.get(one, headers=bystander_h)).status_code == 404
        assert (await c.get(one, headers=colleague_h)).status_code == 200
        # The thread desk opens for the reviewer too.
        assert (await c.get(f"{one}/thread", headers=colleague_h)).status_code == 200
        # …but a bystander may not decide either way.
        assert (
            await c.post(f"/api/v1/interactions/{row_id}/approve", headers=bystander_h)
        ).status_code in (403, 404)

        # The colleague may make tasks but not clients; the stand-in client is the owner's.
        task = await _task(
            c, colleague_h, "Answer the quote", company_id=await default_company(c, owner_h)
        )
        approved = await c.post(
            f"/api/v1/interactions/{row_id}/approve",
            json={"task_ids": [task["id"]]},
            headers=colleague_h,
        )
        assert approved.status_code == 200, approved.text
        body = approved.json()
        assert body["status"] == "logged"
        assert body["task_id"] == task["id"]
        assert body["reviewable"] is False
        # Ownership never moved: it is still the owner's mailbox that holds the message.
        assert body["owner_user_id"] == str(t.user.id)

        for h in (owner_h, colleague_h):
            assert (await c.get(queue, headers=h)).json()["items"] == []
        # Logged now, so the whole team reads it — the bystander included.
        assert (await c.get(one, headers=bystander_h)).status_code == 200

    assert await _unread(t.org.id, t.user.id) == 0
    assert await _unread(t.org.id, colleague.id) == 0
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        left = list(await session.scalars(select(InteractionReviewer.id)))
        assert left == []


async def test_the_owner_approving_first_clears_the_colleague_too(client_for, monkeypatch) -> None:
    """Symmetric: whichever reviewer goes first, the other's queue and notification go."""
    t, owner_h, colleague, _, row_id = await _pending_via_poll(
        client_for, monkeypatch, slug="gmail-owner-first", cc="collega2@example.com"
    )
    colleague_h = await auth_cookie(colleague)
    async with client_for(t.host) as c:
        assert (
            await c.post(f"/api/v1/interactions/{row_id}/approve", headers=owner_h)
        ).status_code == 200
        queue = "/api/v1/interactions?status=pending&mine=true"
        assert (await c.get(queue, headers=colleague_h)).json()["items"] == []
        # A second decision is refused for both — the row is no longer pending.
        assert (
            await c.post(f"/api/v1/interactions/{row_id}/approve", headers=colleague_h)
        ).status_code in (403, 409)
    assert await _unread(t.org.id, colleague.id) == 0


async def test_a_colleague_may_reject_and_the_bulk_path_agrees(client_for, monkeypatch) -> None:
    """Reject through the bulk route as the reviewer: eligibility is the same statement asked
    the batch way, and the row (with its reviewer links) is gone for everyone."""
    t, owner_h, colleague, bystander, row_id = await _pending_via_poll(
        client_for, monkeypatch, slug="gmail-reviewer-reject", cc="collega3@example.com"
    )
    colleague_h = await auth_cookie(colleague)
    bystander_h = await auth_cookie(bystander)
    async with client_for(t.host) as c:
        refused = await c.post(
            "/api/v1/interactions/bulk/reject", json={"ids": [row_id]}, headers=bystander_h
        )
        assert refused.status_code == 200
        assert refused.json()["succeeded"] == 0 and len(refused.json()["failed"]) == 1

        done = await c.post(
            "/api/v1/interactions/bulk/reject", json={"ids": [row_id]}, headers=colleague_h
        )
        assert done.status_code == 200, done.text
        assert done.json()["succeeded"] == 1
        assert (await c.get(f"/api/v1/interactions/{row_id}", headers=owner_h)).status_code == 404
    assert await _unread(t.org.id, t.user.id) == 0
