"""External notification channels via Apprise (#17): admin-only CRUD, encryption, SSRF, fan-out.

No network is touched here: named providers (``slack://``) skip host resolution, and the SSRF
case uses a literal private IP so no DNS is needed. Delivery *dispatch* (the provider call) is the
worker's job and is not exercised.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select

from app.core.auth.models import User
from app.db import async_session_maker, set_current_org
from app.modules.notifications.models import NotificationChannelConfig, NotificationDelivery
from tests.conftest import auth_cookie, leave_workday, make_tenant

_SLACK = "slack://xoxb-abc-def/#crm"


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
