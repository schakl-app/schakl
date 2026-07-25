"""Personal e-mail delivery + digest (#17), and the guided channel-input normalization.

No network: the digest test monkeypatches the org transport and captures what would be sent.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.core.models import Org
from app.db import async_session_maker, set_current_org
from app.modules.notifications import external
from app.modules.notifications.channel_admin import normalize_channel_input
from app.modules.notifications.models import NotificationDelivery
from tests.conftest import auth_cookie, leave_workday, make_tenant
from tests.test_notification_channels import _member

# --------------------------------------------------------------------------- #
# normalize_channel_input: the guided forms paste the provider's own URL
# --------------------------------------------------------------------------- #


def test_normalize_converts_native_webhook_urls() -> None:
    assert (
        normalize_channel_input("slack", "https://hooks.slack.com/services/T0/B0/XYZ")
        == "slack://T0/B0/XYZ"
    )
    assert (
        normalize_channel_input("discord", "https://discord.com/api/webhooks/123/tok-en")
        == "discord://123/tok-en"
    )
    assert (
        normalize_channel_input(
            "gchat",
            "https://chat.googleapis.com/v1/spaces/SPACE/messages?key=KEY&token=TOK",
        )
        == "gchat://SPACE/KEY/TOK"
    )
    assert (
        normalize_channel_input(
            "msteams",
            "https://x.webhook.office.com/webhookb2/A@B/IncomingWebhook/CCC/DDD",
        )
        == "msteams://A@B/CCC/DDD"
    )
    assert (
        normalize_channel_input("telegram", "123456:ABC-def/78910")
        == "tgram://123456:ABC-def/78910"
    )
    assert (
        normalize_channel_input("webhook", "https://example.com/hook") == "jsons://example.com/hook"
    )


def test_normalize_passes_apprise_urls_through() -> None:
    assert normalize_channel_input("slack", "slack://T0/B0/XYZ") == "slack://T0/B0/XYZ"
    assert normalize_channel_input("custom", "ntfy://topic") == "ntfy://topic"


def test_normalize_rejects_foreign_input() -> None:
    with pytest.raises(ValueError):
        normalize_channel_input("slack", "https://example.com/not-slack")
    with pytest.raises(ValueError):
        normalize_channel_input("gchat", "https://chat.googleapis.com/other")
    with pytest.raises(ValueError):
        normalize_channel_input("email", "slack://not-an-address")


# --------------------------------------------------------------------------- #
# Per-event e-mail preference (#245) and the digest sweep
# --------------------------------------------------------------------------- #


async def test_email_matrix_default_off_and_per_event_override(client_for) -> None:
    """E-mail is off for every event by default; a per-event override turns one on (#245)."""
    t = await make_tenant("email-matrix")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        matrix = (await c.get("/api/v1/notifications/preferences", headers=headers)).json()
        assert all(row["email_enabled"] is False for row in matrix["events"])
        assert matrix["email"]["source"] == "default"

        saved = await c.put(
            "/api/v1/notifications/preferences",
            json={
                "email_events": [
                    {"event_type": "leave.requested", "enabled": True, "digest": "weekly"}
                ],
                "email": {"digest_time": "09:30", "digest_weekday": 4},
            },
            headers=headers,
        )
        assert saved.status_code == 200, saved.text
        body = saved.json()
        row = next(r for r in body["events"] if r["event_type"] == "leave.requested")
        assert row["email_enabled"] is True and row["email_digest"] == "weekly"
        assert row["email_source"] == "user"
        # Every other event stays off and inherited — one override does not touch the rest.
        other = next(r for r in body["events"] if r["event_type"] == "task.assigned")
        assert other["email_enabled"] is False and other["email_source"] == "default"
        assert body["email"]["digest_time"] == "09:30:00"
        assert body["email"]["digest_weekday"] == 4 and body["email"]["source"] == "user"


async def test_email_needs_in_app_enabled(client_for) -> None:
    """E-mail fans out from the in-app row, so an event switched off in-app never mails (#245)."""
    t = await make_tenant("email-needs-inapp")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.put(
            "/api/v1/notifications/preferences",
            json={
                "events": [
                    {"event_type": "leave.requested", "enabled": False, "digest": "immediate"}
                ],
                "email_events": [
                    {"event_type": "leave.requested", "enabled": True, "digest": "immediate"}
                ],
            },
            headers=owner,
        )
        member = await _member(c, owner, "emp@needs-inapp.example")
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
        rows = (
            (
                await session.execute(
                    select(NotificationDelivery).where(
                        NotificationDelivery.org_id == t.org.id,
                        NotificationDelivery.channel == "email",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert rows == []  # in-app off → no inbox row → no e-mail delivery


async def test_fanout_enqueues_email_delivery_at_the_digest_slot(client_for) -> None:
    t = await make_tenant("digest-fanout")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        # The owner (who gets notified about leave requests) wants that event by daily e-mail.
        await c.put(
            "/api/v1/notifications/preferences",
            json={
                "email_events": [
                    {"event_type": "leave.requested", "enabled": True, "digest": "daily"}
                ]
            },
            headers=owner,
        )
        member = await _member(c, owner, "emp@digest-fanout.example")
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
        rows = (
            (
                await session.execute(
                    select(NotificationDelivery).where(
                        NotificationDelivery.org_id == t.org.id,
                        NotificationDelivery.channel == "email",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].status == "pending"
        # Daily cadence → held for the next 08:00 slot, never due immediately.
        assert rows[0].deliver_after is not None
        assert rows[0].deliver_after > datetime.now(UTC)


async def test_digest_sweep_groups_one_mail_per_recipient(client_for, monkeypatch) -> None:
    t = await make_tenant("digest-sweep")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.put(
            "/api/v1/notifications/preferences",
            json={
                "email_events": [
                    {"event_type": "leave.requested", "enabled": True, "digest": "immediate"}
                ]
            },
            headers=owner,
        )
        member = await _member(c, owner, "emp@digest-sweep.example")
        mh = await auth_cookie(member)
        types = (await c.get("/api/v1/leave/types", headers=owner)).json()
        special = next(x["id"] for x in types if x["key"] == "special")
        for offset in (0, 1):
            start = leave_workday(offset)
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

    sent: list[tuple[str, str, str, str | None]] = []

    async def fake_send(session, org_id, message, **kwargs):  # noqa: ANN001
        sent.append((message.to, message.subject, message.text, message.html))
        return True, None

    import app.core.email.service as email_service

    monkeypatch.setattr(email_service, "send_org_email", fake_send)

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        org = await session.get(Org, t.org.id)
        await external.dispatch_email_deliveries(session, org)
        await session.commit()

    # Two notifications, one recipient → exactly one mail, both items in the body.
    assert len(sent) == 1
    to, subject, text, html = sent[0]
    assert to == t.user.email
    assert "2" in subject
    assert text.count("http") == 2
    # The mail reads as sentences (#236), never as raw event types or i18n keys.
    assert "leave.requested" not in text and "notifications.event" not in text
    assert html is not None and html.count("<a href=") == 2
    assert text.startswith("M ")  # the actor (the member's display name) opens the sentence

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        rows = (
            (
                await session.execute(
                    select(NotificationDelivery).where(
                        NotificationDelivery.org_id == t.org.id,
                        NotificationDelivery.channel == "email",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 2
        assert all(r.status == "sent" for r in rows)
        now = datetime.now(UTC)
        assert all(r.deliver_after is not None and r.deliver_after <= now for r in rows)


async def test_org_default_email_is_inherited(client_for) -> None:
    """An org-default e-mail override reaches a member as an inherited row (#245)."""
    t = await make_tenant("email-orgdefault")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, owner, "emp@orgdef.example")
        mh = await auth_cookie(member)
        await c.put(
            "/api/v1/notifications/preferences/defaults",
            json={
                "email_events": [
                    {"event_type": "task.assigned", "enabled": True, "digest": "daily"}
                ]
            },
            headers=owner,
        )
        matrix = (await c.get("/api/v1/notifications/preferences", headers=mh)).json()
        row = next(r for r in matrix["events"] if r["event_type"] == "task.assigned")
        assert row["email_enabled"] is True
        assert row["email_digest"] == "daily"
        assert row["email_source"] == "org"
