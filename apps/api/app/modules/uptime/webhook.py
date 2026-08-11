"""Uptime Kuma calling *us* (docs/UPTIME.md §11).

This is the direction that needs no tunnel, no credential and no outbound reach — which is why
a ``linked`` instance is a first-class mode rather than a degraded one. A client who will not
hand over the only administrator account of their own monitoring still gets a status timeline,
an alert, and an automation trigger.

The URL is the addressing: ``{org_id}.{instance_id}.{secret}``, the Google Calendar channel
token reused rather than reinvented (``app/core/payments/tokens.py``), because the problem is
identical and getting it wrong is a cross-tenant write.

**Five gates, in this order**, and the order is the point:

1. the token names the tenant, so nothing is ever read unscoped;
2. RLS is bound before anything is read;
3. the secret is compared in **constant time**, and a mismatch is a bare ``404`` — never a
   ``401``, which would confirm the instance exists;
4. only then is the body read, **for the monitor id and the claimed state and nothing else**;
5. on a ``managed`` instance the claim is confirmed by an authenticated re-fetch.

**A webhook body is a hint, never a fact.** Kuma's webhook is unsigned JSON that anyone who
learns the URL can post. On a ``linked`` instance there is no re-fetch available, which is the
honest limit of that mode: the row is stored with ``reported=True`` and the screen says
*reported by the instance* rather than presenting a claim as a measurement.

Three things this route may not do, each of which a "helpful" version would:

* **It never creates.** A monitor id we do not already hold is a 404, not an insert. A route
  that auto-registers what it is told about is an unauthenticated writer of tenant rows.
* **It never writes configuration.** Its entire write surface is one heartbeat row and one
  notification event, so the worst a leaked URL buys is a false status on a known monitor —
  recoverable, and contradicted by the next sync on a managed instance.
* **It is bounded before it is parsed.** The body cap is checked before the JSON is decoded
  (§17's rule that every cap is checked before the work it bounds); an ingest route that is
  cheap to call and expensive to serve is the one shape a public URL must never have.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.payments.tokens import matches, parse
from app.modules.uptime.models import (
    InstanceMode,
    UptimeHeartbeat,
    UptimeInstance,
    UptimeMonitor,
)

logger = logging.getLogger("schakl.uptime")

#: Uptime Kuma's webhook body is small — a monitor, a heartbeat and the message. Anything past
#: this is not a Kuma notification, and refusing it before decoding is what keeps the route
#: cheap to serve as well as cheap to call.
MAX_BODY_BYTES = 64 * 1024

#: The states Kuma reports. Anything else is stored as ``pending`` rather than rejected: a new
#: state in a future version should not make a client's outage invisible.
KNOWN_STATES = {"up", "down", "pending", "maintenance"}


def _status_of(payload: dict[str, Any]) -> str:
    """Kuma's heartbeat status, from either the numeric or the textual shape.

    ``heartbeat.status`` is ``0`` (down) / ``1`` (up) / ``2`` (pending) / ``3`` (maintenance) in
    the socket payload, while the webhook template can send a word. Both are read because a
    tenant may template their own body, and guessing wrong here means recording an outage as an
    all-clear.
    """
    heartbeat = payload.get("heartbeat")
    raw: Any = None
    if isinstance(heartbeat, dict):
        raw = heartbeat.get("status")
    if raw is None:
        raw = payload.get("status")
    if isinstance(raw, bool):
        return "up" if raw else "down"
    if isinstance(raw, int):
        return {0: "down", 1: "up", 2: "pending", 3: "maintenance"}.get(raw, "pending")
    text = str(raw or "").strip().lower()
    return text if text in KNOWN_STATES else "pending"


def _monitor_id_of(payload: dict[str, Any]) -> int | None:
    monitor = payload.get("monitor")
    raw = monitor.get("id") if isinstance(monitor, dict) else payload.get("monitorID")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _observed_at(payload: dict[str, Any]) -> datetime:
    """When Kuma says it happened, or now.

    Kuma's own timestamp is preferred so a delayed delivery does not land in the wrong minute,
    but it is never *trusted* into the future: a body-supplied clock that could claim tomorrow
    would let one leaked URL pin a monitor's latest state permanently.
    """
    heartbeat = payload.get("heartbeat")
    raw = heartbeat.get("time") if isinstance(heartbeat, dict) else None
    now = datetime.now(UTC)
    if not raw:
        return now
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return now
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return min(parsed, now)


async def ingest(token: str, body: bytes) -> int:
    """Record one reported heartbeat. Returns the HTTP status to answer with.

    Answers a status and never raises, for the reason the payment callback does: this runs
    outside a request context that could roll anything back, and a stack trace on a public
    endpoint is a gift.
    """
    from app.core.models import Org, OrgStatus
    from app.db import async_session_maker, set_current_org

    if len(body) > MAX_BODY_BYTES:
        return 413
    parsed = parse(token)
    if parsed is None:
        return 404

    async with async_session_maker() as session:
        org = await session.get(Org, parsed.org_id)
        if org is None or org.status != OrgStatus.ACTIVE.value:
            return 404
        await set_current_org(session, org.id)

        instance = await session.scalar(
            select(UptimeInstance).where(
                UptimeInstance.id == parsed.account_id, UptimeInstance.org_id == org.id
            )
        )
        # Constant-time, and a bare 404 either way: a wrong secret and an unknown instance must
        # be indistinguishable, or the route becomes an oracle for which instances exist.
        if instance is None or not matches(instance.webhook_secret, parsed.secret):
            return 404
        if not instance.active:
            # An off switch has to be retroactive to be worth having (#304's rule): unticking
            # `active` withdraws a URL somebody already configured at the far end.
            return 404

        try:
            payload = json.loads(body or b"{}")
        except ValueError:
            return 400
        if not isinstance(payload, dict):
            return 400

        kuma_id = _monitor_id_of(payload)
        if kuma_id is None:
            return 400

        monitor = await session.scalar(
            select(UptimeMonitor).where(
                UptimeMonitor.org_id == org.id,
                UptimeMonitor.instance_id == instance.id,
                UptimeMonitor.kuma_monitor_id == kuma_id,
            )
        )
        # Never creates. A monitor we do not hold is a 404 — the token proves the *instance* is
        # known, not that this monitor is, and auto-registering would make this an
        # unauthenticated writer of tenant rows.
        if monitor is None:
            return 404

        status = _status_of(payload)
        message = payload.get("msg") or payload.get("message")
        heartbeat = payload.get("heartbeat") if isinstance(payload.get("heartbeat"), dict) else {}
        ping = heartbeat.get("ping")

        stmt = (
            pg_insert(UptimeHeartbeat.__table__)
            .values(
                org_id=org.id,
                monitor_id=monitor.id,
                status=status,
                observed_at=_observed_at(payload),
                message=str(message)[:500] if message else None,
                ping_ms=int(ping) if isinstance(ping, (int, float)) else None,
                reported=True,
            )
            # The idempotency guarantee is the index, not a check-then-insert: a provider
            # retrying and an hourly reconcile are in flight against each other, and "have we
            # recorded this?" followed by an insert leaves a window every retry enters.
            .on_conflict_do_nothing(constraint="uq_uptime_heartbeat_event")
        )
        # Read everything the announcement needs **before** the commit. A commit expires every
        # loaded ORM object, and the next attribute read would lazy-load synchronously with no
        # greenlet — `MissingGreenlet`. Capturing plain values also keeps `_announce` from
        # depending on live rows, which is the right shape for it regardless.
        announcement = {
            "monitor_id": monitor.id,
            "monitor_name": monitor.name,
            "instance_name": instance.name,
            "reported": instance.mode == InstanceMode.LINKED.value,
        }
        try:
            result = await session.execute(stmt)
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("uptime webhook failed for org %s", org.slug)
            # 503 so Kuma's own retry schedule becomes the recovery mechanism.
            return 503

    # Outside the session above, and in one of its own: an announcement that fails must not be
    # able to touch the heartbeat's transaction, and the heartbeat is already committed.
    if result.rowcount and status == "down":
        await _announce(parsed.org_id, announcement, status)
    return 200


async def _announce(org_id: uuid.UUID, about: dict[str, Any], status: str) -> None:
    """Turn a fresh outage into a notification event — through the existing machinery.

    Uptime Kuma has ninety-odd notification providers and is better at delivery than we would
    be, so this is not a second delivery channel. It is what puts the outage on the *client's
    record*, where an agency's own alerting already looks.

    Deliberately **not** an activity-trail entry: an unauthenticated caller must never be able
    to write lines into an audit trail, which is the shape that turns a leaked URL into a way
    to bury evidence under noise.
    """
    from app.core.models import Org
    from app.db import async_session_maker, set_current_org

    try:
        from app.core.jobs import system_context
        from app.modules.notifications.service import NotificationService

        async with async_session_maker() as session:
            org = await session.get(Org, org_id)
            if org is None:
                return
            await set_current_org(session, org.id)
            ctx = system_context(org, session)
            await NotificationService(ctx).ingest(
                "uptime.monitor.down",
                "uptime_monitor",
                about["monitor_id"],
                {
                    "monitor_name": about["monitor_name"],
                    "instance_name": about["instance_name"],
                    "status": status,
                    # Said out loud, because on a `linked` instance there is no re-fetch and
                    # this is a claim rather than a measurement.
                    "reported": about["reported"],
                },
            )
            await session.commit()
    except Exception:
        # The outage is already recorded. Failing to announce it must not lose the heartbeat,
        # must not hand the caller a 500, and must not leave a poisoned session behind.
        logger.exception("uptime webhook: notification failed for org %s", org_id)


def webhook_url(base: str, org_id: uuid.UUID, instance: UptimeInstance) -> str:
    """The URL an admin pastes into Uptime Kuma's notification settings."""
    from app.core.payments.tokens import mint

    token = mint(org_id, instance.id, instance.webhook_secret)
    return f"{base.rstrip('/')}/api/v1/uptime/hook/{token}"
