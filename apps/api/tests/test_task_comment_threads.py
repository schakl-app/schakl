"""Threaded comments (#312): replies, the one-level rule, and who hears about one."""

from __future__ import annotations

from tests.conftest import FAR_FUTURE_DUE, auth_cookie, default_company, make_tenant
from tests.test_notifications_emits import _inbox
from tests.test_notifications_fanout import _member


async def _task_with_comment(c, headers, title: str = "Brief") -> tuple[dict, dict]:
    task = (await c.post(
        "/api/v1/tasks",
        json={
            "company_id": await default_company(c, headers),
            "due_date": FAR_FUTURE_DUE, "title": title,
        },
        headers=headers,
    )).json()
    root = (
        await c.post(
            f"/api/v1/tasks/{task['id']}/comments",
            json={"body": "Opening question"},
            headers=headers,
        )
    ).json()
    return task, root


async def test_a_reply_records_its_parent(client_for) -> None:
    t = await make_tenant("thread-basic")
    headers = await auth_cookie(t.user)

    async with client_for(t.host) as c:
        task, root = await _task_with_comment(c, headers)
        reply = (
            await c.post(
                f"/api/v1/tasks/{task['id']}/comments",
                json={"body": "The answer", "parent_id": root["id"]},
                headers=headers,
            )
        ).json()
        assert reply["parent_id"] == root["id"]
        assert root["parent_id"] is None

        detail = (await c.get(f"/api/v1/tasks/{task['id']}", headers=headers)).json()
        assert [x["id"] for x in detail["comments"]] == [root["id"], reply["id"]]
        # The trail distinguishes the two, and links to the answer rather than to the thread.
        replied = [a for a in detail["activities"] if a["action"] == "replied"]
        assert len(replied) == 1
        assert replied[0]["payload"]["comment_id"] == reply["id"]
        assert replied[0]["payload"]["parent_id"] == root["id"]


async def test_a_reply_to_a_reply_is_re_rooted_not_refused(client_for) -> None:
    """Threads are one level deep — the second answer joins the same conversation."""
    t = await make_tenant("thread-flat")
    headers = await auth_cookie(t.user)

    async with client_for(t.host) as c:
        task, root = await _task_with_comment(c, headers)
        first = (
            await c.post(
                f"/api/v1/tasks/{task['id']}/comments",
                json={"body": "One", "parent_id": root["id"]},
                headers=headers,
            )
        ).json()
        second = (
            await c.post(
                f"/api/v1/tasks/{task['id']}/comments",
                json={"body": "Two", "parent_id": first["id"]},
                headers=headers,
            )
        ).json()
        assert second["parent_id"] == root["id"]


async def test_a_parent_on_another_task_is_a_404(client_for) -> None:
    """A wrong id is a wrong id — re-rooting only fixes a reading-order problem."""
    t = await make_tenant("thread-cross-task")
    headers = await auth_cookie(t.user)

    async with client_for(t.host) as c:
        _, elsewhere = await _task_with_comment(c, headers, title="Other")
        other = (await c.post(
            "/api/v1/tasks",
            json={
                "company_id": await default_company(c, headers),
                "due_date": FAR_FUTURE_DUE, "title": "Mine",
            },
            headers=headers,
        )).json()
        refused = await c.post(
            f"/api/v1/tasks/{other['id']}/comments",
            json={"body": "Nope", "parent_id": elsewhere["id"]},
            headers=headers,
        )
        assert refused.status_code == 404


async def test_the_detail_read_keeps_a_thread_together(client_for) -> None:
    """A reply sits under its opener even when newer threads were started after it."""
    t = await make_tenant("thread-order")
    headers = await auth_cookie(t.user)

    async with client_for(t.host) as c:
        task, first = await _task_with_comment(c, headers)
        second = (
            await c.post(
                f"/api/v1/tasks/{task['id']}/comments",
                json={"body": "A separate point"},
                headers=headers,
            )
        ).json()
        late_reply = (
            await c.post(
                f"/api/v1/tasks/{task['id']}/comments",
                json={"body": "Late answer to the first", "parent_id": first["id"]},
                headers=headers,
            )
        ).json()

        detail = (await c.get(f"/api/v1/tasks/{task['id']}", headers=headers)).json()
        # Chronologically the late reply is last; by thread it belongs beside what it answers.
        assert [x["id"] for x in detail["comments"]] == [
            first["id"],
            late_reply["id"],
            second["id"],
        ]


