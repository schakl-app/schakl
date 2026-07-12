"""REST surface for ``/api/v1/notifications`` (issue #16).

An inbox is personal: every route serves the caller's own rows, so the tests below check the
*absence* of any way to read someone else's — across a tenant boundary and within one org.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.auth.models import User
from app.core.events import emit
from app.db import async_session_maker, set_current_org
from app.modules.notifications.models import Notification
from tests.conftest import Tenant, auth_cookie, make_tenant
from tests.test_notifications_fanout import _ctx, _member


async def _notify(tenant: Tenant, actor: User, recipient: User, **overrides) -> uuid.UUID:
    """Deliver one immediate notification to ``recipient`` and return the task id."""
    task_id = overrides.pop("task_id", None) or uuid.uuid4()
    payload = {"task_id": task_id, "_recipients": [recipient.id], "title": "Write the docs"}
    payload.update(overrides)
    async with _ctx(tenant, actor) as ctx:
        await emit("task.assigned", ctx, payload)
    return task_id


async def test_inbox_list_count_and_read_toggle(client_for) -> None:
    t = await make_tenant("notif-api-inbox")
    member = await _member(t, "inbox@example.com")
    headers = await auth_cookie(member)
    await _notify(t, t.user, member)

    async with client_for(t.host) as client:
        listing = await client.get("/api/v1/notifications", headers=headers)
        assert listing.status_code == 200
        body = listing.json()
        assert body["total"] == 1
        item = body["items"][0]
        assert item["event_type"] == "task.assigned"
        assert item["entity_type"] == "task"
        assert item["payload"]["title"] == "Write the docs"
        assert item["actor_name"] == t.user.email  # no full_name → the email names them
        assert item["read_at"] is None

        assert (await client.get("/api/v1/notifications/unread-count", headers=headers)).json()[
            "count"
        ] == 1

        # Reversible: read, then unread again (docs/UX.md — a toggle destroys nothing).
        marked = await client.patch(
            f"/api/v1/notifications/{item['id']}", json={"read": True}, headers=headers
        )
        assert marked.status_code == 200 and marked.json()["read_at"] is not None
        assert (await client.get("/api/v1/notifications/unread-count", headers=headers)).json()[
            "count"
        ] == 0

        unmarked = await client.patch(
            f"/api/v1/notifications/{item['id']}", json={"read": False}, headers=headers
        )
        assert unmarked.json()["read_at"] is None
        assert (await client.get("/api/v1/notifications/unread-count", headers=headers)).json()[
            "count"
        ] == 1


async def test_unread_filter_and_mark_all_read(client_for) -> None:
    t = await make_tenant("notif-api-markall")
    member = await _member(t, "markall@example.com")
    headers = await auth_cookie(member)
    await _notify(t, t.user, member)
    await _notify(t, t.user, member)

    async with client_for(t.host) as client:
        unread = await client.get(
            "/api/v1/notifications", params={"unread": True}, headers=headers
        )
        assert unread.json()["total"] == 2

        result = await client.post("/api/v1/notifications/mark-all-read", headers=headers)
        assert result.status_code == 200 and result.json()["updated"] == 2

        assert (await client.get("/api/v1/notifications/unread-count", headers=headers)).json()[
            "count"
        ] == 0
        read_only = await client.get(
            "/api/v1/notifications", params={"unread": False}, headers=headers
        )
        assert read_only.json()["total"] == 2


async def test_a_pending_digest_row_is_neither_listed_nor_counted(client_for) -> None:
    """``visible_at`` in the future *is* the digest: the row exists, the bell stays quiet."""
    t = await make_tenant("notif-api-pending")
    member = await _member(t, "pending@example.com")
    headers = await auth_cookie(member)
    # task.commented defaults to the daily digest, so this lands at tomorrow's 08:00.
    async with _ctx(t, t.user) as ctx:
        await emit(
            "task.commented",
            ctx,
            {"task_id": uuid.uuid4(), "_recipients": [member.id], "excerpt": "later"},
        )

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        row = (await session.execute(select(Notification))).scalars().one()
        assert row.visible_at > datetime.now(UTC)

    async with client_for(t.host) as client:
        assert (await client.get("/api/v1/notifications", headers=headers)).json()["total"] == 0
        assert (await client.get("/api/v1/notifications/unread-count", headers=headers)).json()[
            "count"
        ] == 0

    # Once its moment arrives, the same row surfaces — no synthetic digest row was needed.
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        row = (await session.execute(select(Notification))).scalars().one()
        row.visible_at = datetime.now(UTC) - timedelta(minutes=1)
        await session.commit()

    async with client_for(t.host) as client:
        assert (await client.get("/api/v1/notifications/unread-count", headers=headers)).json()[
            "count"
        ] == 1


async def test_count_opt_out_skips_the_count_query(client_for) -> None:
    t = await make_tenant("notif-api-nocount")
    member = await _member(t, "nocount@example.com")
    headers = await auth_cookie(member)
    await _notify(t, t.user, member)

    async with client_for(t.host) as client:
        body = (
            await client.get(
                "/api/v1/notifications", params={"count": False}, headers=headers
            )
        ).json()
        assert len(body["items"]) == 1
        assert body["total"] == 0  # deliberately not computed (docs/PERFORMANCE.md)


async def test_activity_feed_is_recipient_independent(client_for) -> None:
    """The feed shows what happened to a record, even to someone nobody notified."""
    t = await make_tenant("notif-api-activity")
    member = await _member(t, "activity@example.com")
    bystander = await _member(t, "bystander@example.com")
    task_id = await _notify(t, t.user, member)
    bystander_headers = await auth_cookie(bystander)

    async with client_for(t.host) as client:
        feed = await client.get(
            "/api/v1/notifications/activity",
            params={"entity_type": "task", "entity_id": str(task_id)},
            headers=bystander_headers,
        )
        assert feed.status_code == 200
        items = feed.json()
        assert len(items) == 1
        assert items[0]["event_type"] == "task.assigned"
        assert items[0]["actor_name"] == t.user.email

        # An unknown entity type is rejected rather than silently returning nothing.
        bad = await client.get(
            "/api/v1/notifications/activity",
            params={"entity_type": "nonsense", "entity_id": str(task_id)},
            headers=bystander_headers,
        )
        assert bad.status_code == 422


async def test_watch_endpoint_is_tri_state(client_for) -> None:
    t = await make_tenant("notif-api-watch")
    member = await _member(t, "watch@example.com")
    headers = await auth_cookie(member)
    entity_id = str(uuid.uuid4())
    params = {"entity_type": "project", "entity_id": entity_id}

    async with client_for(t.host) as client:
        assert (
            await client.get("/api/v1/notifications/watch", params=params, headers=headers)
        ).json()["watching"] is None

        for watching in (True, False):
            put = await client.put(
                "/api/v1/notifications/watch",
                json={**params, "watching": watching},
                headers=headers,
            )
            assert put.json()["watching"] is watching
            assert (
                await client.get("/api/v1/notifications/watch", params=params, headers=headers)
            ).json()["watching"] is watching

        # null clears the row and restores the default fan-out.
        cleared = await client.put(
            "/api/v1/notifications/watch", json={**params, "watching": None}, headers=headers
        )
        assert cleared.json()["watching"] is None


async def test_preferences_resolve_default_then_org_then_user(client_for) -> None:
    t = await make_tenant("notif-api-prefs")
    member = await _member(t, "prefs@example.com")
    owner_headers = await auth_cookie(t.user)
    member_headers = await auth_cookie(member)

    def row(matrix: dict, event_type: str) -> dict:
        return next(r for r in matrix["events"] if r["event_type"] == event_type)

    async with client_for(t.host) as client:

        async def my_matrix() -> dict:
            res = await client.get("/api/v1/notifications/preferences", headers=member_headers)
            return res.json()

        # 1. Nothing set anywhere: the hardcoded default decides, and says so.
        mine = await my_matrix()
        assert row(mine, "task.assigned")["digest"] == "immediate"
        assert row(mine, "task.assigned")["source"] == "default"
        assert row(mine, "task.commented")["digest"] == "daily"
        assert mine["general"]["due_soon_days"] == 3

        # 2. The org curates a default: the member inherits it, badged as inherited.
        await client.put(
            "/api/v1/notifications/preferences/defaults",
            json={
                "events": [{"event_type": "task.commented", "enabled": False,
                            "digest": "immediate"}],
                "general": {"due_soon_days": 7},
            },
            headers=owner_headers,
        )
        mine = await my_matrix()
        assert row(mine, "task.commented")["enabled"] is False
        assert row(mine, "task.commented")["source"] == "org"
        assert mine["general"]["due_soon_days"] == 7
        assert mine["general"]["source"] == "org"

        # 3. The member overrides it for themselves; the org default is untouched.
        updated = (
            await client.put(
                "/api/v1/notifications/preferences",
                json={
                    "events": [{"event_type": "task.commented", "enabled": True,
                                "digest": "weekly", "digest_weekday": 2,
                                "digest_time": "09:30:00"}],
                    "general": {"due_soon_days": 1},
                },
                headers=member_headers,
            )
        ).json()
        assert row(updated, "task.commented")["source"] == "user"
        assert row(updated, "task.commented")["digest"] == "weekly"
        assert row(updated, "task.commented")["digest_weekday"] == 2
        assert updated["general"]["due_soon_days"] == 1

        # Untouched events still fall through to the layers beneath.
        assert row(updated, "task.assigned")["source"] == "default"
        defaults = (
            await client.get("/api/v1/notifications/preferences/defaults", headers=owner_headers)
        ).json()
        assert row(defaults, "task.commented")["enabled"] is False

        # 4. A PUT replaces wholesale: dropping the row restores what is inherited.
        reset = (
            await client.put(
                "/api/v1/notifications/preferences", json={"events": []}, headers=member_headers
            )
        ).json()
        assert row(reset, "task.commented")["source"] == "org"
        assert reset["general"]["due_soon_days"] == 7


async def test_preferences_reject_an_unknown_event_or_cadence(client_for) -> None:
    t = await make_tenant("notif-api-prefs-bad")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as client:
        for body in (
            {"events": [{"event_type": "task.exploded"}]},
            {"events": [{"event_type": "task.assigned", "digest": "fortnightly"}]},
            {"events": [{"event_type": "task.assigned"}, {"event_type": "task.assigned"}]},
        ):
            res = await client.put(
                "/api/v1/notifications/preferences", json=body, headers=headers
            )
            assert res.status_code == 422, body


async def test_org_defaults_are_manager_only(client_for) -> None:
    t = await make_tenant("notif-api-prefs-gate")
    member = await _member(t, "grunt@example.com")
    headers = await auth_cookie(member)

    async with client_for(t.host) as client:
        assert (
            await client.get("/api/v1/notifications/preferences/defaults", headers=headers)
        ).status_code == 403
        assert (
            await client.put(
                "/api/v1/notifications/preferences/defaults",
                json={"events": []},
                headers=headers,
            )
        ).status_code == 403


async def test_an_inbox_is_private_to_its_owner(client_for) -> None:
    """Two members of the same org cannot touch each other's rows."""
    t = await make_tenant("notif-api-private")
    alice = await _member(t, "alice@example.com")
    bob = await _member(t, "bob@example.com")
    await _notify(t, t.user, alice)

    async with client_for(t.host) as client:
        alice_headers = await auth_cookie(alice)
        bob_headers = await auth_cookie(bob)
        item = (await client.get("/api/v1/notifications", headers=alice_headers)).json()["items"][0]

        assert (await client.get("/api/v1/notifications", headers=bob_headers)).json()["total"] == 0
        # Bob cannot read Alice's row by guessing its id.
        stolen = await client.patch(
            f"/api/v1/notifications/{item['id']}", json={"read": True}, headers=bob_headers
        )
        assert stolen.status_code == 404
        # …and Alice's row is still unread.
        assert (
            await client.get("/api/v1/notifications/unread-count", headers=alice_headers)
        ).json()["count"] == 1


