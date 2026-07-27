"""CRUD for external notification channels (#17, #283).

Two capabilities, deliberately distinct:

* ``notifications.channels.manage`` (admin) — the **org's** channels: the shared rooms everyone's
  events land in. They embed bot tokens and can be pointed at arbitrary webhooks, so configuring
  one is an administrative act.
* ``notifications.channels.manage_own`` (admin + member) — **my own** channel: my Slack DM, my
  ntfy topic. Connecting one is a personal setting, like my e-mail cadence, and every member has
  it. The SSRF guard is the same either way, so a member cannot reach further than an admin can.

The route declares the ``manage_own`` floor; this service refines it with the row in hand, which
is the only place the distinction can be made (CLAUDE.md §15). A member never learns that an org
channel or a colleague's channel exists: those are a **404**, not a 403.

The Apprise URL is SSRF-checked on write, encrypted at rest (:mod:`app.core.crypto`), and never
returned; the API exposes only a redacted preview and a test-send that surfaces the provider's
real error.
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
            "digest": channel.digest,
            "digest_time": channel.digest_time,
            "digest_weekday": channel.digest_weekday,
            "created_at": channel.created_at,
        }

    # --- access scoping (#283) ------------------------------------------------ #
    @property
    def _manages_org(self) -> bool:
        """Admin: sees and edits the org's shared channels as well as their own."""
        return self.ctx.can("notifications.channels.manage")

    async def _visible_or_404(self, channel_id: uuid.UUID) -> NotificationChannelConfig:
        """Load a channel the caller may act on, hiding the rest behind a 404.

        A ``require(..., owner_id)`` style assertion would raise 403 here and thereby confirm,
        to anyone who guesses an id, that the channel exists. Scope-aware *loading* is what
        keeps that from leaking (issue #19, mirroring ``time.service._owned_or_404``).
        """
        channel = await self.channels.get_or_404(channel_id)
        if not self._manages_org and channel.user_id != self.ctx.user.id:
            raise AppError("not_found", "errors.not_found", status_code=404)
        return channel

    async def list(self) -> list[dict]:
        """Every channel for an admin; only the caller's own for a plain member."""
        self.ctx.require("notifications.channels.manage_own")
        # Filter in the query, not after the fact: the 200-row page must not be spent on
        # channels the caller may not see (docs/PERFORMANCE.md).
        scope = {} if self._manages_org else {"user_id": self.ctx.user.id}
        rows = await self.channels.list(
            limit=200, order_by=NotificationChannelConfig.name, **scope
        )
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
        """A member may only ever create a channel that is **theirs**; an admin, any channel."""
        self.ctx.require("notifications.channels.manage_own")
        user_id = data.user_id
        if not self._manages_org:
            # Not a request to be refused — a member has no way to mean anything else, and the
            # web form does not send the field. Forcing it is what makes "my channels" safe.
            user_id = self.ctx.user.id
        stored = self._guard_url(data.kind, data.url)
        channel = await self.channels.create(
            kind=data.kind,
            name=data.name,
            url_enc=encrypt(stored),
            enabled=data.enabled,
            event_filter=data.event_filter,
            user_id=user_id,
            digest=data.digest,
            digest_time=data.digest_time,
            digest_weekday=data.digest_weekday,
            created_by_user_id=self.ctx.user.id,
        )
        return self._read(channel)

    async def update(self, channel_id: uuid.UUID, data: ChannelUpdate) -> dict:
        self.ctx.require("notifications.channels.manage_own")
        channel = await self._visible_or_404(channel_id)
        values = data.model_dump(exclude_unset=True, exclude={"url"})
        # ``digest`` is NOT NULL with a default; an explicit ``null`` means "leave it", not
        # "clear it" (``digest_time``/``digest_weekday`` *are* nullable and may be cleared).
        if values.get("digest") is None:
            values.pop("digest", None)
        if "url" in data.model_fields_set and data.url:
            stored = self._guard_url(values.get("kind", channel.kind), data.url)
            values["url_enc"] = encrypt(stored)
        channel = await self.channels.update(channel, **values)
        return self._read(channel)

    async def delete(self, channel_id: uuid.UUID) -> None:
        self.ctx.require("notifications.channels.manage_own")
        await self.channels.delete(await self._visible_or_404(channel_id))

    async def test(self, channel_id: uuid.UUID) -> ChannelTestResult:
        """Send a test message now and report the provider's real result — the one place a channel
        does synchronous network I/O, because it is an explicit user action, not the hot path."""
        self.ctx.require("notifications.channels.manage_own")
        channel = await self._visible_or_404(channel_id)
        from app.core.email.branding import load_brand
        from app.i18n import resolve_locale, translate

        brand = await load_brand(self.ctx.session, self.ctx.org)
        locale = resolve_locale(getattr(self.ctx.user, "locale", None))
        message = RenderedMessage(
            title=f"{brand.brand_name}: "
            + translate("settings.notifications.test_subject", locale),
            body=translate(
                "settings.notifications.test_body", locale, brand=brand.brand_name
            ),
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
                    brand=brand,
                )
            else:
                ok, error = await send_via_apprise(decrypt(channel.url_enc), message)
        except SsrfError as exc:
            return ChannelTestResult(ok=False, error=f"blocked target: {exc}")
        except Exception as exc:  # noqa: BLE001 - surface the provider failure, don't 500
            return ChannelTestResult(ok=False, error=str(exc))
        return ChannelTestResult(ok=ok, error=error)
