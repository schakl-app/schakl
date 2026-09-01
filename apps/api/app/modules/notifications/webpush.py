"""Browser push notifications as a delivery channel (#309).

The fifth channel, and the first one that can reach someone whose laptop is shut. It is
**implicit** like the bell and personal e-mail — every member has it, there is nothing to
connect — but unlike them it has *devices*: a ``push_subscriptions`` row per browser.

Deliberately not a ``notification_channels`` row (see ``docs/WEBPUSH.md`` §2). A subscription is
nothing a person types: a browser mints it, it belongs to a device rather than a person, it
rotates without warning, and it dies with a ``410 Gone``. As a channel row an ordinary auto-prune
would delete a user's channel — and cascade away the preference rows carrying its routing.

Everything else is the machinery that already exists: the delivery row is written inside the emit
transaction with the recipient's cadence in ``deliver_after``, and the per-org cron drains it,
groups it, and renders one bundled message with ``external.build_digest_message``.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import webpush
from app.core.crypto import decrypt, encrypt
from app.core.events import EmitContext
from app.core.net_guard import SsrfBlocked
from app.core.tenancy import RequestContext
from app.core.timezone import org_zoneinfo
from app.modules.notifications.events import CHANNEL_WEB_PUSH
from app.modules.notifications.models import (
    Notification,
    NotificationDelivery,
    NotificationEvent,
    PushSubscription,
    PushVapidKey,
)

logger = logging.getLogger("schakl.notifications")

#: What a push service is told to contact if this application server misbehaves (RFC 8292 §2.1).
#: Not a real mailbox on most installs and not required to be — it reaches no browser and is not
#: a secret. A per-org address would leak the tenant's contact to Google on every send.
VAPID_SUBJECT = "mailto:noreply@schakl.app"

#: The body of a bundled push, before encryption. A push service must accept 4096 bytes of
#: *encrypted* record, and the JSON has to fit inside that with room for the header and tag —
#: so the sentences are truncated here rather than discovered to be too long at the send.
MAX_BODY_CHARS = 2400


async def vapid_keys(session: AsyncSession, org_id: uuid.UUID) -> webpush.VapidKeys:
    """This org's application-server keypair, minting one on first use.

    Lazy rather than configured: two env vars plus a keygen command would mean the feature is
    silently off after every unattended upgrade (``docs/WORKFLOW.md``), which is the worst of the
    available failure modes — nobody would find out until they wondered why nothing arrived.

    Never rotated. A browser binds its subscription to the ``applicationServerKey`` it subscribed
    with, so replacing this keypair does not re-key anything: it orphans every registered device.
    """
    row = (
        await session.execute(select(PushVapidKey).where(PushVapidKey.org_id == org_id))
    ).scalar_one_or_none()
    if row is None:
        keys = webpush.generate_keys()
        row = PushVapidKey(
            org_id=org_id,
            public_key=keys.public_key,
            private_key_enc=encrypt(keys.private_key),
        )
        session.add(row)
        await session.flush()
        return keys
    return webpush.VapidKeys(
        public_key=row.public_key, private_key=decrypt(row.private_key_enc)
    )


class PushSubscriptionService:
    """The caller's own devices. There is no ``user_id`` parameter anywhere: an inbox is personal.

    Registering a browser is the same act as reading your own inbox, so it rides
    ``notifications.notification.write`` rather than earning a key of its own. Deliberately not
    ``channels.manage_own``: that key exists to gate a URL somebody *types*, with an SSRF surface
    behind it, and it is not held by the ``client`` role. A subscription is minted by the person's
    own browser and points at Google, Mozilla or Apple — so a client-portal login registers a
    device for their own notifications like anyone else.
    """

    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx

    async def register(
        self, *, endpoint: str, p256dh: str, auth: str, user_agent: str | None
    ) -> PushSubscription:
        """Store (or refresh) one browser's subscription.

        An endpoint the caller already registered is **refreshed, not duplicated** — the client
        re-presents it on every session precisely because endpoints rotate silently, and a browser
        that reports the same one is the same device. Re-registering somebody *else's* endpoint
        takes it over rather than failing: an endpoint identifies a browser, and a browser that is
        now signed in as this user is now this user's. The alternative — a 409 — would strand the
        device permanently on a shared laptop.
        """
        from app.errors import AppError

        try:
            webpush.assert_endpoint_safe(endpoint)
        except SsrfBlocked as exc:
            # Refused before it is stored, and refused again at send time: this row is the one
            # attacker-supplied URL in the module, and DNS can rebind between the two moments.
            raise AppError(
                "invalid_endpoint",
                "errors.push_endpoint_blocked",
                status_code=422,
            ) from exc

        session = self.ctx.session
        existing = (
            await session.execute(
                select(PushSubscription).where(
                    PushSubscription.org_id == self.ctx.org.id,
                    PushSubscription.endpoint == endpoint,
                )
            )
        ).scalar_one_or_none()
        now = datetime.now(UTC)
        if existing is not None:
            existing.user_id = self.ctx.user.id
            existing.p256dh = p256dh
            existing.auth = auth
            existing.user_agent = user_agent
            existing.last_seen_at = now
            await session.flush()
            return existing

        row = PushSubscription(
            org_id=self.ctx.org.id,
            user_id=self.ctx.user.id,
            endpoint=endpoint,
            p256dh=p256dh,
            auth=auth,
            user_agent=user_agent,
            last_seen_at=now,
        )
        session.add(row)
        await session.flush()
        return row

    async def list(self) -> list[PushSubscription]:
        """This person's devices, newest first."""
        return list(
            (
                await self.ctx.session.execute(
                    select(PushSubscription)
                    .where(
                        PushSubscription.org_id == self.ctx.org.id,
                        PushSubscription.user_id == self.ctx.user.id,
                    )
                    .order_by(PushSubscription.created_at.desc())
                )
            )
            .scalars()
            .all()
        )

    async def revoke(self, subscription_id: uuid.UUID) -> None:
        """Drop one device. Somebody else's id is a **404**, never a 403 (CLAUDE.md §15)."""
        from app.errors import AppError

        row = (
            await self.ctx.session.execute(
                select(PushSubscription).where(
                    PushSubscription.org_id == self.ctx.org.id,
                    PushSubscription.user_id == self.ctx.user.id,
                    PushSubscription.id == subscription_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise AppError("not_found", "errors.not_found", status_code=404)
        await self.ctx.session.delete(row)
        await self.ctx.session.flush()

    async def revoke_endpoint(self, endpoint: str) -> None:
        """Drop the device identified by its own endpoint — what a browser unsubscribing knows.

        Scoped to the caller, so this is not a way to silence a colleague by guessing an endpoint.
        Silent when it matches nothing: unsubscribing twice is not an error, and the browser has
        already thrown the subscription away either way.
        """
        await self.ctx.session.execute(
            delete(PushSubscription).where(
                PushSubscription.org_id == self.ctx.org.id,
                PushSubscription.user_id == self.ctx.user.id,
                PushSubscription.endpoint == endpoint,
            )
        )

    async def test(self, *, title: str, body: str) -> tuple[int, str | None]:
        """Send one push to every device this person has. Returns ``(delivered, last error)``.

        The one place a push leaves the API process rather than the worker, and it is worth the
        exception: "did connecting this browser actually work?" is unanswerable by any amount of
        staring at settings, and the mirror of the test-send channels have had since #17. Retired
        devices are pruned here too — a test is as good a moment to learn that as a real send.
        """
        session = self.ctx.session
        keys = await vapid_keys(session, self.ctx.org.id)
        subscriptions = await self.list()
        delivered = 0
        error: str | None = None
        gone: list[uuid.UUID] = []
        for subscription in subscriptions:
            try:
                await webpush.send(
                    subscription.endpoint,
                    p256dh=subscription.p256dh,
                    auth=subscription.auth,
                    payload={"title": title, "body": body, "tag": "schakl-test", "count": 1},
                    keys=keys,
                    subject=VAPID_SUBJECT,
                )
            except webpush.WebPushError as exc:
                if exc.gone:
                    gone.append(subscription.id)
                else:
                    error = str(exc)
            except Exception as exc:  # noqa: BLE001 - the result is the error, not a 500
                error = str(exc)
            else:
                delivered += 1
        if gone:
            await session.execute(delete(PushSubscription).where(PushSubscription.id.in_(gone)))
        return delivered, error


class WebPushChannel:
    """Push channel: one delivery row per *recipient* who actually has a device.

    **Per recipient, not per device**, and that is the load-bearing choice. The cadence belongs to
    the person; the devices are an implementation detail of reaching them. A row per device would
    turn one daily digest of ten events into ten × three messages, and would make "was this
    delivered?" a question with three answers and no way to settle it. The fan-out to devices
    happens in the sweep instead, against whatever devices exist *then* — which is also what makes
    registering a new browser retroactively work for anything still pending.

    Recipients with **no** subscription are skipped, in one batched query for the whole batch (the
    e-mail channel already spends one; the fan-out's query budget covers it). Without that, every
    event would leave a delivery row for every colleague who never granted permission, and the
    sweep would spend its 200-row window discovering that.

    Same rule as every channel: DB writes only, never a provider call. A push service being slow
    must not slow down saving a task.
    """

    key = CHANNEL_WEB_PUSH

    async def deliver(
        self,
        ctx: EmitContext,
        *,
        event: NotificationEvent,
        notifications: Sequence[Notification],
    ) -> None:
        from app.modules.notifications.prefs import (
            compute_visible_at,
            resolve_web_push_for_recipients,
        )

        if not notifications:
            return
        session = ctx.session
        org_id = ctx.org.id
        user_ids = [row.user_id for row in notifications]

        prefs = await resolve_web_push_for_recipients(session, org_id, event.event_type, user_ids)
        if not any(pref.enabled for pref in prefs.values()):
            # Nobody routes this event to a browser: skip the device lookup entirely.
            return

        subscribed = set(
            (
                await session.execute(
                    select(PushSubscription.user_id)
                    .where(
                        PushSubscription.org_id == org_id,
                        PushSubscription.user_id.in_(user_ids),
                    )
                    .distinct()
                )
            )
            .scalars()
            .all()
        )
        if not subscribed:
            return

        now = datetime.now(UTC)
        tz = await org_zoneinfo(session, org_id)
        for row in notifications:
            pref = prefs.get(row.user_id)
            if pref is None or not pref.enabled or row.user_id not in subscribed:
                continue
            session.add(
                NotificationDelivery(
                    org_id=org_id,
                    notification_id=row.id,
                    channel=CHANNEL_WEB_PUSH,
                    status="pending",
                    # Quiet hours are honoured here and not on the bell: this is the channel
                    # that can wake someone at 03:00 (#309).
                    deliver_after=compute_visible_at(pref, now, tz=tz, quiet_hours=True),
                )
            )


# --------------------------------------------------------------------------- #
# Worker-side dispatch
# --------------------------------------------------------------------------- #
async def _icon_url(session: AsyncSession, org_id: uuid.UUID, brand) -> str | None:  # noqa: ANN001
    """The square icon the notification wears, resolved from the tenant's own branding.

    Never baked into the service worker: branding is runtime and per tenant (Golden Rule 4), and
    a self-hosted agency's staff should see *their* mark on the lock screen. Same source and same
    size variant the PWA manifest serves (#198). The logo is a deliberate second choice — it is
    usually wide, and a wide image in a round slot is worse than the bundled default the service
    worker falls back to.
    """
    from app.core.models import OrgSettings

    row = await session.scalar(select(OrgSettings).where(OrgSettings.org_id == org_id))
    icon = getattr(row, "app_icon_url", None) if row is not None else None
    if icon:
        return f"{icon}{'&' if '?' in icon else '?'}size=192"
    return brand.logo_url


def _payload(message, brand, count: int, icon: str | None) -> dict:  # noqa: ANN001
    """What the service worker receives, after decryption.

    The push service forwards a blob it cannot read (RFC 8291 encrypts to the browser's own
    keys), which is what makes it acceptable to put the real sentence in here rather than a
    "you have a message" stub that would make the notification useless.
    """
    body = message.body
    if len(body) > MAX_BODY_CHARS:
        body = body[: MAX_BODY_CHARS - 1] + "…"
    inbox = f"{brand.base_url}/notifications"
    return {
        "title": message.title,
        "body": body,
        # A single notification opens the thing it is about; a digest opens the inbox, because
        # there is no one record it is about and picking the first would be a lie.
        "url": (_first_link(message.body) if count == 1 else None) or inbox,
        # One tag per recipient, so a later push *replaces* the earlier one instead of stacking
        # three lock-screen entries that each say a version of the same thing.
        "tag": "schakl-notifications",
        "count": count,
        "icon": icon,
    }


def _first_link(body: str) -> str | None:
    """The deep link ``build_digest_message`` put under the sentence, if it left one."""
    for line in body.splitlines():
        if line.startswith("https://") or line.startswith("http://"):
            return line.strip()
    return None


async def dispatch_webpush_deliveries(session, org) -> None:  # noqa: ANN001
    """Send every due web-push delivery, one message per recipient, fanned out to their devices.

    Grouped by recipient like the e-mail sweep, not by channel like the external one: a person
    has an inbox, and here that inbox happens to be spread over their phone and their laptop.

    Settling differs from the other two channels in one way that matters. **The bundle is sent if
    any one device accepted it** — a user with a dead phone and a live laptop was reached, and
    failing the bundle would re-send to the laptop every minute until it exhausted its attempts.
    A ``404``/``410`` deletes the subscription and does **not** count as a failure: a device
    someone threw away is not an error, and burning attempts on it would eventually fail the
    bundle for the devices that are alive.
    """
    from app.modules.notifications.external import (
        MAX_ATTEMPTS,
        _backoff_ready,
        _due,
        _settle,
        _still_active,
        build_digest_message,
    )

    now = datetime.now(UTC)
    rows = (await session.execute(_due(CHANNEL_WEB_PUSH, org.id, now))).all()
    if not rows:
        return

    from app.core.auth.models import User
    from app.core.email.branding import load_brand

    brand = await load_brand(session, org)
    keys = await vapid_keys(session, org.id)
    icon = await _icon_url(session, org.id, brand)

    groups: dict[uuid.UUID, list[tuple[NotificationDelivery, Notification]]] = {}
    for delivery, notification in rows:
        groups.setdefault(notification.user_id, []).append((delivery, notification))

    for user_id, items in groups.items():
        ready = [pair for pair in items if _backoff_ready(pair[0], now)]
        if not ready:
            continue
        # A phone is an inbox too: a notification read/resolved in-app before its push slot is no
        # longer news, so drop it and, if the bundle is emptied, buzz nobody (#170).
        ready = _still_active(ready)
        if not ready:
            continue

        subscriptions = list(
            (
                await session.execute(
                    select(PushSubscription).where(
                        PushSubscription.org_id == org.id,
                        PushSubscription.user_id == user_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        if not subscriptions:
            # Every device was revoked between the emit and now. Nothing to retry against, and
            # the bell already has the notification — settle rather than pile up pending rows.
            for delivery, _ in ready:
                delivery.status = "failed"
                delivery.last_error = "no registered devices"
            continue

        user = await session.get(User, user_id)
        locale = getattr(user, "locale", None) or settings.default_locale
        message = await build_digest_message(
            session, brand, [notification for _, notification in ready], locale
        )
        if message is None:
            for delivery, _ in ready:
                delivery.status = "failed"
                delivery.last_error = "notification no longer exists"
            continue

        payload = _payload(message, brand, len(ready), icon)
        for delivery, _ in ready:
            delivery.attempts += 1

        delivered = False
        error: str | None = None
        gone: list[uuid.UUID] = []
        for subscription in subscriptions:
            try:
                await webpush.send(
                    subscription.endpoint,
                    p256dh=subscription.p256dh,
                    auth=subscription.auth,
                    payload=payload,
                    keys=keys,
                    subject=VAPID_SUBJECT,
                )
            except webpush.WebPushError as exc:
                if exc.gone:
                    gone.append(subscription.id)
                    continue
                error = str(exc)
            except SsrfBlocked as exc:
                # The endpoint resolved publicly when it was registered and does not now.
                error = f"blocked target: {exc}"
            except Exception as exc:  # noqa: BLE001 - one device must not kill the sweep
                error = str(exc)
            else:
                delivered = True
                subscription.last_success_at = now

        if gone:
            await session.execute(
                delete(PushSubscription).where(PushSubscription.id.in_(gone))
            )
            logger.info("pruned %d retired push subscription(s)", len(gone))

        if not delivered and error is None and gone:
            # Every device this bundle had was retired. There is nothing left to reach and
            # nothing to blame — settle it rather than retry against an empty set.
            for delivery, _ in ready:
                delivery.status = "failed"
                delivery.last_error = "every device was retired"
            continue

        _settle(ready, ok=delivered, error=error, now=now)
        if not delivered and any(d.attempts >= MAX_ATTEMPTS for d, _ in ready):
            logger.warning("web push gave up for user %s: %s", user_id, error)