async def test_deleting_a_thread_opener_takes_its_replies(client_for) -> None:
    t = await make_tenant("thread-delete")
    headers = await auth_cookie(t.user)

    async with client_for(t.host) as c:
        task, root = await _task_with_comment(c, headers)
        for body in ("One", "Two"):
            await c.post(
                f"/api/v1/tasks/{task['id']}/comments",
                json={"body": body, "parent_id": root["id"]},
                headers=headers,
            )
        gone = await c.delete(
            f"/api/v1/tasks/{task['id']}/comments/{root['id']}", headers=headers
        )
        assert gone.status_code == 204

        detail = (await c.get(f"/api/v1/tasks/{task['id']}", headers=headers)).json()
        assert detail["comments"] == []
        # The trail says how many words went with it, or the conversation vanishes silently.
        deleted = [a for a in detail["activities"] if a["action"] == "comment_deleted"]
        assert deleted[0]["payload"]["replies"] == 2


async def test_deleting_a_reply_leaves_the_thread_standing(client_for) -> None:
    t = await make_tenant("thread-delete-reply")
    headers = await auth_cookie(t.user)

    async with client_for(t.host) as c:
        task, root = await _task_with_comment(c, headers)
        reply = (
            await c.post(
                f"/api/v1/tasks/{task['id']}/comments",
                json={"body": "Answer", "parent_id": root["id"]},
                headers=headers,
            )
        ).json()
        await c.delete(f"/api/v1/tasks/{task['id']}/comments/{reply['id']}", headers=headers)

        detail = (await c.get(f"/api/v1/tasks/{task['id']}", headers=headers)).json()
        assert [x["id"] for x in detail["comments"]] == [root["id"]]
        deleted = [a for a in detail["activities"] if a["action"] == "comment_deleted"]
        # No cascade happened, so no count is claimed.
        assert "replies" not in deleted[0]["payload"]


async def test_an_edit_cannot_move_a_reply_between_threads(client_for) -> None:
    t = await make_tenant("thread-edit")
    headers = await auth_cookie(t.user)

    async with client_for(t.host) as c:
        task, root = await _task_with_comment(c, headers)
        reply = (
            await c.post(
                f"/api/v1/tasks/{task['id']}/comments",
                json={"body": "Answer", "parent_id": root["id"]},
                headers=headers,
            )
        ).json()
        other_root = (
            await c.post(
                f"/api/v1/tasks/{task['id']}/comments", json={"body": "Elsewhere"}, headers=headers
            )
        ).json()
        edited = await c.patch(
            f"/api/v1/tasks/{task['id']}/comments/{reply['id']}",
            json={"body": "Reworded", "parent_id": other_root["id"]},
            headers=headers,
        )
        assert edited.status_code == 200
        # `parent_id` is not part of the update schema, so the extra key changes nothing.
        assert edited.json()["parent_id"] == root["id"]


async def test_the_thread_hears_replied_and_everyone_else_hears_commented(client_for) -> None:
    """One write, one sentence per recipient: answered, or told a task was commented on."""
    t = await make_tenant("thread-fanout")
    in_thread = await _member(t, "in-thread@example.com")
    assignee = await _member(t, "assignee@example.com")
    owner_headers = await auth_cookie(t.user)
    thread_headers = await auth_cookie(in_thread)

    async with client_for(t.host) as c:
        task = (
            await c.post(
                "/api/v1/tasks",
                json={
                    "company_id": await default_company(c, owner_headers),
                    "due_date": FAR_FUTURE_DUE,
                    "title": "Brief",
                    "assignee_user_id": str(assignee.id),
                },
                headers=owner_headers,
            )
        ).json()
        root = (
            await c.post(
                f"/api/v1/tasks/{task['id']}/comments",
                json={"body": "A question"},
                headers=thread_headers,
            )
        ).json()
        await c.post(
            f"/api/v1/tasks/{task['id']}/comments",
            json={"body": "An answer", "parent_id": root["id"]},
            headers=owner_headers,
        )

    # Whoever opened the thread is being answered.
    thread_events = {et for et, _ in await _inbox(t, in_thread.id)}
    assert "task.replied" in thread_events
    assert "task.commented" not in thread_events
    # The assignee, who has not written in it, hears the ordinary comment and nothing else.
    assignee_events = {et for et, _ in await _inbox(t, assignee.id)}
    assert "task.commented" in assignee_events
    assert "task.replied" not in assignee_events


