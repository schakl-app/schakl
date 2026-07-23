"""Gmail-style conversation grouping for emails (#272): conversation_id assignment on the
gmail feed and on approve, list folding + conversation_count, the manual add-to-conversation
merge (owner-only), the /thread endpoint, and the migration's backfill logic."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from app.core.auth.models import User
from app.core.events import SystemContext
from app.db import async_session_maker, set_current_org
from app.modules.interactions import system as interactions_system
from app.modules.interactions.models import Interaction
from tests.conftest import auth_cookie, make_tenant

_T0 = datetime(2026, 7, 10, 9, 0, tzinfo=UTC)


def _at(hours: int) -> datetime:
    return _T0 + timedelta(hours=hours)


async def _seed(
    tenant,
    owner_user_id: uuid.UUID,
    *,
    thread_id: str | None,
    message_id: str,
    occurred_at: datetime,
    pending: bool = False,
    subject: str | None = None,
) -> str:
    """Insert a gmail-sourced interaction the way the poller does, with control over the thread,
    message id and timestamp so the fold's 'newest representative' is deterministic."""
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        ctx = SystemContext(org=tenant.org, session=session)
        row = await interactions_system.record_email(
            ctx,
            owner_user_id=owner_user_id,
            owner_name="Mailbox Owner",
            occurred_at=occurred_at,
            subject=subject or f"Message {message_id}",
            snippet=f"snippet {message_id}",
            direction="inbound",
            participants=[{"email": "klant@client.nl", "name": "Klant", "role": "from"}],
            gmail_message_id=message_id,
            gmail_thread_id=thread_id,
            rfc822_message_id=f"<{message_id}@mail.example>",
            deep_link=None,
            pending=pending,
            mappings={},
        )
        await session.commit()
        return str(row.id)


