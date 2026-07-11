"""@mentions in task comments (issue #63): structural capture + its own notification."""

from __future__ import annotations

from tests.conftest import auth_cookie, make_tenant
from tests.test_notifications_emits import _inbox
from tests.test_notifications_fanout import _member


async def test_mention_notifies_a_non_participant(client_for) -> None:
    """A mentioned member who is neither assignee nor prior commenter still gets told."""
    t = await make_tenant("mention-notify")
    member = await _member(t, "mentioned@example.com")
    owner_headers = await auth_cookie(t.user)

    async with client_for(t.host) as c:
        made = await c.post("/api/v1/tasks", json={"title": "Brief"}, headers=owner_headers)
        task = made.json()
        body = f"cc @[Mentioned](mention:{member.id}) please review"
        comment = (
            await c.post(
                f"/api/v1/tasks/{task['id']}/comments", json={"body": body}, headers=owner_headers
            )
        ).json()
        # The mention is captured structurally, not left to be re-parsed on render.
        assert comment["mentioned_user_ids"] == [str(member.id)]

    # task.mentioned is immediate, so it lands in the member's inbox straight away.
    inbox = await _inbox(t, member.id)
    assert any(event_type == "task.mentioned" for event_type, _ in inbox)


async def test_mention_reads_as_its_own_event_not_a_comment(client_for) -> None:
    """A mentioned user gets task.mentioned; they are dropped from the generic commented fan-out."""
    t = await make_tenant("mention-distinct")
    assignee = await _member(t, "assignee@example.com")
    mentioned = await _member(t, "pulled-in@example.com")
    owner_headers = await auth_cookie(t.user)

    async with client_for(t.host) as c:
        task = (
            await c.post(
                "/api/v1/tasks",
                json={"title": "Brief", "assignee_user_id": str(assignee.id)},
                headers=owner_headers,
            )
        ).json()
        body = f"@[Pulled In](mention:{mentioned.id})"
        await c.post(
            f"/api/v1/tasks/{task['id']}/comments", json={"body": body}, headers=owner_headers
        )

    # The mentioned user hears "mentioned you", never the generic "commented".
    mentioned_events = {et for et, _ in await _inbox(t, mentioned.id)}
    assert "task.mentioned" in mentioned_events
    assert "task.commented" not in mentioned_events
    # The assignee, who was not mentioned, still hears the ordinary comment.
    assignee_events = {et for et, _ in await _inbox(t, assignee.id)}
    assert "task.commented" in assignee_events


async def test_foreign_mention_id_is_ignored(client_for) -> None:
    """An id that isn't a member of this org can't be used to notify across tenants."""
    t = await make_tenant("mention-foreign")
    other = await make_tenant("mention-foreign-other")
    owner_headers = await auth_cookie(t.user)

    async with client_for(t.host) as c:
        task = (await c.post("/api/v1/tasks", json={"title": "T"}, headers=owner_headers)).json()
        body = f"@[Outsider](mention:{other.user.id})"
        comment = (
            await c.post(
                f"/api/v1/tasks/{task['id']}/comments", json={"body": body}, headers=owner_headers
            )
        ).json()
        assert comment["mentioned_user_ids"] == []
