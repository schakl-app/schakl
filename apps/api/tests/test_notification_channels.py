"""External notification channels via Apprise (#17): admin-only CRUD, encryption, SSRF, fan-out.

Since #295 a **shared room is routed exactly like a personal channel** — per event, from the
matrix of the scope that owns it, which for a room is the org defaults. So every fan-out test
here connects the channel *and then routes something to it*; a connected-but-unrouted room is
silent, and that is asserted rather than assumed.

No network is touched here: named providers (``slack://``) skip host resolution, and the SSRF
case uses a literal private IP so no DNS is needed. Delivery *dispatch* (the provider call) is the
worker's job and is not exercised.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.auth.models import User
from app.core.models import Org
from app.db import async_session_maker, set_current_org
from app.modules.notifications import external
from app.modules.notifications.models import NotificationChannelConfig, NotificationDelivery
from tests.conftest import auth_cookie, leave_workday, make_tenant

_SLACK = "slack://xoxb-abc-def/#crm"
_DISCORD = "discord://123456/tok-en"


async def _leave_request(client, headers, owner_headers, offset: int = 0) -> None:
    """Fire one notifiable event: a member's leave request notifies the approver."""
    types = (await client.get("/api/v1/leave/types", headers=owner_headers)).json()
    special = next(x["id"] for x in types if x["key"] == "special")
    start = leave_workday(offset)
    res = await client.post(
        "/api/v1/leave/requests",
        json={
            "leave_type_id": special,
            "start_date": start.isoformat(),
            "end_date": start.isoformat(),
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text


async def _sweep(org_id: uuid.UUID) -> None:
    """Run the worker's external sweep for one org, exactly as the cron does."""
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        org = await session.get(Org, org_id)
        await external.dispatch_external_deliveries(session, org)
        await session.commit()


async def _deliveries(org_id: uuid.UUID) -> list[NotificationDelivery]:
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        return list(
            (
                await session.execute(
                    select(NotificationDelivery)
                    .where(
                        NotificationDelivery.org_id == org_id,
                        NotificationDelivery.channel == "external",
                    )
                    .order_by(NotificationDelivery.created_at.asc())
                )
            )
            .scalars()
            .all()
        )


async def _member(client, headers, email: str) -> User:
    res = await client.post(
        "/api/v1/members/invite",
        json={"email": email, "full_name": "M", "role": "member"},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return User(
        id=uuid.UUID(res.json()["user_id"]), email=email, hashed_password="", is_active=True
    )


async def _route(client, headers, routing: dict[str, dict[str, str]]) -> dict:  # noqa: ANN001
    """Route shared rooms from the org-default matrix, as Instellingen → Standaard meldingen does.

    ``routing`` is ``{channel id: {event type: cadence}}``. Wholesale like every block in this
    body, so every room the test cares about goes in one call — sending one channel would clear
    the others, exactly as the form always posts every column.
    """
    res = await client.put(
        "/api/v1/notifications/preferences/defaults",
        json={
            "channels": [
                {
                    "channel_config_id": channel_id,
                    "events": [
                        {"event_type": event, "enabled": True, "digest": cadence}
                        for event, cadence in events.items()
                    ],
                }
                for channel_id, events in routing.items()
            ]
        },
        headers=headers,
    )
    assert res.status_code == 200, res.text
    return res.json()


async def _connect_shared(client, headers, name: str = "Team", url: str = _SLACK) -> str:  # noqa: ANN001
    """Connect a shared room (no ``user_id``) and return its id."""
    res = await client.post(
        "/api/v1/notifications/channels",
        json={"kind": "slack" if url == _SLACK else "discord", "name": name, "url": url},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    assert res.json()["user_id"] is None
    return res.json()["id"]


async def test_channel_crud_encrypts_and_redacts(client_for) -> None:
    t = await make_tenant("chan-crud")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        created = await c.post(
            "/api/v1/notifications/channels",
            json={"kind": "slack", "name": "Team", "url": _SLACK},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert "url" not in body  # the secret-bearing URL is never returned
        assert body["redacted"].startswith("slack://")
        assert "xoxb-abc-def" not in body["redacted"]
        cid = body["id"]

        listed = await c.get("/api/v1/notifications/channels", headers=headers)
        assert len(listed.json()) == 1

        updated = await c.patch(
            f"/api/v1/notifications/channels/{cid}",
            json={"enabled": False},
            headers=headers,
        )
        assert updated.status_code == 200
        assert updated.json()["enabled"] is False

    # The URL is encrypted at rest — the raw column is not the plaintext.
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        row = await session.scalar(
            select(NotificationChannelConfig).where(NotificationChannelConfig.id == uuid.UUID(cid))
        )
        assert row.url_enc != _SLACK
        assert "xoxb" not in row.url_enc


async def test_ssrf_blocks_private_webhook(client_for) -> None:
    t = await make_tenant("chan-ssrf")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        res = await c.post(
            "/api/v1/notifications/channels",
            json={"kind": "webhook", "name": "internal", "url": "json://10.0.0.1/hook"},
            headers=headers,
        )
        assert res.status_code == 422
        assert "notification_channel_blocked" in res.text


async def test_member_creating_a_channel_only_ever_gets_their_own(client_for) -> None:
    """``manage_own`` (#283): a member connects their own transport, never an org one."""
    t = await make_tenant("chan-rbac")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, owner, "m@example.com")
        mh = await auth_cookie(member)
        # No ``user_id`` in the body, and asking for an org channel outright: both land on
        # the caller, because a member has no way to mean anything else.
        mine = await c.post(
            "/api/v1/notifications/channels",
            json={"kind": "slack", "name": "My DM", "url": _SLACK},
            headers=mh,
        )
        assert mine.status_code == 201, mine.text
        assert mine.json()["user_id"] == str(member.id)

        forced = await c.post(
            "/api/v1/notifications/channels",
            json={"kind": "slack", "name": "Sneaky", "url": _SLACK, "user_id": None},
            headers=mh,
        )
        assert forced.status_code == 201
        assert forced.json()["user_id"] == str(member.id)

        # An admin still owns the org's shared rooms.
        shared = await c.post(
            "/api/v1/notifications/channels",
            json={"kind": "slack", "name": "#crm", "url": _SLACK},
            headers=owner,
        )
        assert shared.status_code == 201 and shared.json()["user_id"] is None


async def test_member_sees_and_touches_only_their_own_channels(client_for) -> None:
    """An org channel, or a colleague's, is a **404** to a member — never a 403 (#283, §15)."""
    t = await make_tenant("chan-scope")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        a = await _member(c, owner, "a@scope.example")
        b = await _member(c, owner, "b@scope.example")
        ah, bh = await auth_cookie(a), await auth_cookie(b)

        org_id = (
            await c.post(
                "/api/v1/notifications/channels",
                json={"kind": "slack", "name": "#crm", "url": _SLACK},
                headers=owner,
            )
        ).json()["id"]
        b_id = (
            await c.post(
                "/api/v1/notifications/channels",
                json={"kind": "slack", "name": "B's DM", "url": _SLACK},
                headers=bh,
            )
        ).json()["id"]
        a_id = (
            await c.post(
                "/api/v1/notifications/channels",
                json={"kind": "slack", "name": "A's DM", "url": _SLACK},
                headers=ah,
            )
        ).json()["id"]

        listed = (await c.get("/api/v1/notifications/channels", headers=ah)).json()
        assert [row["id"] for row in listed] == [a_id]

        for other in (org_id, b_id):
            assert (
                await c.patch(
                    f"/api/v1/notifications/channels/{other}", json={"enabled": False}, headers=ah
                )
            ).status_code == 404
            assert (
                await c.delete(f"/api/v1/notifications/channels/{other}", headers=ah)
            ).status_code == 404
            assert (
                await c.post(f"/api/v1/notifications/channels/{other}/test", headers=ah)
            ).status_code == 404

        # Their own is theirs to edit.
        assert (
            await c.patch(
                f"/api/v1/notifications/channels/{a_id}", json={"name": "Mine"}, headers=ah
            )
        ).status_code == 200
        # And the admin still sees every one of them.
        assert len((await c.get("/api/v1/notifications/channels", headers=owner)).json()) == 3


async def test_event_fanout_enqueues_a_delivery(client_for) -> None:
    """A routed org channel gets a pending delivery row when an event reaches a recipient."""
    t = await make_tenant("chan-fanout")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        cid = await _connect_shared(c, owner)
        await _route(c, owner, {cid: {"leave.requested": "immediate"}})
        member = await _member(c, owner, "emp@example.com")
        mh = await auth_cookie(member)
        types = (await c.get("/api/v1/leave/types", headers=owner)).json()
        special = next(t["id"] for t in types if t["key"] == "special")

        start = leave_workday(0)
        end = start + timedelta(days=0)
        # The member requests leave → the manager (owner) is notified → the org channel is queued.
        res = await c.post(
            "/api/v1/leave/requests",
            json={
                "leave_type_id": special,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            },
            headers=mh,
        )
        assert res.status_code == 201, res.text

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        deliveries = (
            (
                await session.execute(
                    select(NotificationDelivery).where(
                        NotificationDelivery.org_id == t.org.id,
                        NotificationDelivery.channel == "external",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(deliveries) == 1
        assert deliveries[0].status == "pending"


async def test_connected_but_unrouted_room_is_silent(client_for) -> None:
    """No row means **not routed** (#295) — connecting a room must not start it pinging.

    This is the behaviour that replaced the old empty ``event_filter`` meaning "every event": a
    shared room now opts in per event, exactly as a personal channel always has.
    """
    t = await make_tenant("chan-unrouted")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _connect_shared(c, owner, "Silent")
        member = await _member(c, owner, "emp@unrouted.example")
        await _leave_request(c, await auth_cookie(member), owner)

    assert await _deliveries(t.org.id) == []


async def test_room_routing_is_per_event(client_for) -> None:
    """A room hears only the events it was routed (#295) — the event_filter's replacement."""
    t = await make_tenant("chan-per-event")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        cid = await _connect_shared(c, owner, "Companies only")
        # Routed for company events, so a leave request must not reach it.
        await _route(c, owner, {cid: {"company.created": "immediate"}})
        member = await _member(c, owner, "emp@per-event.example")
        await _leave_request(c, await auth_cookie(member), owner)

    assert await _deliveries(t.org.id) == []


async def test_room_routing_roundtrips_on_the_defaults_matrix(client_for) -> None:
    """The org matrix reads back a room's column, and a wholesale save clears what it omits."""
    t = await make_tenant("chan-route-roundtrip")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        cid = await _connect_shared(c, owner, "#crm")

        # A fresh room is a column of the org matrix with every event off.
        matrix = (await c.get("/api/v1/notifications/preferences/defaults", headers=owner)).json()
        column = next(ch for ch in matrix["channels"] if ch["id"] == cid)
        assert column["name"] == "#crm"
        assert all(row["enabled"] is False for row in column["events"])

        saved = await _route(
            c, owner, {cid: {"leave.requested": "immediate", "task.assigned": "daily"}}
        )
        routed = {
            row["event_type"]: row["digest"]
            for row in next(ch for ch in saved["channels"] if ch["id"] == cid)["events"]
            if row["enabled"]
        }
        assert routed == {"leave.requested": "immediate", "task.assigned": "daily"}

        # Wholesale: a save that omits an event un-routes it.
        after = await _route(c, owner, {cid: {"leave.requested": "immediate"}})
        still = {
            row["event_type"]
            for row in next(ch for ch in after["channels"] if ch["id"] == cid)["events"]
            if row["enabled"]
        }
        assert still == {"leave.requested"}

        # An id from another scope is a 404, never a 403 that confirms it exists.
        member = await _member(c, owner, "emp@route-roundtrip.example")
        personal = (
            await c.post(
                "/api/v1/notifications/channels",
                json={"kind": "slack", "name": "My DM", "url": _SLACK},
                headers=await auth_cookie(member),
            )
        ).json()["id"]
        refused = await c.put(
            "/api/v1/notifications/preferences/defaults",
            json={"channels": [{"channel_config_id": personal, "events": []}]},
            headers=owner,
        )
        assert refused.status_code == 404


async def test_saving_defaults_without_channels_manage_leaves_rooms_routed(client_for) -> None:
    """Reading and writing the channel columns are one permission, so a save cannot wipe them.

    The blocks are wholesale, so an admin who is shown no columns posts an empty list — and if
    that were allowed to write, saving an unrelated in-app default would silently un-route every
    shared room. A caller who does not hold ``channels.manage`` therefore sees no columns *and*
    changes none (#295).
    """
    t = await make_tenant("chan-route-nowipe")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        cid = await _connect_shared(c, owner, "#crm")
        await _route(c, owner, {cid: {"leave.requested": "immediate"}})

        # A role that curates the org defaults but does not manage channels — the exact split
        # that would otherwise wipe the room's routing on an unrelated save.
        role = await c.post(
            "/api/v1/roles",
            json={"key": "defaults_only", "permissions": ["notifications.defaults.manage"]},
            headers=owner,
        )
        assert role.status_code == 201, role.text
        member = await _member(c, owner, "emp@nowipe.example")
        members = (await c.get("/api/v1/members", headers=owner)).json()
        row = next(m for m in members if m["user_id"] == str(member.id))
        member_role = next(
            r for r in (await c.get("/api/v1/roles", headers=owner)).json() if r["key"] == "member"
        )
        assigned = await c.put(
            f"/api/v1/members/{row['membership_id']}/roles",
            json={"role_ids": [member_role["id"], role.json()["id"]]},
            headers=owner,
        )
        assert assigned.status_code == 200, assigned.text
        mh = await auth_cookie(member)

        # No columns for them …
        seen = (await c.get("/api/v1/notifications/preferences/defaults", headers=mh)).json()
        assert seen["channels"] == []

        # … and an ordinary save of the in-app defaults leaves the room routed.
        saved = await c.put(
            "/api/v1/notifications/preferences/defaults",
            json={"events": [{"event_type": "task.assigned", "enabled": False}]},
            headers=mh,
        )
        assert saved.status_code == 200, saved.text

        after = (await c.get("/api/v1/notifications/preferences/defaults", headers=owner)).json()
        routed = {
            row["event_type"]
            for row in next(ch for ch in after["channels"] if ch["id"] == cid)["events"]
            if row["enabled"]
        }
        assert routed == {"leave.requested"}


async def test_member_cannot_route_a_shared_room(client_for) -> None:
    """A room is org config: a member's own matrix has no column for it (#295, §15)."""
    t = await make_tenant("chan-route-rbac")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        cid = await _connect_shared(c, owner, "#crm")
        member = await _member(c, owner, "emp@route-rbac.example")
        mh = await auth_cookie(member)

        mine = (await c.get("/api/v1/notifications/preferences", headers=mh)).json()
        assert [ch["id"] for ch in mine["channels"]] == []

        # Reaching for it by id is a 404 on their own endpoint, and a 403 on the org's.
        body = {"channels": [{"channel_config_id": cid, "events": []}]}
        assert (
            await c.put("/api/v1/notifications/preferences", json=body, headers=mh)
        ).status_code == 404
        assert (
            await c.put("/api/v1/notifications/preferences/defaults", json=body, headers=mh)
        ).status_code == 403


# --------------------------------------------------------------------------- #
# Per-event cadence + digest bundling (#283 Phase A, generalised to rooms in #295)
# --------------------------------------------------------------------------- #


async def test_channel_schedule_roundtrip(client_for) -> None:
    """A channel carries a digest *schedule* — the hour and weekday its bundles land on (#283).

    Not a cadence: since #295 no channel has one of its own, so ``digest`` is not a field of the
    channel API at all. Which events, and how often, is the matrix's column.
    """
    t = await make_tenant("chan-schedule")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        plain = (
            await c.post(
                "/api/v1/notifications/channels",
                json={"kind": "slack", "name": "Now", "url": _SLACK},
                headers=headers,
            )
        ).json()
        assert "digest" not in plain and "event_filter" not in plain
        assert plain["digest_time"] is None and plain["digest_weekday"] is None

        weekly = (
            await c.post(
                "/api/v1/notifications/channels",
                json={
                    "kind": "slack",
                    "name": "Weekly",
                    "url": _SLACK,
                    "digest_time": "09:30",
                    "digest_weekday": 4,
                },
                headers=headers,
            )
        ).json()
        assert weekly["digest_time"] == "09:30:00" and weekly["digest_weekday"] == 4

        patched = await c.patch(
            f"/api/v1/notifications/channels/{weekly['id']}",
            json={"digest_time": "08:00", "digest_weekday": None},
            headers=headers,
        )
        assert patched.status_code == 200
        assert patched.json()["digest_time"] == "08:00:00"
        assert patched.json()["digest_weekday"] is None


async def test_room_groups_per_event_like_email(client_for) -> None:
    """One room, two cadences: leave immediately, tasks in the daily digest (#295).

    The point of the feature in one test. A room used to have exactly one cadence for everything
    it received, so "bundle the noisy events, ping me for the urgent ones" — which e-mail has had
    per event since #245 — was not expressible on Slack at all.
    """
    t = await make_tenant("chan-mixed-cadence")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        cid = await _connect_shared(c, owner, "#crm")
        await _route(c, owner, {cid: {"leave.requested": "immediate", "task.assigned": "daily"}})
        member = await _member(c, owner, "emp@mixed.example")
        await _leave_request(c, await auth_cookie(member), owner)

    rows = await _deliveries(t.org.id)
    assert len(rows) == 1
    # leave.requested is immediate on this room, so its slot is now — not tomorrow's 08:00.
    assert rows[0].deliver_after <= datetime.now(UTC) + timedelta(seconds=5)


async def test_digest_channel_holds_delivery_until_its_slot(client_for) -> None:
    """A daily-routed event's row is written now but held for the slot, never sent immediately."""
    t = await make_tenant("chan-digest-hold")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        cid = await _connect_shared(c, owner)
        await _route(c, owner, {cid: {"leave.requested": "daily"}})
        member = await _member(c, owner, "emp@digest-hold.example")
        await _leave_request(c, await auth_cookie(member), owner)

    rows = await _deliveries(t.org.id)
    assert len(rows) == 1
    assert rows[0].deliver_after is not None
    assert rows[0].deliver_after > datetime.now(UTC)

    # The sweep must respect the slot: nothing goes out yet.
    await _sweep(t.org.id)
    assert [r.status for r in await _deliveries(t.org.id)] == ["pending"]


async def test_sweep_bundles_one_message_per_channel(client_for, monkeypatch) -> None:
    """Two events, two channels → one message *each*, both events in each body (#283)."""
    t = await make_tenant("chan-bundle")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        ids = [
            await _connect_shared(c, owner, name, url)
            for name, url in (("Slack", _SLACK), ("Discord", _DISCORD))
        ]
        await _route(c, owner, {cid: {"leave.requested": "immediate"} for cid in ids})
        member = await _member(c, owner, "emp@bundle.example")
        mh = await auth_cookie(member)
        for offset in (0, 1):
            await _leave_request(c, mh, owner, offset)

    sent: list[tuple[str, str, str]] = []

    async def fake_apprise(url, message):  # noqa: ANN001
        sent.append((url, message.title, message.body))
        return True, None

    monkeypatch.setattr(external, "send_via_apprise", fake_apprise)
    await _sweep(t.org.id)

    # Four delivery rows (2 events × 2 channels) leave as exactly two messages.
    assert len(sent) == 2
    assert {url for url, _, _ in sent} == {_SLACK, _DISCORD}
    for _, title, body in sent:
        assert body.count("http") == 2  # both deep links in one message
        assert "2" in title  # the counted digest subject, not a single sentence
        # The message reads as sentences (#236), never as raw event types or i18n keys.
        assert "leave.requested" not in body and "notifications.event" not in body

    rows = await _deliveries(t.org.id)
    assert len(rows) == 4
    assert all(r.status == "sent" and r.sent_at is not None for r in rows)


async def test_immediate_channel_fires_on_the_next_tick(client_for, monkeypatch) -> None:
    """``immediate`` still means "the next sweep", the default every channel keeps (#283)."""
    t = await make_tenant("chan-immediate")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        cid = await _connect_shared(c, owner)
        await _route(c, owner, {cid: {"leave.requested": "immediate"}})
        member = await _member(c, owner, "emp@immediate.example")
        await _leave_request(c, await auth_cookie(member), owner)

    sent: list[str] = []

    async def fake_apprise(url, message):  # noqa: ANN001
        sent.append(message.body)
        return True, None

    monkeypatch.setattr(external, "send_via_apprise", fake_apprise)
    await _sweep(t.org.id)

    assert len(sent) == 1
    assert sent[0].count("http") == 1  # a group of one — no digest subject
    assert [r.status for r in await _deliveries(t.org.id)] == ["sent"]


async def test_failed_bundle_stays_pending_with_the_provider_error(client_for, monkeypatch) -> None:
    """A provider failure keeps the whole bundle pending and records the real reason (#17)."""
    t = await make_tenant("chan-fail")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        cid = await _connect_shared(c, owner)
        await _route(c, owner, {cid: {"leave.requested": "immediate"}})
        member = await _member(c, owner, "emp@fail.example")
        await _leave_request(c, await auth_cookie(member), owner)

    async def fake_apprise(url, message):  # noqa: ANN001
        return False, "channel_not_found"

    monkeypatch.setattr(external, "send_via_apprise", fake_apprise)
    await _sweep(t.org.id)

    rows = await _deliveries(t.org.id)
    assert len(rows) == 1
    assert rows[0].status == "pending"
    assert rows[0].attempts == 1
    assert rows[0].last_error == "channel_not_found"


async def test_channels_are_tenant_isolated(client_for) -> None:
    a = await make_tenant("chan-org-a")
    b = await make_tenant("chan-org-b")
    ah = await auth_cookie(a.user)
    bh = await auth_cookie(b.user)
    async with client_for(a.host) as ca:
        cid = (
            await ca.post(
                "/api/v1/notifications/channels",
                json={"kind": "slack", "name": "A", "url": _SLACK},
                headers=ah,
            )
        ).json()["id"]
    async with client_for(b.host) as cb:
        assert (await cb.get("/api/v1/notifications/channels", headers=bh)).json() == []
        res = await cb.patch(
            f"/api/v1/notifications/channels/{cid}", json={"enabled": False}, headers=bh
        )
        assert res.status_code == 404
