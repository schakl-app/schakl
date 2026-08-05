"""Personal external channels with per-event cadence (#283, Phase B).

The point of the feature in one sentence: *"my Slack: ``leave.requested`` immediately,
``leave.approved`` in a daily digest, everything else silent."* These tests hold that sentence
to its word — routing, cadence, bundling, tenant isolation, and the fact that connecting a
channel and saying nothing else delivers **nothing**.

No network: the Apprise call is monkeypatched, and named providers (``slack://``) skip host
resolution anyway.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, text

from app.db import async_session_maker, set_current_org
from app.modules.notifications import external
from app.modules.notifications.models import NotificationDelivery, NotificationPreference
from tests.conftest import auth_cookie, make_tenant
from tests.test_notification_channels import (
    _SLACK,
    _deliveries,
    _leave_request,
    _member,
    _sweep,
)


async def _connect(client, headers, user_id, name: str = "My DM", **extra) -> str:  # noqa: ANN001
    """Connect a channel *to this person*.

    ``user_id`` is explicit because an admin holds ``channels.manage`` and may create an org
    channel, so their create is only personal if it says so — exactly what the "My channels"
    form posts. A plain member never needs it: the service forces their own id either way.
    """
    res = await client.post(
        "/api/v1/notifications/channels",
        json={
            "kind": "slack",
            "name": name,
            "url": _SLACK,
            "user_id": str(user_id) if user_id else None,
            **extra,
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _route(client, headers, channel_id: str, routes: dict[str, str]) -> dict:  # noqa: ANN001
    """Set this channel's whole per-event routing, wholesale, as the matrix form does."""
    res = await client.put(
        "/api/v1/notifications/preferences",
        json={
            "channels": [
                {
                    "channel_config_id": channel_id,
                    "events": [
                        {"event_type": event, "enabled": True, "digest": digest}
                        for event, digest in routes.items()
                    ],
                }
            ]
        },
        headers=headers,
    )
    assert res.status_code == 200, res.text
    return res.json()


# --------------------------------------------------------------------------- #
# The matrix carries a column per personal channel
# --------------------------------------------------------------------------- #


async def test_matrix_gains_a_column_per_personal_channel(client_for) -> None:
    t = await make_tenant("pers-matrix")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        before = (await c.get("/api/v1/notifications/preferences", headers=headers)).json()
        assert before["channels"] == []

        channel_id = await _connect(c, headers, t.user.id)
        matrix = (await c.get("/api/v1/notifications/preferences", headers=headers)).json()
        assert len(matrix["channels"]) == 1
        column = matrix["channels"][0]
        assert column["id"] == channel_id and column["kind"] == "slack"
        # Connected but silent: every event off until the owner routes it here.
        assert all(row["enabled"] is False for row in column["events"])
        assert {row["event_type"] for row in column["events"]} == {
            row["event_type"] for row in matrix["events"]
        }

        saved = await _route(
            c, headers, channel_id, {"leave.requested": "immediate", "leave.approved": "daily"}
        )
        routed = {
            row["event_type"]: row
            for row in saved["channels"][0]["events"]
            if row["enabled"]
        }
        assert routed["leave.requested"]["digest"] == "immediate"
        assert routed["leave.approved"]["digest"] == "daily"
        assert len(routed) == 2  # nothing else was touched


async def test_each_matrix_carries_only_its_own_scopes_channels(client_for) -> None:
    """A channel is a column of exactly one matrix, and the scope that owns it decides which.

    Both are columns now (#295) — a shared room is routed per event like everything else — but not
    of the same table: `#crm` is one answer for the whole agency, my DM is mine. An admin holds
    both capabilities and so sees both screens; each still shows only what it routes.
    """
    t = await make_tenant("pers-not-org")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        shared = (
            await c.post(
                "/api/v1/notifications/channels",
                json={"kind": "slack", "name": "#crm", "url": _SLACK},
                headers=headers,
            )
        ).json()["id"]
        mine = await _connect(c, headers, t.user.id, "My DM")

        matrix = (await c.get("/api/v1/notifications/preferences", headers=headers)).json()
        assert [ch["id"] for ch in matrix["channels"]] == [mine]

        defaults = (
            await c.get("/api/v1/notifications/preferences/defaults", headers=headers)
        ).json()
        assert [ch["id"] for ch in defaults["channels"]] == [shared]


