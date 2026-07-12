"""Google core (#22): settings secret handling, connections, vault upsert, error flagging."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.core.crypto import decrypt, encrypt
from app.db import async_session_maker, set_current_org
from app.modules.google.client import mark_connection_error
from app.modules.google.models import GoogleConnection
from app.modules.google.service import GoogleConnectionsService
from app.registry import registry
from tests.conftest import auth_cookie, make_tenant


async def _seed_connection(tenant, user_id: uuid.UUID, **overrides) -> uuid.UUID:
    values = dict(
        org_id=tenant.org.id,
        user_id=user_id,
        google_sub="sub-1",
        email="werknemer@agency.nl",
        scopes=["openid", "email"],
        refresh_token_encrypted=encrypt("refresh-token-plain"),
    )
    values.update(overrides)
    async with async_session_maker() as session:
        await set_current_org(session, tenant.org.id)
        connection = GoogleConnection(**values)
        session.add(connection)
        await session.commit()
        return connection.id


def test_google_module_is_licensed() -> None:
    """The whole commercial boundary is the sku on the descriptor (issue #137)."""
    module = registry.get("google")
    assert module is not None and module.sku == "google"


async def test_settings_secret_is_write_only(client_for) -> None:
    t = await make_tenant("goog-settings")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        first = await c.get("/api/v1/google/settings", headers=headers)
        assert first.status_code == 200, first.text
        assert first.json()["client_secret_configured"] is False
        assert first.json()["callback_url"].endswith("/api/v1/google/oauth/callback")

        saved = await c.put(
            "/api/v1/google/settings",
            json={
                "client_id": "abc.apps.googleusercontent.com",
                "client_secret": "super-secret",
                "calendar_enabled": True,
                "drive_enabled": True,
            },
            headers=headers,
        )
        assert saved.status_code == 200, saved.text
        body = saved.json()
        assert body["client_secret_configured"] is True
        assert "super-secret" not in saved.text  # the secret never leaves the server

        # An empty secret on a later save keeps the stored one.
        kept = await c.put(
            "/api/v1/google/settings",
            json={
                "client_id": "abc.apps.googleusercontent.com",
                "client_secret": "",
                "calendar_enabled": True,
                "drive_enabled": True,
                "gmail_enabled": True,
            },
            headers=headers,
        )
        assert kept.json()["client_secret_configured"] is True
        assert kept.json()["gmail_enabled"] is True

    # Stored encrypted — never plaintext at rest.
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        from app.modules.google.models import GoogleSettings

        row = await session.scalar(select(GoogleSettings))
        assert row is not None and row.client_secret_encrypted != "super-secret"
        assert decrypt(row.client_secret_encrypted) == "super-secret"


async def test_settings_are_admin_only(client_for) -> None:
    t = await make_tenant("goog-member", role="member")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        assert (await c.get("/api/v1/google/settings", headers=headers)).status_code == 403
        # But a member reads their own connection state (per-user opt-in surface).
        me = await c.get("/api/v1/google/connections/me", headers=headers)
        assert me.status_code == 200 and me.json()["connected"] is False


async def test_my_connection_patch_and_disconnect(client_for, monkeypatch) -> None:
    t = await make_tenant("goog-me")
    await _seed_connection(t, t.user.id, gmail_sync_enabled=False)
    headers = await auth_cookie(t.user)

    revoked: list[str] = []

    async def _fake_revoke(connection) -> None:
        revoked.append(connection.email)

    monkeypatch.setattr("app.modules.google.service.google_client.revoke", _fake_revoke)

    async with client_for(t.host) as c:
        me = (await c.get("/api/v1/google/connections/me", headers=headers)).json()
        assert me["connected"] is True
        assert me["connection"]["email"] == "werknemer@agency.nl"

        patched = await c.patch(
            "/api/v1/google/connections/me",
            json={"gmail_sync_enabled": True, "gmail_excluded_label": "geen-crm"},
            headers=headers,
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["connection"]["gmail_sync_enabled"] is True
        assert patched.json()["connection"]["gmail_excluded_label"] == "geen-crm"

        gone = await c.post("/api/v1/google/connections/me/disconnect", headers=headers)
        assert gone.status_code == 204
        assert revoked == ["werknemer@agency.nl"]
        assert (await c.get("/api/v1/google/connections/me", headers=headers)).json()[
            "connected"
        ] is False


@dataclass
class _Ctx:
    org: object
    session: object
    user: object


async def test_callback_upsert_keeps_refresh_token_and_unions_scopes() -> None:
    t = await make_tenant("goog-upsert")
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        ctx = _Ctx(org=t.org, session=session, user=t.user)
        service = GoogleConnectionsService(ctx)  # type: ignore[arg-type]

        first = await service.upsert_from_callback(
            user_id=t.user.id,
            google_sub="sub-9",
            email="me@agency.nl",
            granted_scopes=["openid", "email", "https://www.googleapis.com/auth/drive"],
            refresh_token="rt-1",
            access_token="at-1",
            expires_at=datetime.now(UTC),
        )
        # Reconnect that adds Gmail: Google omits the refresh token, scopes must union.
        second = await service.upsert_from_callback(
            user_id=t.user.id,
            google_sub="sub-9",
            email="me@agency.nl",
            granted_scopes=[
                "openid",
                "email",
                "https://www.googleapis.com/auth/gmail.readonly",
            ],
            refresh_token=None,
            access_token="at-2",
            expires_at=datetime.now(UTC),
        )
        assert second.id == first.id
        assert decrypt(second.refresh_token_encrypted) == "rt-1"
        assert "https://www.googleapis.com/auth/drive" in second.scopes
        assert "https://www.googleapis.com/auth/gmail.readonly" in second.scopes
        await session.commit()


async def test_connection_error_notifies_owner_once() -> None:
    t = await make_tenant("goog-error")
    connection_id = await _seed_connection(t, t.user.id)
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        connection = await session.get(GoogleConnection, connection_id)
        await mark_connection_error(session, t.org, connection, "invalid_grant")
        await mark_connection_error(session, t.org, connection, "invalid_grant")
        assert connection.status == "error"

        from app.modules.notifications.models import NotificationEvent

        count = await session.scalar(
            select(func.count())
            .select_from(NotificationEvent)
            .where(NotificationEvent.org_id == t.org.id)
        )
        assert count == 1
        await session.commit()


async def test_connections_tenant_isolation(client_for) -> None:
    a = await make_tenant("goog-iso-a")
    b = await make_tenant("goog-iso-b")
    await _seed_connection(a, a.user.id)
    b_headers = await auth_cookie(b.user)
    async with client_for(b.host) as cb:
        assert (await cb.get("/api/v1/google/connections", headers=b_headers)).json() == []