async def _member(client, headers, email: str) -> User:
    res = await client.post(
        "/api/v1/members/invite",
        json={"email": email, "full_name": "Mailbox Owner", "role": "member"},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return User(
        id=uuid.UUID(res.json()["user_id"]), email=email, hashed_password="", is_active=True
    )


async def _conversation_id(org_id: uuid.UUID, interaction_id: str) -> uuid.UUID | None:
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        row = await session.get(Interaction, uuid.UUID(interaction_id))
        return row.conversation_id


async def test_auto_logged_thread_folds_to_one_row(client_for) -> None:
    """Two emails auto-logged in the same gmail thread share a conversation and fold to one
    list row carrying the message count; a third in another thread stays separate."""
    t = await make_tenant("conv-autofold")
    headers = await auth_cookie(t.user)
    first = await _seed(t, t.user.id, thread_id="thr-1", message_id="m1", occurred_at=_at(0))
    second = await _seed(t, t.user.id, thread_id="thr-1", message_id="m2", occurred_at=_at(1))
    await _seed(t, t.user.id, thread_id="thr-2", message_id="m3", occurred_at=_at(2))

    # The first message got a conversation_id backfilled when the second joined it.
    conv_first = await _conversation_id(t.org.id, first)
    conv_second = await _conversation_id(t.org.id, second)
    assert conv_first is not None and conv_first == conv_second

    async with client_for(t.host) as c:
        page = (await c.get("/api/v1/interactions", headers=headers)).json()
        # Two conversations: the folded thr-1 pair (one row) + the thr-2 singleton.
        assert page["total"] == 2
        subjects = {row["subject"]: row for row in page["items"]}
        # The representative is the newest message of the folded thread.
        folded = subjects["Message m2"]
        assert folded["conversation_count"] == 2
        assert folded["conversation_id"] == str(conv_first)
        # The lone thread is a singleton — no badge.
        assert subjects["Message m3"]["conversation_count"] == 1
        # The older message is NOT its own top-level row (folded away).
        assert "Message m1" not in subjects


async def test_second_message_joins_conversation_on_approve(client_for) -> None:
    """A pending email approved into a thread that already has a logged sibling joins its
    conversation the moment it lands, and /thread returns both newest-first."""
    t = await make_tenant("conv-approve-join")
    headers = await auth_cookie(t.user)
    logged = await _seed(t, t.user.id, thread_id="thr-A", message_id="a1", occurred_at=_at(0))
    pending = await _seed(
        t, t.user.id, thread_id="thr-A", message_id="a2", occurred_at=_at(1), pending=True
    )
    # Before approval the logged singleton has no conversation and doesn't fold.
    assert await _conversation_id(t.org.id, logged) is None

    async with client_for(t.host) as c:
        approved = await c.post(f"/api/v1/interactions/{pending}/approve", headers=headers)
        assert approved.status_code == 200, approved.text
        assert approved.json()["conversation_count"] == 2

        conv = await _conversation_id(t.org.id, pending)
        assert conv is not None and conv == await _conversation_id(t.org.id, logged)

        # The list now folds the pair to one row.
        page = (await c.get("/api/v1/interactions", headers=headers)).json()
        assert page["total"] == 1
        assert page["items"][0]["conversation_count"] == 2

        # /thread on the representative returns both messages, newest first.
        thread = (await c.get(f"/api/v1/interactions/{pending}/thread", headers=headers)).json()
        assert [row["id"] for row in thread] == [pending, logged]
        assert all(row["conversation_count"] == 2 for row in thread)


async def test_manual_and_pending_rows_never_fold(client_for) -> None:
    """conversation_id is only ever set on logged email rows: manual notes and a still-pending
    email each stay their own singleton row with conversation_count 1."""
    t = await make_tenant("conv-no-fold")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        for i in range(2):
            assert (
                await c.post(
                    "/api/v1/interactions",
                    json={"kind": "note", "occurred_at": _at(i).isoformat(), "subject": f"N{i}"},
                    headers=headers,
                )
            ).status_code == 201
    # A pending gmail row in a thread — never folded while pending.
    pending = await _seed(
        t, t.user.id, thread_id="thr-P", message_id="p1", occurred_at=_at(3), pending=True
    )
    assert await _conversation_id(t.org.id, pending) is None

    async with client_for(t.host) as c:
        page = (
            await c.get("/api/v1/interactions", params={"mine": True}, headers=headers)
        ).json()
        # Two notes + one pending email = three separate rows, none folded.
        assert page["total"] == 3
        assert all(row["conversation_count"] == 1 for row in page["items"])


async def test_add_to_conversation_merges_and_guards(client_for) -> None:
    """The manual merge glues one logged gmail email onto another's conversation; it is
    owner-only and rejects an ineligible target (self, manual, pending, another owner's)."""
    t = await make_tenant("conv-merge")  # t.user = owner, holds "*"
    owner_headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        mailbox = await _member(c, owner_headers, "mailbox@merge.example")
        colleague = await _member(c, owner_headers, "collega@merge.example")
    mailbox_headers = await auth_cookie(mailbox)

    # Two unrelated logged emails, both the mailbox owner's, in different threads.
    row = await _seed(t, mailbox.id, thread_id="thr-C", message_id="c1", occurred_at=_at(0))
    target = await _seed(t, mailbox.id, thread_id="thr-D", message_id="d1", occurred_at=_at(1))
    # A manual note and a pending email and a colleague-owned email — all ineligible targets.
    pending = await _seed(
        t, mailbox.id, thread_id="thr-E", message_id="e1", occurred_at=_at(2), pending=True
    )
    foreign = await _seed(t, colleague.id, thread_id="thr-F", message_id="f1", occurred_at=_at(3))

    async with client_for(t.host) as c:
        manual = (
            await c.post(
                "/api/v1/interactions",
                json={"kind": "note", "occurred_at": _at(4).isoformat(), "subject": "Los"},
                headers=mailbox_headers,
            )
        ).json()

        def merge(interaction_id, target_id, headers):
            return c.post(
                f"/api/v1/interactions/{interaction_id}/add-to-conversation",
                json={"target_interaction_id": target_id},
                headers=headers,
            )

        # Self target → 422.
        assert (await merge(row, row, mailbox_headers)).status_code == 422
        # Manual target → 422.
        assert (await merge(row, manual["id"], mailbox_headers)).status_code == 422
        # Pending target → 422.
        assert (await merge(row, pending, mailbox_headers)).status_code == 422
        # A colleague's row as target → 422 (not the caller's mailbox).
        assert (await merge(row, foreign, mailbox_headers)).status_code == 422
        # The row itself must be the caller's own gmail row: the org owner is not the mailbox
        # owner, so even a wildcard holder is refused (403, review is owner-only).
        assert (await merge(row, target, owner_headers)).status_code == 403

        # Happy path: the two unrelated emails fold into one conversation.
        merged = await merge(row, target, mailbox_headers)
        assert merged.status_code == 200, merged.text
        assert merged.json()["conversation_count"] == 2
        conv = await _conversation_id(t.org.id, row)
        assert conv is not None and conv == await _conversation_id(t.org.id, target)

        # It landed on the row's own activity trail.
        trail = (
            await c.get(
                "/api/v1/activity",
                params={"entity_type": "interaction", "entity_id": row},
                headers=mailbox_headers,
            )
        ).json()
        assert "interaction.conversation_linked" in [e["action"] for e in trail]

        # The row (path id) must itself be logged: merging a pending row is a 409.
        assert (await merge(pending, target, mailbox_headers)).status_code == 409


async def test_thread_endpoint_singleton_and_isolation(client_for) -> None:
    """A row not in a conversation is its own one-message thread; another org never reaches it."""
    t = await make_tenant("conv-thread-iso")
    headers = await auth_cookie(t.user)
    single = await _seed(t, t.user.id, thread_id="thr-S", message_id="s1", occurred_at=_at(0))
    async with client_for(t.host) as c:
        thread = (await c.get(f"/api/v1/interactions/{single}/thread", headers=headers)).json()
        assert [row["id"] for row in thread] == [single]
        assert thread[0]["conversation_count"] == 1

    other = await make_tenant("conv-thread-iso-b")
    other_headers = await auth_cookie(other.user)
    async with client_for(other.host) as cb:
        assert (
            await cb.get(f"/api/v1/interactions/{single}/thread", headers=other_headers)
        ).status_code == 404


async def test_migration_backfill_folds_existing_threads() -> None:
    """The migration's per-org backfill mints one conversation per (thread) group of logged rows
    and is idempotent: a re-run reuses an existing sibling's id rather than splitting the group."""
    t = await make_tenant("conv-backfill")
    # Two logged rows sharing a thread, as they'd exist *before* this migration: NULL conversation.
    a = await _seed(t, t.user.id, thread_id="thr-mig", message_id="mg1", occurred_at=_at(0))
    b = await _seed(t, t.user.id, thread_id="thr-mig", message_id="mg2", occurred_at=_at(1))
    solo = await _seed(t, t.user.id, thread_id="thr-solo", message_id="mg3", occurred_at=_at(2))

    backfill = text(
        """
        WITH g AS MATERIALIZED (
                SELECT gmail_thread_id,
                       COALESCE(MAX(conversation_id::text),
                                gen_random_uuid()::text)::uuid AS conv
                  FROM interactions
                 WHERE org_id = :org_id
                   AND gmail_thread_id IS NOT NULL
                   AND status = 'logged'
                 GROUP BY gmail_thread_id
             )
        UPDATE interactions i
           SET conversation_id = g.conv
          FROM g
         WHERE i.org_id = :org_id
           AND i.gmail_thread_id = g.gmail_thread_id
           AND i.status = 'logged'
           AND i.conversation_id IS NULL
        """
    )
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        # Simulate pre-migration data: strip the conversation ids the runtime helper assigned.
        await session.execute(
            text("UPDATE interactions SET conversation_id = NULL WHERE org_id = :o"),
            {"o": t.org.id},
        )
        await session.commit()

    async def convs() -> dict[str, uuid.UUID | None]:
        return {
            "a": await _conversation_id(t.org.id, a),
            "b": await _conversation_id(t.org.id, b),
            "solo": await _conversation_id(t.org.id, solo),
        }

    assert all(v is None for v in (await convs()).values())

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        await session.execute(backfill, {"org_id": t.org.id})
        await session.commit()

    first = await convs()
    assert first["a"] is not None and first["a"] == first["b"]  # the thread folded
    assert first["solo"] is not None and first["solo"] != first["a"]  # the singleton is its own

    # Idempotent: re-running the backfill changes nothing (every row already has an id).
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        await session.execute(backfill, {"org_id": t.org.id})
        await session.commit()
    assert await convs() == first