async def test_cannot_write_preferences_for_someone_elses_channel(client_for) -> None:
    t = await make_tenant("pers-foreign")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, owner, "m@foreign.example")
        mh = await auth_cookie(member)
        theirs = await _connect(c, mh, member.id, "Their DM")
        org = (
            await c.post(
                "/api/v1/notifications/channels",
                json={"kind": "slack", "name": "#crm", "url": _SLACK},
                headers=owner,
            )
        ).json()["id"]

        for channel_id in (theirs, org, str(uuid.uuid4())):
            res = await c.put(
                "/api/v1/notifications/preferences",
                json={
                    "channels": [
                        {
                            "channel_config_id": channel_id,
                            "events": [{"event_type": "task.assigned", "enabled": True}],
                        }
                    ]
                },
                headers=owner,
            )
            assert res.status_code == 404, res.text


# --------------------------------------------------------------------------- #
# Routing and cadence
# --------------------------------------------------------------------------- #


async def test_unrouted_personal_channel_delivers_nothing(client_for) -> None:
    """Connecting a channel must not start pinging someone's phone on its own (#283)."""
    t = await make_tenant("pers-silent")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await _connect(c, owner, t.user.id)
        member = await _member(c, owner, "emp@silent.example")
        await _leave_request(c, await auth_cookie(member), owner)

    assert await _deliveries(t.org.id) == []


async def test_per_event_cadence_routes_and_schedules_independently(client_for) -> None:
    """The headline: one event immediate, another on a digest, the rest silent."""
    t = await make_tenant("pers-cadence")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        channel_id = await _connect(c, owner, t.user.id)
        await _route(c, owner, channel_id, {"leave.requested": "daily"})
        member = await _member(c, owner, "emp@cadence.example")
        await _leave_request(c, await auth_cookie(member), owner)

    rows = await _deliveries(t.org.id)
    assert len(rows) == 1
    assert str(rows[0].channel_config_id) == channel_id
    # Daily → held for the next slot, not sent on the next tick.
    assert rows[0].deliver_after is not None and rows[0].deliver_after > datetime.now(UTC)


async def test_unrouted_event_is_skipped_even_when_another_is_routed(client_for) -> None:
    t = await make_tenant("pers-skip")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        channel_id = await _connect(c, owner, t.user.id)
        # Route something else entirely: the leave request must not reach the channel.
        await _route(c, owner, channel_id, {"task.assigned": "immediate"})
        member = await _member(c, owner, "emp@skip.example")
        await _leave_request(c, await auth_cookie(member), owner)

    assert await _deliveries(t.org.id) == []


async def test_event_filter_does_not_route_a_personal_channel(client_for) -> None:
    """Two routing mechanisms on one channel would be two places to look (#283)."""
    t = await make_tenant("pers-filter")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        # A filter that says "everything" still delivers nothing without a matrix row.
        await _connect(c, owner, t.user.id, event_filter=["leave.requested"])
        member = await _member(c, owner, "emp@filter.example")
        await _leave_request(c, await auth_cookie(member), owner)

    assert await _deliveries(t.org.id) == []


async def test_personal_channel_needs_the_in_app_row(client_for) -> None:
    """External fans out from the bell rows, so an event off in-app cannot reach a channel."""
    t = await make_tenant("pers-needs-inapp")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        channel_id = await _connect(c, owner, t.user.id)
        await c.put(
            "/api/v1/notifications/preferences",
            json={
                "events": [
                    {"event_type": "leave.requested", "enabled": False, "digest": "immediate"}
                ],
                "channels": [
                    {
                        "channel_config_id": channel_id,
                        "events": [{"event_type": "leave.requested", "enabled": True}],
                    }
                ],
            },
            headers=owner,
        )
        member = await _member(c, owner, "emp@needs-inapp.example")
        await _leave_request(c, await auth_cookie(member), owner)

    assert await _deliveries(t.org.id) == []


async def test_personal_digest_bundles_into_one_message(client_for, monkeypatch) -> None:
    """Two events on one personal channel leave as a single bundled message (#283)."""
    t = await make_tenant("pers-bundle")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        channel_id = await _connect(c, owner, t.user.id)
        await _route(c, owner, channel_id, {"leave.requested": "immediate"})
        member = await _member(c, owner, "emp@bundle.example")
        mh = await auth_cookie(member)
        for offset in (0, 1):
            await _leave_request(c, mh, owner, offset)

    sent: list[tuple[str, str]] = []

    async def fake_apprise(url, message):  # noqa: ANN001
        sent.append((message.title, message.body))
        return True, None

    monkeypatch.setattr(external, "send_via_apprise", fake_apprise)
    await _sweep(t.org.id)

    assert len(sent) == 1
    title, body = sent[0]
    assert body.count("http") == 2
    assert "leave.requested" not in body and "notifications.event" not in body
    rows = await _deliveries(t.org.id)
    assert len(rows) == 2 and all(r.status == "sent" for r in rows)


