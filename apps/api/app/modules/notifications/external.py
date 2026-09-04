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
    of #16), while a *personal* channel gets its owner's notifications;
  * **every** transport batches (#283). A delivery row carries ``deliver_after`` from its
    channel's cadence, and the worker sends everything due for one channel as a single message —
    the digest machinery personal e-mail has had since #17, generalised to all of Apprise;
  * **one channel, one routing table** (#295). Every channel — a shared ``#crm`` room as much as
    someone's Slack DM — is routed per event from the matrix of the scope that owns it. The
    ``event_filter`` + channel-level ``digest`` a shared room used to route by are no longer read.
"""

from __future__ import annotations

import logging
import socket
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from urllib.parse import urlsplit

from sqlalchemy import or_, select

from app.config import settings
from app.core.crypto import decrypt
from app.core.events import EmitContext
from app.core.net_guard import is_public_address
from app.core.timezone import org_zoneinfo
from app.i18n import translate_count
from app.modules.notifications.events import (
    CHANNEL_EMAIL,
    CHANNEL_EXTERNAL,
    DIGEST_IMMEDIATE,
    PORTAL_EVENTS,
)
from app.modules.notifications.models import (
    Notification,
    NotificationChannelConfig,
    NotificationDelivery,
    NotificationEvent,
)

logger = logging.getLogger("schakl.notifications")

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


class ExternalChannel:
    """Push channel: enqueues one ``notification_deliveries`` row per matching configured channel.

    Runs inside the emit transaction — DB writes only, never a provider call. An org channel is
    written once per event (the batch is the event's whole audience, so the first row stands in
    for the room); a personal channel is written for its owner's notifications.

    Every row carries ``deliver_after``. The worker holds it until the slot passes and then sends
    everything due for that channel as **one** message — the same digest machinery personal
    e-mail has had since #17, now for every Apprise transport. An ``immediate`` channel simply
    lands a slot of "now" and leaves on the next tick.

    **Routing and cadence come from one place for every channel** (#295): the per-event preference
    row for *this* channel, in the matrix of the scope that owns it — the owner's for a personal
    transport, the org's for a shared room. ``event_filter`` and the channel-level ``digest`` were
    the shared room's separate answer to the same two questions and are no longer read; two
    routing mechanisms on one table were two places to look when something did not arrive, and
    only one of them could say "bundle Slack daily, like e-mail".

    Which notification a delivery hangs off is the one thing that still differs, and it is not a
    preference: a personal channel takes its owner's row, a shared room takes the first of the
    batch, because the room's message stands in for the whole audience rather than one recipient.
    """

    key = CHANNEL_EXTERNAL

    async def deliver(
        self,
        ctx: EmitContext,
        *,
        event: NotificationEvent,
        notifications: Sequence[Notification],
    ) -> None:
        from app.modules.notifications.prefs import (
            compute_visible_at,
            resolve_channel_prefs,
        )

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
        now = datetime.now(UTC)
        # This org's wall clock decides where a digest slot falls (CLAUDE.md §8).
        tz = await org_zoneinfo(session, org_id)
        by_user = {row.user_id: row for row in notifications}
        # Every channel's rule for this event in one query — never one per channel.
        channel_prefs = await resolve_channel_prefs(session, org_id, event.event_type, configs)

        for config in configs:
            pref = channel_prefs.get(config.id)
            if pref is None or not pref.enabled:
                continue
            # A personal channel only ever carries its owner's notifications.
            target = notifications[0] if config.user_id is None else by_user.get(config.user_id)
            if target is None:
                continue
            session.add(
                NotificationDelivery(
                    org_id=org_id,
                    notification_id=target.id,
                    channel=CHANNEL_EXTERNAL,
                    channel_config_id=config.id,
                    status="pending",
                    # Quiet hours apply to every *pushed* channel and not to the bell (#309):
                    # a room or a phone that pings at 03:00 is what the setting was collected
                    # for, and it had been stored and read by nothing since #16.
                    deliver_after=compute_visible_at(pref, now, tz=tz, quiet_hours=True),
                )
            )


class EmailChannel:
    """Personal e-mail (#245): one delivery row per notification the recipient opted into.

    The recipient's **per-event** e-mail preference decides whether this event mails and at
    what cadence: ``immediate`` rows are due at once, digest rows carry ``deliver_after`` — the
    worker holds them and sends everything due for a user as **one** mail. E-mail is a subset of
    in-app: it fans out from the freshly-written inbox rows, so an event the recipient switched
    off in-app never reaches this channel. Same DB-only rule as every channel: no I/O here, and
    the whole batch resolves its preferences in one query (never one per recipient).
    """

    key = CHANNEL_EMAIL

    async def deliver(
        self,
        ctx: EmitContext,
        *,
        event: NotificationEvent,
        notifications: Sequence[Notification],
    ) -> None:
        from app.modules.notifications.prefs import (
            compute_visible_at,
            resolve_email_for_recipients,
        )

        if not notifications:
            return
        prefs = await resolve_email_for_recipients(
            ctx.session, ctx.org.id, event.event_type, [row.user_id for row in notifications]
        )
        # A client's contact person is not in the app to read a bell: for a login from outside
        # the agency the mail is the notification, so a portal-facing event mails at once unless
        # that person (or the org) has said otherwise. Staff keep the silent default — one
        # query over the batch, never one per recipient.
        external: set[uuid.UUID] = set()
        if event.event_type in PORTAL_EVENTS:
            from app.core.portal import external_user_ids

            external = await external_user_ids(
                ctx.session, ctx.org.id, {row.user_id for row in notifications}
            )
        now = datetime.now(UTC)
        # Same rule as the external channel: the org's calendar, resolved once.
        tz = await org_zoneinfo(ctx.session, ctx.org.id)
        for row in notifications:
            pref = prefs.get(row.user_id)
            if pref is not None and row.user_id in external and pref.source == "default":
                pref = replace(pref, enabled=True, delay_minutes=0, digest=DIGEST_IMMEDIATE)
            if pref is None or not pref.enabled:
                continue
            ctx.session.add(
                NotificationDelivery(
                    org_id=ctx.org.id,
                    notification_id=row.id,
                    channel=CHANNEL_EMAIL,
                    status="pending",
                    # Pushed channel → quiet hours apply (#309); see ExternalChannel above.
                    deliver_after=compute_visible_at(pref, now, tz=tz, quiet_hours=True),
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


async def build_digest_message(
    session,  # noqa: ANN001
    brand,  # noqa: ANN001
    notifications: Sequence[Notification],
    locale: str,
) -> RenderedMessage | None:
    """Bundle N notifications into **one** message in ``locale`` (#283).

    This is what makes a digest a digest, and it is deliberately transport-agnostic: chat
    transports send ``body`` (``send_via_apprise`` accepts a multi-line body), e-mail sends
    ``html`` wrapped in the org's chrome at the send seam. A single item keeps its own sentence
    as the title — that is the best subject one notification can have; several fall back to the
    counted digest subject.

    ``None`` means every underlying event has since been deleted, so there is nothing to say.
    """
    from app.modules.notifications.render import email_fragment, event_path, event_sentence

    rendered: list[tuple[str, str | None]] = []
    for notification in notifications:
        event = await session.get(NotificationEvent, notification.event_id)
        if event is None:
            continue
        actor = await _actor_name(session, event.actor_user_id)
        sentence = event_sentence(event, actor, locale)
        path = event_path(event)
        rendered.append((sentence, brand.base_url + path if path else None))
    if not rendered:
        return None

    if len(rendered) == 1:
        title = rendered[0][0]
    else:
        title = translate_count("notifications.email.digest_subject", locale, count=len(rendered))
    if not title.startswith(brand.brand_name):
        title = f"{brand.brand_name}: {title}"
    body = "\n\n".join(f"{sentence}\n{link}" if link else sentence for sentence, link in rendered)
    return RenderedMessage(
        title=title, body=body, html=email_fragment(rendered, brand.primary_color, locale)
    )


def _settle(
    ready: Sequence[tuple[NotificationDelivery, Notification]],
    *,
    ok: bool,
    error: str | None,
    now: datetime,
) -> None:
    """Mark a whole bundle sent or failed together — they left as one message."""
    for delivery, _ in ready:
        if ok:
            delivery.status = "sent"
            delivery.sent_at = now
            delivery.last_error = None
        else:
            delivery.last_error = error
            delivery.status = "failed" if delivery.attempts >= MAX_ATTEMPTS else "pending"


def _still_active(
    ready: Sequence[tuple[NotificationDelivery, Notification]],
) -> list[tuple[NotificationDelivery, Notification]]:
    """Keep only deliveries whose notification is still unread, settling the rest ``skipped``.

    A digest is a summary of what still wants the recipient's attention, so a notification the
    recipient already **read** — or that was **resolved**, which marks it read for everyone it
    was pending on (#170: a decided leave request, a finished-and-so-no-longer-overdue task) —
    between its slot being scheduled and this sweep is no longer news. Mailing it tells someone
    about something they have already dealt with, which is exactly the "several e-mails about
    things I handled" a personal digest is supposed to spare them.

    The window this closes is real: a daily row's in-app twin becomes visible at the same 08:00
    its e-mail is due, but an ``immediate`` row carries a grace delay, and a resolve fires the
    moment the underlying item is acted on — so between emit and send the twin is routinely read.

    A skipped delivery is settled terminally (distinct from ``sent``: nothing left the building)
    so it neither lingers ``pending`` for the next sweep nor reads as a message that went out.
    Applied only where a delivery hangs off *the recipient's own* notification row — never a
    shared room, whose message stands in for a whole audience and whose stand-in row's read
    state says nothing about the room (see ``dispatch_external_deliveries``).
    """
    active: list[tuple[NotificationDelivery, Notification]] = []
    for delivery, notification in ready:
        if notification.read_at is not None:
            delivery.status = "skipped"
            delivery.last_error = None
        else:
            active.append((delivery, notification))
    return active


def _due(channel: str, org_id: uuid.UUID, now: datetime):  # noqa: ANN202
    """Every pending, not-exhausted, past-its-slot delivery on one channel, oldest first."""
    return (
        select(NotificationDelivery, Notification)
        .join(Notification, Notification.id == NotificationDelivery.notification_id)
        .where(
            NotificationDelivery.org_id == org_id,
            NotificationDelivery.channel == channel,
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


async def dispatch_email_deliveries(session, org) -> None:  # noqa: ANN001
    """Send every due e-mail delivery, one mail per recipient (#17).

    Grouping is what makes a digest: a daily-cadence user's rows all carry the same
    ``deliver_after`` slot, so when it passes they surface together and leave as a single
    message. An immediate-cadence user simply gets a group of one. Failures keep the rows
    pending with the provider's error, riding the same backoff as every delivery.
    """
    now = datetime.now(UTC)
    rows = (await session.execute(_due(CHANNEL_EMAIL, org.id, now))).all()
    if not rows:
        return

    from app.core.auth.models import User
    from app.core.email.branding import load_brand
    from app.core.email.senders import OutgoingEmail
    from app.core.email.service import send_org_email

    brand = await load_brand(session, org)
    groups: dict[uuid.UUID, list[tuple[NotificationDelivery, Notification]]] = {}
    for delivery, notification in rows:
        groups.setdefault(notification.user_id, []).append((delivery, notification))

    for user_id, items in groups.items():
        ready = [pair for pair in items if _backoff_ready(pair[0], now)]
        if not ready:
            continue
        # A personal inbox: drop what the recipient has already read/resolved (#170), and if the
        # whole bundle is stale send nothing rather than a mail about handled work.
        ready = _still_active(ready)
        if not ready:
            continue
        user = await session.get(User, user_id)
        if user is None or not user.email:
            for delivery, _ in ready:
                delivery.status = "failed"
                delivery.last_error = "recipient no longer exists"
            continue
        locale = user.locale if getattr(user, "locale", None) else settings.default_locale

        message = await build_digest_message(
            session, brand, [notification for _, notification in ready], locale
        )
        if message is None:
            for delivery, _ in ready:
                delivery.status = "failed"
                delivery.last_error = "notification no longer exists"
            continue

        for delivery, _ in ready:
            delivery.attempts += 1
        try:
            ok, error = await send_org_email(
                session,
                org.id,
                OutgoingEmail(
                    to=user.email,
                    subject=message.title,
                    text=message.body,
                    html=message.html,
                ),
                brand=brand,
            )
        except Exception as exc:  # noqa: BLE001 - one recipient must not kill the sweep
            ok, error = False, str(exc)
        _settle(ready, ok=ok, error=error, now=now)


async def dispatch_external_deliveries(session, org) -> None:  # noqa: ANN001
    """Send every due external delivery, **one message per channel** (#283).

    The e-mail sweep next door groups by recipient; this one groups by ``channel_config_id``,
    because a shared room has no single recipient — the whole point of a room is that several
    people's notifications land in it. One group is one message: a ``#crm`` channel on the daily
    cadence gets the day's twelve events as twelve lines, not twelve pings.

    An ``immediate`` channel bundles whatever accumulated within one cron tick, which is a group
    of one in practice and exactly how personal e-mail has always behaved. Failures keep the
    whole bundle pending with the provider's error and ride the shared backoff.
    """
    now = datetime.now(UTC)
    rows = (await session.execute(_due(CHANNEL_EXTERNAL, org.id, now))).all()
    if not rows:
        return

    from app.core.auth.models import User
    from app.core.email.branding import load_brand

    brand = await load_brand(session, org)
    groups: dict[uuid.UUID | None, list[tuple[NotificationDelivery, Notification]]] = {}
    for delivery, notification in rows:
        groups.setdefault(delivery.channel_config_id, []).append((delivery, notification))

    for config_id, items in groups.items():
        ready = [pair for pair in items if _backoff_ready(pair[0], now)]
        if not ready:
            continue
        config = (
            await session.get(NotificationChannelConfig, config_id)
            if config_id is not None
            else None
        )
        if config is None:
            for delivery, _ in ready:
                delivery.status = "failed"
                delivery.last_error = "channel no longer exists"
            continue

        # A personal transport (someone's own Slack DM or e-mail) is an inbox, so a read/resolved
        # notification is dropped exactly as the personal e-mail sweep drops it. A shared room is
        # not: its message stands in for the whole audience and the row it hangs off is the first
        # of the batch, whose read state says nothing about the room (#170).
        if config.user_id is not None:
            ready = _still_active(ready)
            if not ready:
                continue

        # Personal channel → the owner's locale; a shared room → the org default.
        locale = settings.default_locale
        if config.user_id is not None:
            owner = await session.get(User, config.user_id)
            locale = owner.locale if owner and owner.locale else settings.default_locale

        message = await build_digest_message(
            session, brand, [notification for _, notification in ready], locale
        )
        if message is None:
            for delivery, _ in ready:
                delivery.status = "failed"
                delivery.last_error = "notification no longer exists"
            continue

        for delivery, _ in ready:
            delivery.attempts += 1
        try:
            if config.kind == "email":
                from app.core.email.senders import OutgoingEmail
                from app.core.email.service import send_org_email

                ok, error = await send_org_email(
                    session,
                    org.id,
                    OutgoingEmail(
                        to=decrypt(config.url_enc),
                        subject=message.title,
                        text=message.body,
                        html=message.html,
                    ),
                    brand=brand,
                )
            else:
                ok, error = await send_via_apprise(decrypt(config.url_enc), message)
        except SsrfError as exc:
            ok, error = False, f"blocked target: {exc}"
        except Exception as exc:  # noqa: BLE001 - one channel must not kill the sweep
            ok, error = False, str(exc)
        _settle(ready, ok=ok, error=error, now=now)
