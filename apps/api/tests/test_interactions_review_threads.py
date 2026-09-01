"""The review queue folds a Gmail thread to one row (the conversation-review research task):
pending rows group per mailbox + thread and carry the ids a thread-level review acts on,
``/thread`` on a pending anchor shows the logged history beside the waiting messages, and a
single approve / reject can take the rest of the thread with it."""

from __future__ import annotations

from tests.conftest import auth_cookie, make_tenant
from tests.test_interactions_conversations import _at, _conversation_id, _member, _seed


async def _company(client, headers, name: str = "Acme") -> str:
    res = await client.post("/api/v1/companies", json={"name": name}, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def test_review_queue_folds_a_pending_thread(client_for) -> None:
    """Three pending messages of one thread are one queue row — the newest, counting three,
    naming all three (oldest first) and everyone the thread reached — beside a lone thread's
    own row. The logged history of that same thread stays its own row on the timeline, and a
    colleague sees none of the pending rows at all."""
    t = await make_tenant("review-fold")
    headers = await auth_cookie(t.user)
    history = await _seed(t, t.user.id, thread_id="thr-A", message_id="h0", occurred_at=_at(-1))
    a1 = await _seed(
        t,
        t.user.id,
        thread_id="thr-A",
        message_id="a1",
        occurred_at=_at(0),
        pending=True,
        participants=[{"email": "jan@client.nl", "name": "Jan", "role": "from"}],
    )
    a2 = await _seed(
        t,
        t.user.id,
        thread_id="thr-A",
        message_id="a2",
        occurred_at=_at(1),
        pending=True,
        participants=[{"email": "piet@client.nl", "name": "Piet", "role": "from"}],
    )
    a3 = await _seed(
        t,
        t.user.id,
        thread_id="thr-A",
        message_id="a3",
        occurred_at=_at(2),
        pending=True,
        participants=[{"email": "jan@client.nl", "name": "Jan", "role": "from"}],
    )
    b1 = await _seed(
        t, t.user.id, thread_id="thr-B", message_id="b1", occurred_at=_at(3), pending=True
    )

    async with client_for(t.host) as c:
        queue = (
            await c.get("/api/v1/interactions?status=pending&mine=true", headers=headers)
        ).json()
        # Two conversations to review, not four messages — and the total says so too.
        assert queue["total"] == 2
        assert [row["id"] for row in queue["items"]] == [b1, a3]
        folded = queue["items"][1]
        assert folded["conversation_count"] == 3
        assert folded["review_ids"] == [a1, a2, a3]
        # Who the thread is with: the representative's own first, then the rest, deduplicated.
        assert [p["email"] for p in folded["participants"]] == ["jan@client.nl", "piet@client.nl"]
        lone = queue["items"][0]
        assert lone["conversation_count"] == 1
        assert lone["review_ids"] == [b1]

        # The timeline: the logged history is its own row, never merged into a pending fold.
        everything = (await c.get("/api/v1/interactions", headers=headers)).json()
        assert everything["total"] == 3
        by_id = {row["id"]: row for row in everything["items"]}
        assert by_id[history]["conversation_count"] == 1
        assert by_id[history]["review_ids"] == []
        assert by_id[a3]["review_ids"] == [a1, a2, a3]

        # The single-row read answers the same fold the list does.
        one = (await c.get(f"/api/v1/interactions/{a2}", headers=headers)).json()
        assert one["conversation_count"] == 3
        assert one["review_ids"] == [a1, a2, a3]

    # A colleague's timeline holds only the logged row: a pending fold is its owner's alone.
    async with client_for(t.host) as c:
        colleague = await _member(c, headers, "collega@fold.example")
    colleague_headers = await auth_cookie(colleague)
    async with client_for(t.host) as c:
        theirs = (await c.get("/api/v1/interactions", headers=colleague_headers)).json()
        assert [row["id"] for row in theirs["items"]] == [history]


async def test_thread_of_a_pending_anchor_shows_history_and_own_pending(client_for) -> None:
    """``/thread`` on a pending row is its Gmail thread: the logged history anybody may read plus
    the caller's own waiting messages, newest first — never a colleague's pending copy."""
    t = await make_tenant("review-thread")
    owner_headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        colleague = await _member(c, owner_headers, "collega@thread.example")
    colleague_headers = await auth_cookie(colleague)

    history = await _seed(t, t.user.id, thread_id="thr-T", message_id="t0", occurred_at=_at(0))
    p1 = await _seed(
        t, t.user.id, thread_id="thr-T", message_id="t1", occurred_at=_at(1), pending=True
    )
    p2 = await _seed(
        t, t.user.id, thread_id="thr-T", message_id="t2", occurred_at=_at(2), pending=True
    )
    theirs = await _seed(
        t, colleague.id, thread_id="thr-T", message_id="t3", occurred_at=_at(3), pending=True
    )

    async with client_for(t.host) as c:
        thread = (await c.get(f"/api/v1/interactions/{p2}/thread", headers=owner_headers)).json()
        assert [row["id"] for row in thread] == [p2, p1, history]
        assert all(row["conversation_count"] == 3 for row in thread)
        # Every pending member names the same review set; the history names none.
        assert [row["review_ids"] for row in thread] == [[p1, p2], [p1, p2], []]

        # The colleague's own pending copy threads with the history and not with ours.
        mine = (
            await c.get(f"/api/v1/interactions/{theirs}/thread", headers=colleague_headers)
        ).json()
        assert [row["id"] for row in mine] == [theirs, history]
        # …and they cannot open ours at all.
        assert (
            await c.get(f"/api/v1/interactions/{p2}/thread", headers=colleague_headers)
        ).status_code == 404


async def test_approve_whole_thread_files_every_pending_message(client_for) -> None:
    """One approve with ``whole_thread`` logs the thread's other waiting messages with the same
    links; they fold into one conversation and the queue empties. Without it, the approve is
    the single-message act it always was."""
    t = await make_tenant("review-approve-thread")
    headers = await auth_cookie(t.user)
    p1 = await _seed(
        t, t.user.id, thread_id="thr-W", message_id="w1", occurred_at=_at(0), pending=True
    )
    p2 = await _seed(
        t, t.user.id, thread_id="thr-W", message_id="w2", occurred_at=_at(1), pending=True
    )
    p3 = await _seed(
        t, t.user.id, thread_id="thr-W", message_id="w3", occurred_at=_at(2), pending=True
    )
    other = await _seed(
        t, t.user.id, thread_id="thr-X", message_id="x1", occurred_at=_at(3), pending=True
    )

    async with client_for(t.host) as c:
        company = await _company(c, headers)
        res = await c.post(
            f"/api/v1/interactions/{p3}/approve",
            json={"company_id": company, "whole_thread": True},
            headers=headers,
        )
        assert res.status_code == 200, res.text
        approved = res.json()
        assert approved["status"] == "logged"
        assert approved["conversation_count"] == 3
        assert approved["review_ids"] == []

        for row_id in (p1, p2, p3):
            row = (await c.get(f"/api/v1/interactions/{row_id}", headers=headers)).json()
            assert row["status"] == "logged", row_id
            assert row["company_id"] == company, row_id
        conv = await _conversation_id(t.org.id, p3)
        assert conv is not None
        assert await _conversation_id(t.org.id, p1) == conv
        assert await _conversation_id(t.org.id, p2) == conv

        # The other thread was not touched: the queue holds exactly it.
        queue = (
            await c.get("/api/v1/interactions?status=pending&mine=true", headers=headers)
        ).json()
        assert [row["id"] for row in queue["items"]] == [other]
        # The timeline folds the approved thread to one row of three.
        page = (await c.get("/api/v1/interactions?status=logged", headers=headers)).json()
        assert [(row["id"], row["conversation_count"]) for row in page["items"]] == [(p3, 3)]

        # Control: the default approve names one message and leaves its thread alone.
        y1 = await _seed(
            t, t.user.id, thread_id="thr-Y", message_id="y1", occurred_at=_at(4), pending=True
        )
        y2 = await _seed(
            t, t.user.id, thread_id="thr-Y", message_id="y2", occurred_at=_at(5), pending=True
        )
        res = await c.post(f"/api/v1/interactions/{y2}/approve", headers=headers)
        assert res.status_code == 200, res.text
        still = (await c.get(f"/api/v1/interactions/{y1}", headers=headers)).json()
        assert still["status"] == "pending"
        assert still["review_ids"] == [y1]


async def test_reject_with_suppress_thread_rejects_the_pending_siblings(client_for) -> None:
    """Ignoring the conversation removes the thread's other waiting messages from the queue in
    the same step; its logged history stays, and a plain reject still takes one row."""
    t = await make_tenant("review-reject-thread")
    headers = await auth_cookie(t.user)
    history = await _seed(t, t.user.id, thread_id="thr-R", message_id="r0", occurred_at=_at(0))
    r1 = await _seed(
        t, t.user.id, thread_id="thr-R", message_id="r1", occurred_at=_at(1), pending=True
    )
    r2 = await _seed(
        t, t.user.id, thread_id="thr-R", message_id="r2", occurred_at=_at(2), pending=True
    )
    r3 = await _seed(
        t, t.user.id, thread_id="thr-R", message_id="r3", occurred_at=_at(3), pending=True
    )

    async with client_for(t.host) as c:
        # A plain reject: one message, the rest of the thread keeps waiting.
        res = await c.post(f"/api/v1/interactions/{r1}/reject", json={}, headers=headers)
        assert res.status_code in (200, 204), res.text
        queue = (
            await c.get("/api/v1/interactions?status=pending&mine=true", headers=headers)
        ).json()
        assert queue["items"][0]["review_ids"] == [r2, r3]

        res = await c.post(
            f"/api/v1/interactions/{r3}/reject",
            json={"suppress_thread": True},
            headers=headers,
        )
        assert res.status_code in (200, 204), res.text
        queue = (
            await c.get("/api/v1/interactions?status=pending&mine=true", headers=headers)
        ).json()
        assert queue["items"] == []
        assert (await c.get(f"/api/v1/interactions/{r2}", headers=headers)).status_code == 404
        # The team's record of the earlier, approved message is not this review's to remove.
        assert (await c.get(f"/api/v1/interactions/{history}", headers=headers)).status_code == 200
