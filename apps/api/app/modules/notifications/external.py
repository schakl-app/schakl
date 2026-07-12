"""External notification transports via Apprise (#17).

One library, 100+ services: an Apprise URL (``slack://``, ``msteams://``, ``gchat://``,
``mailto://``, generic ``json://`` webhooks) is the whole per-channel configuration. This module
holds the SSRF guard, the message rendering (deep link + locale), the push channel that enqueues
a ``notification_deliveries`` row inside the emit transaction, and the worker-side dispatch that
actually calls the provider with retry/backoff.

Design rules honoured here:
  * ``deliver`` never does network I/O — it only writes delivery rows (CLAUDE.md channels seam);
  * generic webhook URLs are SSRF-guarded (private/link-local blocked unless explicitly allowed);
  * an *org* channel gets one message per event, not one per recipient (the digest/batching intent
    of #16), while a *personal* channel gets its owner's notifications.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

from sqlalchemy import select

from app.config import settings
from app.core.crypto import decrypt
from app.core.events import EmitContext
from app.modules.notifications.models import (
    Notification,
    NotificationChannelConfig,
    NotificationDelivery,
    NotificationEvent,
)

logger = logging.getLogger("schakl.notifications")

CHANNEL_EXTERNAL = "external"

#: Transport families we expose in the UI. ``webhook`` (generic ``json://``/``xml://``) is the
#: only one whose host is fully user-controlled, so it carries the SSRF guard. ``email`` is not
#: Apprise at all: it stores a recipient address and sends through the org's configured e-mail
#: transport (Instellingen → E-mail, ``app.core.email`` — issue #17).
KINDS: tuple[str, ...] = (
    "email", "slack", "msteams", "gchat", "discord", "telegram", "mailto", "webhook",
)

#: Schemes a channel URL may use. Blocks ``file://`` and friends outright.
_ALLOWED_SCHEMES = frozenset(
    {
        "slack", "msteams", "gchat", "discord", "tgram", "mailto", "mailtos",
        "json", "jsons", "xml", "xmls", "form", "https", "http",
    }
)

# entity_type → deep-link path (the tenant's own host is prepended). Kept tiny and explicit.
_ENTITY_PATH = {
    "task": "/tasks/{id}",
    "project": "/projects/{id}",
    "company": "/companies/{id}",
    "leave_request": "/leave",
    "timesheet": "/time",
}


class SsrfError(ValueError):
    """A channel URL points at a blocked (private/link-local/loopback) address."""


def check_url_safe(url: str) -> None:
    """Reject a channel URL whose scheme is disallowed or whose host resolves to a private range.

    A self-hosted instance sits inside a trusted network, so ``SCHAKL_ALLOW_PRIVATE_NOTIFICATION_
    TARGETS`` (default off) lets an admin opt into private targets deliberately. Named providers
    (Slack, Teams, …) use fixed public hosts and are not resolved here; the guard is for the
    generic webhook schemes whose host the user supplies.
    """
    scheme = urlsplit(url).scheme.lower()
    if scheme and scheme not in _ALLOWED_SCHEMES:
        raise SsrfError(f"scheme '{scheme}' is not allowed")
    if scheme not in {"json", "jsons", "xml", "xmls", "form", "http", "https"}:
        return  # a named provider — fixed host, nothing user-controlled to resolve
    if settings.allow_private_notification_targets:
        return
    host = urlsplit(url).hostname
    if not host:
        raise SsrfError("missing host")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise SsrfError(f"host '{host}' does not resolve") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise SsrfError(f"host '{host}' resolves to a blocked address {ip}")


@dataclass
class RenderedMessage:
    title: str
    body: str


async def _org_host(session, org) -> str:  # noqa: ANN001
    """The tenant's own base URL for deep links — its verified custom domain, else its slug host."""
    if getattr(org, "custom_domain", None) and getattr(org, "custom_domain_verified_at", None):
        return f"https://{org.custom_domain}"
    return f"https://{org.slug}.{settings.base_domain}"


async def render_message(
    session, org, event: NotificationEvent, locale: str  # noqa: ANN001
) -> RenderedMessage:
    """A deep-linked, locale-aware line for the transport.

    Deliberately terse (a chat line, not a branded email): the event type names what happened and
    a link goes straight back into the CRM. Branded HTML/MJML email is a follow-up (#17 lists it
    separately); this keeps chat + webhook channels working end-to-end now.
    """
    brand = getattr(org, "name", None) or "schakl"
    path = _ENTITY_PATH.get(event.entity_type, "/")
    link = (await _org_host(session, org)) + path.format(id=event.entity_id)
    if locale.startswith("nl"):
        title = f"{brand}: nieuwe melding"
        body = f"Activiteit: {event.event_type}\n{link}"
    else:
        title = f"{brand}: new notification"
        body = f"Activity: {event.event_type}\n{link}"
    return RenderedMessage(title=title, body=body)