async def test_a_mention_in_a_reply_still_wins(client_for) -> None:
    """Mention > reply > comment: a recipient hears exactly the most specific one."""
    t = await make_tenant("thread-mention")
    opener = await _member(t, "opener@example.com")
    owner_headers = await auth_cookie(t.user)
    opener_headers = await auth_cookie(opener)

    async with client_for(t.host) as c:
        task = (
            await c.post(
                "/api/v1/tasks",
                json={
                    "company_id": await default_company(c, owner_headers),
                    "due_date": FAR_FUTURE_DUE, "title": "Brief",
                },
                headers=owner_headers,
            )
        ).json()
        root = (
            await c.post(
                f"/api/v1/tasks/{task['id']}/comments",
                json={"body": "A question"},
                headers=opener_headers,
            )
        ).json()
        await c.post(
            f"/api/v1/tasks/{task['id']}/comments",
            json={
                "body": f"@[Opener](mention:{opener.id}) here you go",
                "parent_id": root["id"],
            },
            headers=owner_headers,
        )

    events = {et for et, _ in await _inbox(t, opener.id)}
    assert "task.mentioned" in events
    assert "task.replied" not in events
    assert "task.commented" not in events


async def test_every_comment_notification_names_the_comment(client_for) -> None:
    """A sentence about a comment carries the comment (#312 follow-up).

    "Jan reageerde op Productfeed opschonen" used to open the task and nothing more, so on a task
    people have been talking on for a year the reader arrived at the top of fifty messages and had
    to find the new ones. Both destinations read this: the web inbox through
    ``notifications/href.ts`` and the mail's button through ``notifications/render.event_path``.
    ``thread_id`` rides along because the answer is not the thread — the card has to unfold the
    right conversation before it can scroll to the message inside it.
    """
    t = await make_tenant("thread-deeplink")
    opener = await _member(t, "opener@example.com")
    watcher = await _member(t, "watcher@example.com")
    owner_headers = await auth_cookie(t.user)
    opener_headers = await auth_cookie(opener)

    async with client_for(t.host) as c:
        task = (
            await c.post(
                "/api/v1/tasks",
                json={
                    "company_id": await default_company(c, owner_headers),
                    "due_date": FAR_FUTURE_DUE,
                    "title": "Brief",
                    "assignee_user_id": str(watcher.id),
                },
                headers=owner_headers,
            )
        ).json()
        root = (
            await c.post(
                f"/api/v1/tasks/{task['id']}/comments",
                json={"body": "A question"},
                headers=opener_headers,
            )
        ).json()
        reply = (
            await c.post(
                f"/api/v1/tasks/{task['id']}/comments",
                json={"body": "The answer", "parent_id": root["id"]},
                headers=owner_headers,
            )
        ).json()

    # The person being answered, and the assignee merely told the task was commented on, are
    # both sent to the *answer* — and both are told which conversation it landed in.
    for user_id, expected in ((opener.id, "task.replied"), (watcher.id, "task.commented")):
        rows = [(et, p) for et, p in await _inbox(t, user_id) if et == expected]
        assert rows, f"{expected} never reached {user_id}"
        payload = rows[-1][1]
        assert payload["comment_id"] == reply["id"]
        assert payload["thread_id"] == root["id"]


async def test_a_capped_conversation_says_that_it_is_capped(client_for) -> None:
    """A short answer that looks complete is the failure this flag exists to prevent.

    The card takes the newest 200 comments and said nothing about the rest, so a task with two
    hundred messages and one with nine hundred rendered identically (CLAUDE.md §17). It costs no
    second query: the read asks for one row more than it keeps.
    """
    t = await make_tenant("thread-cap")
    headers = await auth_cookie(t.user)

    async with client_for(t.host) as c:
        task = (await c.post(
            "/api/v1/tasks",
            json={
                "company_id": await default_company(c, headers),
                "due_date": FAR_FUTURE_DUE, "title": "Brief",
            },
            headers=headers,
        )).json()
        detail = (await c.get(f"/api/v1/tasks/{task['id']}", headers=headers)).json()
        assert detail["comments_truncated"] is False

        from app.modules.tasks import service as task_service

        original = task_service._COMMENT_CAP
        task_service._COMMENT_CAP = 3
        try:
            for n in range(4):
                await c.post(
                    f"/api/v1/tasks/{task['id']}/comments",
                    json={"body": f"Message {n}"},
                    headers=headers,
                )
            detail = (await c.get(f"/api/v1/tasks/{task['id']}", headers=headers)).json()
        finally:
            task_service._COMMENT_CAP = original

        assert detail["comments_truncated"] is True
        # The *newest* three survive, and the surplus row read to answer the question is dropped
        # from the front rather than served as a 201st comment.
        assert [x["body"] for x in detail["comments"]] == ["Message 1", "Message 2", "Message 3"]
