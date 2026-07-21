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

import logging
import socket
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

from sqlalchemy import or_, select

from app.config import settings
from app.core.crypto import decrypt
from app.core.events import EmitContext
from app.core.net_guard import is_public_address
from app.i18n import translate
from app.modules.notifications.events import CHANNEL_EMAIL
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
    "email",
    "slack",
    "msteams",
    "gchat",
    "discord",
    "telegram",
    "mailto",
    "webhook",
    "custom",
)

#: Schemes a channel URL may use. Blocks ``file://`` and friends outright.
_ALLOWED_SCHEMES = frozenset(
    {
        "slack",
        "msteams",
        "gchat",
        "discord",
        "tgram",
        "mailto",
        "mailtos",
        "json",
        "jsons",
        "xml",
        "xmls",
        "form",
        "https",
        "http",
    }
)

class SsrfError(ValueError):
    """A channel URL points at a blocked (private/link-local/loopback) address."""


def check_url_safe(url: str, *, any_scheme: bool = False) -> None:
    """Reject a channel URL whose scheme is disallowed or whose host resolves to a private range.

    A self-hosted instance sits inside a trusted network, so ``SCHAKL_ALLOW_PRIVATE_NOTIFICATION_
    TARGETS`` (default off) lets an admin opt into private targets deliberately. Named providers
    (Slack, Teams, …) use fixed public hosts and are not resolved here; the guard is for the
    generic webhook schemes whose host the user supplies. ``any_scheme`` is the "custom Apprise
    URL" escape hatch: the scheme allowlist is skipped (Apprise knows ~100 of them), the
    private-host check for web schemes stays.
    """
    scheme = urlsplit(url).scheme.lower()
    if not any_scheme and scheme and scheme not in _ALLOWED_SCHEMES:
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
        if not is_public_address(info[4][0]):
            raise SsrfError(f"host '{host}' resolves to a blocked address {info[4][0]}")


@dataclass
class RenderedMessage:
    title: str
    body: str
    #: The e-mail content fragment; chat transports use ``body``, the send seam wraps this
    #: in the org's branded chrome (#236).
    html: str | None = None


async def _actor_name(session, actor_user_id) -> str | None:  # noqa: ANN001
    """How the mail names the acting person; ``None`` means the system acted."""
    if actor_user_id is None:
        return None
    from app.core.auth.models import User

    user = await session.get(User, actor_user_id)
    if user is None:
        return None
    return user.full_name or user.email


async def render_message(
    session,
    org,
    event: NotificationEvent,
    locale: str,  # noqa: ANN001
) -> RenderedMessage:
    """A deep-linked, locale-aware message for one event (#236).

    The body is the same sentence the in-app feed shows (``render.event_sentence`` — the
    server twin of the web's ``format.ts``), prefixed with the actor, plus a deep link back
    into the CRM. Chat transports send it as-is; e-mail also carries an HTML fragment that
    the send seam wraps in the org's branding.
    """
    from app.core.email.branding import load_brand
    from app.modules.notifications.render import email_fragment, event_path, event_sentence

    brand = await load_brand(session, org)
    actor = await _actor_name(session, event.actor_user_id)
    sentence = event_sentence(event, actor, locale)
    path = event_path(event)
    link = brand.base_url + path if path else None
    title = f"{brand.brand_name}: " + translate("notifications.email.title", locale)
    body = f"{sentence}\n{link}" if link else sentence
    html = email_fragment([(sentence, link)], brand.primary_color, locale)
    return RenderedMessage(title=title, body=body, html=html)


class ExternalChannel:
    """Push channel: enqueues one ``notification_deliveries`` row per matching configured channel.

    Runs inside the emit transaction — DB writes only, never a provider call. An org channel is
    written once per event (the batch is the event's whole audience, so the first row stands in
    for the room); a personal channel is written for its owner's notifications.
    """

    key = CHANNEL_EXTERNAL

    async def deliver(
        self,
        ctx: EmitContext,
        *,
        event: NotificationEvent,
        notifications: Sequence[Notification],
    ) -> None:
        if not notifications:
            return
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
        by_user = {row.user_id: row for row in notifications}
        for config in configs:
            if config.event_filter and event.event_type not in config.event_filter:
                continue
            if config.user_id is not None:
                # A personal channel only receives its owner's notifications.
                target = by_user.get(config.user_id)
            else:
                # One message per event for a shared room, not one per recipient.
                target = notifications[0]
            if target is None:
                continue
            session.add(
                NotificationDelivery(
                    org_id=org_id,
                    notification_id=target.id,
                    channel=CHANNEL_EXTERNAL,
                    channel_config_id=config.id,
                    status="pending",
                )
            )