class ExternalChannel:
    """Push channel: enqueues one ``notification_deliveries`` row per matching configured channel.

    Runs inside the emit transaction — DB writes only, never a provider call. An org channel is
    written once per event (deduped across the event's recipients); a personal channel is written
    for its owner's notifications.
    """

    key = CHANNEL_EXTERNAL

    async def deliver(
        self,
        ctx: EmitContext,
        *,
        notification_id: uuid.UUID,
        user_id: uuid.UUID,
        event_type: str,
    ) -> None:
        session = ctx.session
        org_id = ctx.org.id
        configs = (
            (
                await session.execute(
                    select(NotificationChannelConfig).where(
                        NotificationChannelConfig.org_id == org_id,
                        NotificationChannelConfig.enabled.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        if not configs:
            return
        notification = await session.get(Notification, notification_id)
        for config in configs:
            if config.event_filter and event_type not in config.event_filter:
                continue
            if config.user_id is not None and config.user_id != user_id:
                continue  # a personal channel only receives its owner's notifications
            if config.user_id is None and await self._org_channel_already_queued(
                session, org_id, config.id, notification.event_id
            ):
                continue  # one message per event for a shared room, not one per recipient
            session.add(
                NotificationDelivery(
                    org_id=org_id,
                    notification_id=notification_id,
                    channel=CHANNEL_EXTERNAL,
                    channel_config_id=config.id,
                    status="pending",
                )
            )

    async def _org_channel_already_queued(
        self, session, org_id, config_id, event_id  # noqa: ANN001
    ) -> bool:
        existing = await session.scalar(
            select(NotificationDelivery.id)
            .join(Notification, Notification.id == NotificationDelivery.notification_id)
            .where(
                NotificationDelivery.org_id == org_id,
                NotificationDelivery.channel_config_id == config_id,
                Notification.event_id == event_id,
            )
            .limit(1)
        )
        return existing is not None


# --------------------------------------------------------------------------- #
# Worker-side dispatch (the only place a provider is actually called)
# --------------------------------------------------------------------------- #
MAX_ATTEMPTS = 5


def _backoff_ready(delivery: NotificationDelivery, now: datetime) -> bool:
    """Exponential backoff between attempts: 1, 2, 4, 8 … minutes off ``updated_at``."""
    if delivery.attempts == 0:
        return True
    wait_minutes = 2 ** (delivery.attempts - 1)
    updated = delivery.updated_at
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    return (now - updated).total_seconds() >= wait_minutes * 60


async def send_via_apprise(url: str, message: RenderedMessage) -> tuple[bool, str | None]:
    """Call the provider. Returns ``(ok, error)``; ``error`` carries the provider's own message.

    Apprise's ``notify`` returns only a bool, so the real reason is scraped from the Apprise
    logger during the call — which is exactly what the test-send button needs to show.
    """
    import apprise

    check_url_safe(url)
    obj = apprise.Apprise()
    if not obj.add(url):
        return False, "invalid channel URL"

    records: list[str] = []
    handler = logging.Handler()
    handler.setLevel(logging.WARNING)
    handler.emit = lambda record: records.append(record.getMessage())  # type: ignore[method-assign]
    apprise_logger = logging.getLogger("apprise")
    apprise_logger.addHandler(handler)
    try:
        ok = await obj.async_notify(body=message.body, title=message.title)
    finally:
        apprise_logger.removeHandler(handler)
    if ok:
        return True, None
    return False, records[-1] if records else "delivery failed"


async def dispatch_delivery(session, delivery: NotificationDelivery) -> None:  # noqa: ANN001
    """Attempt one pending delivery, updating its status/attempts/last_error in place."""
    now = datetime.now(UTC)
    if not _backoff_ready(delivery, now):
        return
    config = await session.get(NotificationChannelConfig, delivery.channel_config_id)
    notification = await session.get(Notification, delivery.notification_id)
    if config is None or notification is None:
        delivery.status = "failed"
        delivery.last_error = "channel or notification no longer exists"
        return
    event = await session.get(NotificationEvent, notification.event_id)
    # Personal channel → the owner's locale; a shared room → the org default.
    locale = settings.default_locale
    if config.user_id is not None:
        from app.core.auth.models import User

        owner = await session.get(User, config.user_id)
        locale = owner.locale if owner and owner.locale else settings.default_locale

    from app.core.models import Org

    org = await session.get(Org, delivery.org_id)
    message = await render_message(session, org, event, locale)

    delivery.attempts += 1
    try:
        if config.kind == "email":
            from app.core.email.senders import OutgoingEmail
            from app.core.email.service import send_org_email

            ok, error = await send_org_email(
                session,
                delivery.org_id,
                OutgoingEmail(
                    to=decrypt(config.url_enc), subject=message.title, text=message.body
                ),
            )
        else:
            ok, error = await send_via_apprise(decrypt(config.url_enc), message)
    except SsrfError as exc:
        ok, error = False, f"blocked target: {exc}"
    except Exception as exc:  # noqa: BLE001 - a provider hiccup must not kill the sweep
        ok, error = False, str(exc)
    if ok:
        delivery.status = "sent"
        delivery.sent_at = now
        delivery.last_error = None
    else:
        delivery.last_error = error
        delivery.status = "failed" if delivery.attempts >= MAX_ATTEMPTS else "pending"
