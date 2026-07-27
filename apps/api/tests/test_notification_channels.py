"""External notification channels via Apprise (#17): admin-only CRUD, encryption, SSRF, fan-out.

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


async def test_only_admin_manages_channels(client_for) -> None:
    t = await make_tenant("chan-rbac")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, owner, "m@example.com")
        mh = await auth_cookie(member)
        res = await c.post(
            "/api/v1/notifications/channels",
            json={"kind": "slack", "name": "x", "url": _SLACK},
            headers=mh,
        )
        assert res.status_code == 403
        assert (await c.get("/api/v1/notifications/channels", headers=mh)).status_code == 403


async def test_event_fanout_enqueues_a_delivery(client_for) -> None:
    """An enabled org channel gets a pending delivery row when an event reaches a recipient."""
    t = await make_tenant("chan-fanout")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.post(
            "/api/v1/notifications/channels",
            json={"kind": "slack", "name": "Team", "url": _SLACK},
            headers=owner,
        )
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


async def test_event_filter_edit_roundtrip(client_for) -> None:
    """event_filter is settable on create and editable via PATCH; [] means all events (#245)."""
    t = await make_tenant("chan-filter-edit")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        created = (
            await c.post(
                "/api/v1/notifications/channels",
                json={
                    "kind": "slack",
                    "name": "Only tasks",
                    "url": _SLACK,
                    "event_filter": ["task.assigned"],
                },
                headers=headers,
            )
        ).json()
        assert created["event_filter"] == ["task.assigned"]
        cid = created["id"]

        widened = await c.patch(
            f"/api/v1/notifications/channels/{cid}",
            json={"event_filter": ["task.assigned", "leave.requested"]},
            headers=headers,
        )
        assert widened.status_code == 200
        assert set(widened.json()["event_filter"]) == {"task.assigned", "leave.requested"}

        # An unknown event is rejected — the picker only ever posts known keys.
        bad = await c.patch(
            f"/api/v1/notifications/channels/{cid}",
            json={"event_filter": ["not.a.real.event"]},
            headers=headers,
        )
        assert bad.status_code == 422

        cleared = await c.patch(
            f"/api/v1/notifications/channels/{cid}", json={"event_filter": []}, headers=headers
        )
        assert cleared.json()["event_filter"] == []


async def test_event_filter_routes_only_listed_events(client_for) -> None:
    """A channel with a non-empty event_filter is skipped for events it does not list (#245)."""
    t = await make_tenant("chan-filter-route")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        # This channel only wants company events, so a leave request must not reach it.
        await c.post(
            "/api/v1/notifications/channels",
            json={
                "kind": "slack",
                "name": "Companies only",
                "url": _SLACK,
                "event_filter": ["company.created"],
            },
            headers=owner,
        )
        member = await _member(c, owner, "emp@filter-route.example")
        mh = await auth_cookie(member)
        types = (await c.get("/api/v1/leave/types", headers=owner)).json()
        special = next(x["id"] for x in types if x["key"] == "special")
        start = leave_workday(0)
        res = await c.post(
            "/api/v1/leave/requests",
            json={
                "leave_type_id": special,
                "start_date": start.isoformat(),
                "end_date": start.isoformat(),
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
        assert deliveries == []  # leave.requested is not in the channel's filter


# --------------------------------------------------------------------------- #
# Channel-level cadence + digest bundling (#283, Phase A)
# --------------------------------------------------------------------------- #


async def test_channel_cadence_roundtrip(client_for) -> None:
    """A channel carries its own cadence; ``immediate`` is the default (pre-#283 behaviour)."""
    t = await make_tenant("chan-cadence")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        plain = (
            await c.post(
                "/api/v1/notifications/channels",
                json={"kind": "slack", "name": "Now", "url": _SLACK},
                headers=headers,
            )
        ).json()
        assert plain["digest"] == "immediate"
        assert plain["digest_time"] is None and plain["digest_weekday"] is None

        weekly = (
            await c.post(
                "/api/v1/notifications/channels",
                json={
                    "kind": "slack",
                    "name": "Weekly",
                    "url": _SLACK,
                    "digest": "weekly",
                    "digest_time": "09:30",
                    "digest_weekday": 4,
                },
                headers=headers,
            )
        ).json()
        assert weekly["digest"] == "weekly"
        assert weekly["digest_time"] == "09:30:00" and weekly["digest_weekday"] == 4

        patched = await c.patch(
            f"/api/v1/notifications/channels/{weekly['id']}",
            json={"digest": "daily", "digest_time": "08:00", "digest_weekday": None},
            headers=headers,
        )
        assert patched.status_code == 200
        assert patched.json()["digest"] == "daily"
        assert patched.json()["digest_weekday"] is None

        bad = await c.patch(
            f"/api/v1/notifications/channels/{weekly['id']}",
            json={"digest": "fortnightly"},
            headers=headers,
        )
        assert bad.status_code == 422


async def test_digest_channel_holds_delivery_until_its_slot(client_for) -> None:
    """A daily channel's rows are written now but held for the slot, never sent immediately."""
    t = await make_tenant("chan-digest-hold")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.post(
            "/api/v1/notifications/channels",
            json={"kind": "slack", "name": "Team", "url": _SLACK, "digest": "daily"},
            headers=owner,
        )
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
        for name, url in (("Slack", _SLACK), ("Discord", _DISCORD)):
            res = await c.post(
                "/api/v1/notifications/channels",
                json={"kind": name.lower(), "name": name, "url": url},
                headers=owner,
            )
            assert res.status_code == 201, res.text
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
        await c.post(
            "/api/v1/notifications/channels",
            json={"kind": "slack", "name": "Team", "url": _SLACK, "digest": "immediate"},
            headers=owner,
        )
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
        await c.post(
            "/api/v1/notifications/channels",
            json={"kind": "slack", "name": "Team", "url": _SLACK},
            headers=owner,
        )
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
