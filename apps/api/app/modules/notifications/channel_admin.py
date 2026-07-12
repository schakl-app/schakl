"""Admin CRUD for external notification channels (#17).

Only ``notifications.channels.manage`` (admin) may configure channels — they embed bot tokens and
can be pointed at arbitrary webhooks. The Apprise URL is SSRF-checked on write, encrypted at rest
(:mod:`app.core.crypto`), and never returned; the API exposes only a redacted preview and a
test-send that surfaces the provider's real error.
"""

from __future__ import annotations

import re
import uuid
from urllib.parse import parse_qs, urlsplit

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


def normalize_channel_input(kind: str, raw: str) -> str:
    """Turn what an admin actually has — the webhook URL copied from the provider — into the
    Apprise URL we store (#17 UX rebuild). Every converter also passes an already-Apprise URL
    through, so the API stays backward compatible and the "custom" kind stays raw.

    Raises :class:`ValueError` (message = i18n field key) when the input doesn't look like
    that provider's URL, so the form can point at the right field.
    """
    value = raw.strip()
    if kind == "email":
        if "@" not in value or "://" in value:
            raise ValueError("errors.notification_channel_input")
        return value
    if kind == "slack":
        if value.startswith("slack://"):
            return value
        m = re.match(r"https://hooks\.slack\.com/services/([^/]+)/([^/]+)/([^/?#]+)", value)
        if not m:
            raise ValueError("errors.notification_channel_input")
        return f"slack://{m.group(1)}/{m.group(2)}/{m.group(3)}"
    if kind == "discord":
        if value.startswith("discord://"):
            return value
        m = re.match(r"https://discord(?:app)?\.com/api/webhooks/(\d+)/([^/?#]+)", value)
        if not m:
            raise ValueError("errors.notification_channel_input")
        return f"discord://{m.group(1)}/{m.group(2)}"
    if kind == "gchat":
        if value.startswith("gchat://"):
            return value
        parts = urlsplit(value)
        m = re.match(r"/v1/spaces/([^/]+)/messages", parts.path)
        query = parse_qs(parts.query)
        key, token = query.get("key", [None])[0], query.get("token", [None])[0]
        if parts.hostname != "chat.googleapis.com" or not m or not key or not token:
            raise ValueError("errors.notification_channel_input")
        return f"gchat://{m.group(1)}/{key}/{token}"
    if kind == "msteams":
        if value.startswith(("msteams://", "workflows://")):
            return value
        # https://<team>.webhook.office.com/webhookb2/{A}@{B}/IncomingWebhook/{C}/{D}[/{E}]
        m = re.match(
            r"https://[^/]*webhook\.office\.com/webhookb2/([^/]+)/IncomingWebhook/([^/]+)/([^/?#]+)(?:/([^/?#]+))?",
            value,
        )
        if not m:
            raise ValueError("errors.notification_channel_input")
        tokens = "/".join(t for t in m.groups() if t)
        return f"msteams://{tokens}"
    if kind == "telegram":
        if value.startswith("tgram://"):
            return value
        # The form submits "<bot token>/<chat id>".
        if not re.match(r"[0-9]+:[A-Za-z0-9_-]+/.+", value):
            raise ValueError("errors.notification_channel_input")
        return f"tgram://{value}"
    if kind == "webhook":
        if value.startswith(("json://", "jsons://", "xml://", "xmls://", "form://")):
            return value
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("errors.notification_channel_input")
        scheme = "jsons" if parts.scheme == "https" else "json"
        rest = value.split("://", 1)[1]
        return f"{scheme}://{rest}"
    # mailto (legacy) and custom: whatever was pasted, verbatim — the guard still runs.
    return value


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

    def _guard_url(self, kind: str, url: str) -> str:
        """Normalize the pasted input to its stored form, then SSRF-check it."""
        try:
            normalized = normalize_channel_input(kind, url)
        except ValueError as exc:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"url": str(exc)},
            ) from exc
        if kind == "email":
            return normalized  # a recipient address, not a URL — nothing to resolve
        try:
            check_url_safe(normalized, any_scheme=kind == "custom")
        except SsrfError as exc:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"url": "errors.notification_channel_blocked"},
            ) from exc
        return normalized

    async def create(self, data: ChannelCreate) -> dict:
        self.ctx.require("notifications.channels.manage")
        stored = self._guard_url(data.kind, data.url)
        channel = await self.channels.create(
            kind=data.kind,
            name=data.name,
            url_enc=encrypt(stored),
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
            stored = self._guard_url(values.get("kind", channel.kind), data.url)
            values["url_enc"] = encrypt(stored)
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
