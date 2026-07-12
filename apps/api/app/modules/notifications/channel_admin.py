"""Admin CRUD for external notification channels (#17).

Only ``notifications.channels.manage`` (admin) may configure channels — they embed bot tokens and
can be pointed at arbitrary webhooks. The Apprise URL is SSRF-checked on write, encrypted at rest
(:mod:`app.core.crypto`), and never returned; the API exposes only a redacted preview and a
test-send that surfaces the provider's real error.
"""

from __future__ import annotations

import uuid
from urllib.parse import urlsplit

from app.core.crypto import decrypt, encrypt
from app.core.tenancy import RequestContext
from app.errors import AppError
from app.modules.notifications.external import (
    RenderedMessage,
    SsrfError,
    check_url_safe,
    send_via_apprise,
)
from app.modules.notifications.models import NotificationChannelConfig
from app.modules.notifications.schemas import ChannelCreate, ChannelTestResult, ChannelUpdate


def redact(url: str) -> str:
    """``slack://xoxb-****`` — enough to recognise the channel, nothing to leak.

    An ``email`` channel stores a bare recipient address, not a secret URL: mask only the
    local part (``t***@agency.nl``) so the admin can still tell channels apart.
    """
    parts = urlsplit(url)
    if not parts.scheme and "@" in url:
        local, _, domain = url.partition("@")
        return f"{local[:1]}***@{domain}"
    scheme = parts.scheme or "?"
    hint = (parts.netloc or parts.path)[:6]
    return f"{scheme}://{hint}****"


class ChannelService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.channels = ctx.repo(NotificationChannelConfig)

    def _read(self, channel: NotificationChannelConfig) -> dict:
        return {
            "id": channel.id,
            "org_id": channel.org_id,
            "kind": channel.kind,
            "name": channel.name,
            "redacted": redact(decrypt(channel.url_enc)),
            "enabled": channel.enabled,
            "event_filter": list(channel.event_filter),
            "user_id": channel.user_id,
            "created_at": channel.created_at,
        }

    async def list(self) -> list[dict]:
        self.ctx.require("notifications.channels.manage")
        rows = await self.channels.list(limit=200, order_by=NotificationChannelConfig.name)
        return [self._read(c) for c in rows]

    def _guard_url(self, kind: str, url: str) -> None:
        if kind == "email":
            # A recipient address for the org's own transport (#17) — not an Apprise URL.
            if "@" not in url or "://" in url:
                raise AppError(
                    "validation",
                    "errors.validation",
                    status_code=422,
                    fields={"url": "errors.validation"},
                )
            return
        try:
            check_url_safe(url)
        except SsrfError as exc:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"url": "errors.notification_channel_blocked"},
            ) from exc

    async def create(self, data: ChannelCreate) -> dict:
        self.ctx.require("notifications.channels.manage")
        self._guard_url(data.kind, data.url)
        channel = await self.channels.create(
            kind=data.kind,
            name=data.name,
            url_enc=encrypt(data.url),
            enabled=data.enabled,
            event_filter=data.event_filter,
            user_id=data.user_id,
            created_by_user_id=self.ctx.user.id,
        )
        return self._read(channel)

    async def update(self, channel_id: uuid.UUID, data: ChannelUpdate) -> dict:
        self.ctx.require("notifications.channels.manage")
        channel = await self.channels.get_or_404(channel_id)
        values = data.model_dump(exclude_unset=True, exclude={"url"})
        if "url" in data.model_fields_set and data.url:
            self._guard_url(values.get("kind", channel.kind), data.url)
            values["url_enc"] = encrypt(data.url)
        channel = await self.channels.update(channel, **values)
        return self._read(channel)

    async def delete(self, channel_id: uuid.UUID) -> None:
        self.ctx.require("notifications.channels.manage")
        await self.channels.delete(await self.channels.get_or_404(channel_id))

    async def test(self, channel_id: uuid.UUID) -> ChannelTestResult:
        """Send a test message now and report the provider's real result — the one place a channel
        does synchronous network I/O, because it is an explicit admin action, not the hot path."""
        self.ctx.require("notifications.channels.manage")
        channel = await self.channels.get_or_404(channel_id)
        brand = getattr(self.ctx.org, "name", None) or "schakl"
        message = RenderedMessage(
            title=f"{brand}: test", body="This is a test notification from schakl."
        )
        try:
            if channel.kind == "email":
                from app.core.email.senders import OutgoingEmail
                from app.core.email.service import send_org_email

                ok, error = await send_org_email(
                    self.ctx.session,
                    self.ctx.org.id,
                    OutgoingEmail(
                        to=decrypt(channel.url_enc), subject=message.title, text=message.body
                    ),
                )
            else:
                ok, error = await send_via_apprise(decrypt(channel.url_enc), message)
        except SsrfError as exc:
            return ChannelTestResult(ok=False, error=f"blocked target: {exc}")
        except Exception as exc:  # noqa: BLE001 - surface the provider failure, don't 500
            return ChannelTestResult(ok=False, error=str(exc))
        return ChannelTestResult(ok=ok, error=error)