class EmailChannel:
    """Personal e-mail (#17): one delivery row per notification the recipient opted into.

    The recipient's *general* e-mail preference decides cadence: ``immediate`` rows are due
    at once, digest rows carry ``deliver_after`` — the worker holds them and sends everything
    due for a user as **one** mail. Same DB-only rule as every channel: no I/O here, and the
    whole batch resolves its preferences in one query (never one per recipient).
    """

    key = CHANNEL_EMAIL

    async def deliver(
        self,
        ctx: EmitContext,
        *,
        event: NotificationEvent,
        notifications: Sequence[Notification],
    ) -> None:
        from app.modules.notifications.prefs import compute_visible_at, email_prefs_for_recipients

        if not notifications:
            return
        prefs = await email_prefs_for_recipients(
            ctx.session, ctx.org.id, [row.user_id for row in notifications]
        )
        now = datetime.now(UTC)
        for row in notifications:
            pref = prefs.get(row.user_id)
            if pref is None or not pref.enabled:
                continue
            ctx.session.add(
                NotificationDelivery(
                    org_id=ctx.org.id,
                    notification_id=row.id,
                    channel=CHANNEL_EMAIL,
                    status="pending",
                    deliver_after=compute_visible_at(pref, now),
                )
            )


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
                    to=decrypt(config.url_enc),
                    subject=message.title,
                    text=message.body,
                    html=message.html,
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


async def dispatch_email_deliveries(session, org) -> None:  # noqa: ANN001
    """Send every due e-mail delivery, one mail per recipient (#17).

    Grouping is what makes a digest: a daily-cadence user's rows all carry the same
    ``deliver_after`` slot, so when it passes they surface together and leave as a single
    message. An immediate-cadence user simply gets a group of one. Failures keep the rows
    pending with the provider's error, riding the same backoff as every delivery.
    """
    now = datetime.now(UTC)
    rows = (
        await session.execute(
            select(NotificationDelivery, Notification)
            .join(Notification, Notification.id == NotificationDelivery.notification_id)
            .where(
                NotificationDelivery.org_id == org.id,
                NotificationDelivery.channel == CHANNEL_EMAIL,
                NotificationDelivery.status == "pending",
                NotificationDelivery.attempts < MAX_ATTEMPTS,
                or_(
                    NotificationDelivery.deliver_after.is_(None),
                    NotificationDelivery.deliver_after <= now,
                ),
            )
            .order_by(NotificationDelivery.created_at.asc())
            .limit(200)
        )
    ).all()
    if not rows:
        return

    from app.core.auth.models import User
    from app.core.email.branding import load_brand
    from app.core.email.senders import OutgoingEmail
    from app.core.email.service import send_org_email
    from app.modules.notifications.render import email_fragment, event_path, event_sentence

    brand = await load_brand(session, org)
    groups: dict[uuid.UUID, list[tuple[NotificationDelivery, Notification]]] = {}
    for delivery, notification in rows:
        groups.setdefault(notification.user_id, []).append((delivery, notification))

    for user_id, items in groups.items():
        ready = [pair for pair in items if _backoff_ready(pair[0], now)]
        if not ready:
            continue
        user = await session.get(User, user_id)
        if user is None or not user.email:
            for delivery, _ in ready:
                delivery.status = "failed"
                delivery.last_error = "recipient no longer exists"
            continue
        locale = user.locale if getattr(user, "locale", None) else settings.default_locale

        rendered: list[tuple[str, str | None]] = []
        for _, notification in ready:
            event = await session.get(NotificationEvent, notification.event_id)
            if event is None:
                continue
            actor = await _actor_name(session, event.actor_user_id)
            sentence = event_sentence(event, actor, locale)
            path = event_path(event)
            rendered.append((sentence, brand.base_url + path if path else None))
        if len(rendered) == 1:
            # The sentence itself is the best subject a single notification can have.
            subject = rendered[0][0]
        else:
            subject = translate(
                "notifications.email.digest_subject", locale, count=len(rendered)
            )
        if not subject.startswith(brand.brand_name):
            subject = f"{brand.brand_name}: {subject}"
        text = "\n\n".join(
            f"{sentence}\n{link}" if link else sentence for sentence, link in rendered
        )
        html = email_fragment(rendered, brand.primary_color, locale)

        for delivery, _ in ready:
            delivery.attempts += 1
        try:
            ok, error = await send_org_email(
                session,
                org.id,
                OutgoingEmail(to=user.email, subject=subject, text=text, html=html),
                brand=brand,
            )
        except Exception as exc:  # noqa: BLE001 - one recipient must not kill the sweep
            ok, error = False, str(exc)
        for delivery, _ in ready:
            if ok:
                delivery.status = "sent"
                delivery.sent_at = now
                delivery.last_error = None
            else:
                delivery.last_error = error
                delivery.status = "failed" if delivery.attempts >= MAX_ATTEMPTS else "pending"
