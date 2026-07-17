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


async def test_task_reference_is_captured_and_org_scoped(client_for) -> None:
    """A #task reference (#197) is stored structurally; a cross-tenant id never validates."""
    t = await make_tenant("taskref")
    other = await make_tenant("taskref-other")
    owner_headers = await auth_cookie(t.user)
    other_headers = await auth_cookie(other.user)

    async with client_for(other.host) as c:
        foreign = (
            await c.post("/api/v1/tasks", json={"title": "Foreign"}, headers=other_headers)
        ).json()

    async with client_for(t.host) as c:
        host = (await c.post("/api/v1/tasks", json={"title": "Host"}, headers=owner_headers)).json()
        linked = (
            await c.post("/api/v1/tasks", json={"title": "Linked"}, headers=owner_headers)
        ).json()
        body = (
            f"see #[Linked](mention:task:{linked['id']}) "
            f"and #[Foreign](mention:task:{foreign['id']})"
        )
        comment = (
            await c.post(
                f"/api/v1/tasks/{host['id']}/comments", json={"body": body}, headers=owner_headers
            )
        ).json()
        # The in-org reference sticks; the other tenant's task id silently drops out.
        assert comment["mentioned_task_ids"] == [linked["id"]]
        # A reference is a cross-link, not a person: nothing to notify, nothing mentioned.
        assert comment["mentioned_user_ids"] == []


async def test_task_reference_edit_revalidates(client_for) -> None:
    """Editing a comment keeps the stored reference set in step with the body (#197)."""
    t = await make_tenant("taskref-edit")
    owner_headers = await auth_cookie(t.user)

    async with client_for(t.host) as c:
        host = (await c.post("/api/v1/tasks", json={"title": "Host"}, headers=owner_headers)).json()
        linked = (
            await c.post("/api/v1/tasks", json={"title": "Linked"}, headers=owner_headers)
        ).json()
        comment = (
            await c.post(
                f"/api/v1/tasks/{host['id']}/comments",
                json={"body": f"see #[Linked](mention:task:{linked['id']})"},
                headers=owner_headers,
            )
        ).json()
        assert comment["mentioned_task_ids"] == [linked["id"]]
        edited = (
            await c.patch(
                f"/api/v1/tasks/{host['id']}/comments/{comment['id']}",
                json={"body": "plain text now"},
                headers=owner_headers,
            )
        ).json()
        assert edited["mentioned_task_ids"] == []
