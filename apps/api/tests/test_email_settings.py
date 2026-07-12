"""Org e-mail transport (#17): admin-only settings, encryption at rest, secret round-trip.

No network is touched: nothing here calls a provider. The test-send path is exercised only for
the not-configured case, which answers before any transport is chosen.
"""

from __future__ import annotations

import json

from sqlalchemy import select

from app.core.crypto import decrypt
from app.core.email.models import EmailSettings
from app.db import async_session_maker, set_current_org
from tests.conftest import auth_cookie, make_tenant

_BREVO = {
    "provider": "brevo",
    "from_email": "noreply@agency-example.nl",
    "from_name": "Agency",
    "api_key": "xkeysib-secret-123",
}


async def test_settings_upsert_encrypts_and_redacts(client_for) -> None:
    t = await make_tenant("email-crud")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        saved = await c.put("/api/v1/settings/email", json=_BREVO, headers=headers)
        assert saved.status_code == 200, saved.text
        body = saved.json()
        assert body["provider"] == "brevo"
        assert body["has_secret"] is True
        assert "api_key" not in body  # the secret is never returned

        got = await c.get("/api/v1/settings/email", headers=headers)
        assert got.status_code == 200
        assert got.json()["from_email"] == "noreply@agency-example.nl"

    # Encrypted at rest: the raw column holds neither the key nor readable JSON.
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        row = await session.scalar(select(EmailSettings))
        assert row is not None
        assert "xkeysib-secret-123" not in row.config_enc
        assert json.loads(decrypt(row.config_enc))["api_key"] == "xkeysib-secret-123"


async def test_update_with_empty_secret_keeps_stored_key(client_for) -> None:
    t = await make_tenant("email-keep")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        saved = await c.put("/api/v1/settings/email", json=_BREVO, headers=headers)
        assert saved.status_code == 200
        # Change the from-name, omit the api_key — the stored secret must survive.
        updated = await c.put(
            "/api/v1/settings/email",
            json={**_BREVO, "from_name": "Agency B.V.", "api_key": ""},
            headers=headers,
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["has_secret"] is True

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        row = await session.scalar(select(EmailSettings))
        assert json.loads(decrypt(row.config_enc))["api_key"] == "xkeysib-secret-123"
        assert row.from_name == "Agency B.V."


async def test_smtp_requires_host_and_api_provider_requires_key(client_for) -> None:
    t = await make_tenant("email-val")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        no_host = await c.put(
            "/api/v1/settings/email",
            json={"provider": "smtp", "from_email": "a@b-example.nl", "from_name": "A"},
            headers=headers,
        )
        assert no_host.status_code == 422
        no_key = await c.put(
            "/api/v1/settings/email",
            json={"provider": "sendgrid", "from_email": "a@b-example.nl", "from_name": "A"},
            headers=headers,
        )
        assert no_key.status_code == 422


async def test_member_cannot_manage_email_settings(client_for) -> None:
    from tests.test_notification_channels import _member

    t = await make_tenant("email-rbac")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, headers, "m@email-rbac-example.nl")
        member_headers = await auth_cookie(member)
        assert (await c.get("/api/v1/settings/email", headers=member_headers)).status_code == 403
        assert (
            await c.put("/api/v1/settings/email", json=_BREVO, headers=member_headers)
        ).status_code == 403


async def test_tenant_isolation(client_for) -> None:
    t1 = await make_tenant("email-iso-a")
    t2 = await make_tenant("email-iso-b")
    async with client_for(t1.host) as c:
        headers = await auth_cookie(t1.user)
        saved = await c.put("/api/v1/settings/email", json=_BREVO, headers=headers)
        assert saved.status_code == 200
    async with client_for(t2.host) as c:
        headers = await auth_cookie(t2.user)
        got = await c.get("/api/v1/settings/email", headers=headers)
        assert got.status_code == 200
        assert got.json() is None  # org B never sees org A's transport


async def test_delete_turns_email_off_and_test_reports_not_configured(client_for) -> None:
    t = await make_tenant("email-del")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        saved = await c.put("/api/v1/settings/email", json=_BREVO, headers=headers)
        assert saved.status_code == 200
        assert (await c.delete("/api/v1/settings/email", headers=headers)).status_code == 204
        assert (await c.get("/api/v1/settings/email", headers=headers)).json() is None

        result = await c.post("/api/v1/settings/email/test", headers=headers)
        assert result.status_code == 200
        assert result.json() == {"ok": False, "error": "errors.email_not_configured"}


async def test_email_channel_kind_redacts_address(client_for) -> None:
    t = await make_tenant("email-chan")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        created = await c.post(
            "/api/v1/notifications/channels",
            json={"kind": "email", "name": "Team mail", "url": "team@agency.test"},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        assert created.json()["redacted"] == "t***@agency.test"

        bad = await c.post(
            "/api/v1/notifications/channels",
            json={"kind": "email", "name": "Bad", "url": "slack://not-an-address"},
            headers=headers,
        )
        assert bad.status_code == 422
