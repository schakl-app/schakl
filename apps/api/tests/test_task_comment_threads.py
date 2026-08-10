"""Threaded comments (#312): replies, the one-level rule, and who hears about one."""

from __future__ import annotations

from tests.conftest import auth_cookie, make_tenant
from tests.test_notifications_emits import _inbox
from tests.test_notifications_fanout import _member


async def _task_with_comment(c, headers, title: str = "Brief") -> tuple[dict, dict]:
    task = (await c.post("/api/v1/tasks", json={"title": title}, headers=headers)).json()
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
        other = (await c.post("/api/v1/tasks", json={"title": "Mine"}, headers=headers)).json()
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
                json={"title": "Brief", "assignee_user_id": str(assignee.id)},
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
            await c.post("/api/v1/tasks", json={"title": "Brief"}, headers=owner_headers)
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
