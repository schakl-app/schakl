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
# The personal e-mail preference and the digest sweep
# --------------------------------------------------------------------------- #


async def test_email_pref_roundtrip(client_for) -> None:
    t = await make_tenant("digest-pref")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        default = (await c.get("/api/v1/notifications/preferences/email", headers=headers)).json()
        assert default == {
            "enabled": False,
            "digest": "daily",
            "digest_time": "08:00:00",
            "digest_weekday": None,
            "source": "default",
        }
        saved = await c.put(
            "/api/v1/notifications/preferences/email",
            json={"enabled": True, "digest": "weekly", "digest_time": "09:30", "digest_weekday": 4},
            headers=headers,
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["source"] == "user"
        assert saved.json()["digest"] == "weekly"


async def test_fanout_enqueues_email_delivery_at_the_digest_slot(client_for) -> None:
    t = await make_tenant("digest-fanout")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        # The owner (who gets notified about leave requests) wants a daily e-mail digest.
        await c.put(
            "/api/v1/notifications/preferences/email",
            json={"enabled": True, "digest": "daily"},
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
            "/api/v1/notifications/preferences/email",
            json={"enabled": True, "digest": "immediate"},
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