async def test_notifications_tenant_isolation(client_for) -> None:
    a = await make_tenant("notif-iso-a")
    b = await make_tenant("notif-iso-b")
    a_member = await _member(a, "a-member@example.com")
    task_id = await _notify(a, a.user, a_member)

    a_headers = await auth_cookie(a_member)
    b_headers = await auth_cookie(b.user)

    async with client_for(a.host) as client:
        item = (await client.get("/api/v1/notifications", headers=a_headers)).json()["items"][0]

    async with client_for(b.host) as client:
        # B's session on B's host can never reach A's rows.
        assert (await client.get("/api/v1/notifications", headers=b_headers)).json()["total"] == 0
        assert (
            await client.patch(
                f"/api/v1/notifications/{item['id']}", json={"read": True}, headers=b_headers
            )
        ).status_code == 404
        assert (
            await client.get(
                "/api/v1/notifications/activity",
                params={"entity_type": "task", "entity_id": str(task_id)},
                headers=b_headers,
            )
        ).json() == []
        # A's session on B's host is not a member there.
        assert (
            await client.get("/api/v1/notifications", headers=a_headers)
        ).status_code == 403


async def test_email_pending_is_a_first_class_matrix_event(client_for) -> None:
    """#146: interactions.email_pending sits in the preference matrix like any event —
    immediate by default (a review queue is not tomorrow's digest) and user-tunable."""
    t = await make_tenant("notif-email-pending")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as client:
        matrix = (
            await client.get("/api/v1/notifications/preferences", headers=headers)
        ).json()
        row = next(
            r for r in matrix["events"] if r["event_type"] == "interactions.email_pending"
        )
        assert row["digest"] == "immediate" and row["source"] == "default"

        # And it can be retuned like any other row.
        saved = await client.put(
            "/api/v1/notifications/preferences",
            json={
                "events": [
                    {
                        "event_type": "interactions.email_pending",
                        "enabled": True,
                        "delay_minutes": 0,
                        "digest": "daily",
                    }
                ]
            },
            headers=headers,
        )
        assert saved.status_code == 200, saved.text
        matrix = (
            await client.get("/api/v1/notifications/preferences", headers=headers)
        ).json()
        row = next(
            r for r in matrix["events"] if r["event_type"] == "interactions.email_pending"
        )
        assert row["digest"] == "daily" and row["source"] == "user"