async def test_channel_schedule_places_the_digest(client_for) -> None:
    """The channel owns the *schedule*; the matrix owns the cadence (#283)."""
    t = await make_tenant("pers-schedule")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        channel_id = await _connect(c, owner, t.user.id, digest_time="23:30")
        await _route(c, owner, channel_id, {"leave.requested": "daily"})
        member = await _member(c, owner, "emp@schedule.example")
        await _leave_request(c, await auth_cookie(member), owner)

    rows = await _deliveries(t.org.id)
    assert len(rows) == 1
    # 23:30 on **this org's** clock — whatever the offset, never the default 08:00 slot. The
    # zone comes from the same resolver the API used, not a constant this file picks.
    from app.core.timezone import resolve_zoneinfo

    local = rows[0].deliver_after.astimezone(resolve_zoneinfo(None))
    assert (local.hour, local.minute) == (23, 30)


# --------------------------------------------------------------------------- #
# The startup reconciler reaches orgs that predate the key
# --------------------------------------------------------------------------- #


async def test_reconciler_grants_manage_own_to_an_existing_org(client_for) -> None:
    """Why ``manage_own`` is a *new* key and not a scope on the old one (#283).

    The reconciler grants catalog keys an org has never been offered. Re-scoping
    ``notifications.channels.manage`` would have changed no stored grant anywhere, so every
    already-installed org's members would have been left unable to connect a channel, with no
    data migration able to fix it (a migration must never import the catalog).
    """
    t = await make_tenant("pers-reconcile", role="member")
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        # Rewind to the pre-#283 state: the key was never offered, member never held it.
        await session.execute(
            text(
                "UPDATE org_settings SET applied_permission_defaults ="
                " array_remove(applied_permission_defaults,"
                " 'notifications.channels.manage_own') WHERE org_id = :org"
            ),
            {"org": str(t.org.id)},
        )
        await session.execute(
            text(
                "DELETE FROM role_permissions WHERE org_id = :org"
                " AND permission = 'notifications.channels.manage_own'"
            ),
            {"org": str(t.org.id)},
        )
        await session.commit()

    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        assert (await c.get("/api/v1/notifications/channels", headers=headers)).status_code == 403

    from app.core.permissions.reconcile import reconcile_permission_defaults

    await reconcile_permission_defaults()

    async with client_for(t.host) as c:
        assert (await c.get("/api/v1/notifications/channels", headers=headers)).status_code == 200


# --------------------------------------------------------------------------- #
# Tenant isolation
# --------------------------------------------------------------------------- #


async def test_channel_preferences_are_tenant_isolated(client_for) -> None:
    a = await make_tenant("pers-org-a")
    b = await make_tenant("pers-org-b")
    ah, bh = await auth_cookie(a.user), await auth_cookie(b.user)
    async with client_for(a.host) as ca:
        channel_id = await _connect(ca, ah, a.user.id)
        await _route(ca, ah, channel_id, {"leave.requested": "daily"})
    async with client_for(b.host) as cb:
        # B cannot see the column, and cannot claim A's channel id.
        assert (await cb.get("/api/v1/notifications/preferences", headers=bh)).json()[
            "channels"
        ] == []
        res = await cb.put(
            "/api/v1/notifications/preferences",
            json={
                "channels": [
                    {
                        "channel_config_id": channel_id,
                        "events": [{"event_type": "task.assigned", "enabled": True}],
                    }
                ]
            },
            headers=bh,
        )
        assert res.status_code == 404

    async with async_session_maker() as session:
        await set_current_org(session, a.org.id)
        rows = (
            (
                await session.execute(
                    select(NotificationPreference).where(
                        NotificationPreference.channel_config_id.is_not(None)
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].org_id == a.org.id


async def test_deleting_a_channel_takes_its_preferences_with_it(client_for) -> None:
    """The FK cascades: a removed channel leaves no orphaned routing behind."""
    t = await make_tenant("pers-cascade")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        channel_id = await _connect(c, headers, t.user.id)
        await _route(c, headers, channel_id, {"leave.requested": "daily"})
        assert (
            await c.delete(f"/api/v1/notifications/channels/{channel_id}", headers=headers)
        ).status_code == 204

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        rows = (
            (
                await session.execute(
                    select(NotificationPreference).where(
                        NotificationPreference.org_id == t.org.id,
                        NotificationPreference.channel_config_id.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert rows == []
        deliveries = (
            (
                await session.execute(
                    select(NotificationDelivery).where(
                        NotificationDelivery.org_id == t.org.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert all(d.channel_config_id is not None for d in deliveries)
