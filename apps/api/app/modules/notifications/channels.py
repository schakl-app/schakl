"""Delivery channels (issue #16 defines the seam; issue #17 fills it).

A channel turns "user U should learn about event E" into an actual delivery. The **in-app**
channel is a *pull* channel: the ``notifications`` row the fan-out already wrote is the
delivery, so it has nothing to push and writes no ``notification_deliveries`` row.

External transports (SMTP, Slack, Teams via Apprise — issue #17) are *push*: they register
here, the fan-out asks each enabled channel to `deliver`, and a push channel enqueues a
``notification_deliveries`` row for the worker to retry against. Keeping the interface here,
with the pull channel as its first implementation, means #17 adds transports without touching
the fan-out.

Channels are process-wide (registered at import), not per-tenant: *whether* a tenant uses one
is a preference row (``channel`` column), not a different object.
"""

from __future__ import annotations

import uuid
from typing import Protocol, runtime_checkable

from app.core.events import EmitContext
from app.modules.notifications.events import CHANNEL_IN_APP


@runtime_checkable
class NotificationChannel(Protocol):
    """One way to reach a person. ``key`` matches ``notification_preferences.channel``."""

    key: str

    async def deliver(
        self,
        ctx: EmitContext,
        *,
        notification_id: uuid.UUID,
        user_id: uuid.UUID,
        event_type: str,
    ) -> None:
        """Hand this notification to the transport.

        Runs inside the emitter's transaction, so an implementation must not do blocking or
        fallible network I/O here — it enqueues (a delivery row / an ARQ job) and returns.
        """
        ...


class InAppChannel:
    """The bell. A pull channel: the ``notifications`` row *is* the delivery."""

    key = CHANNEL_IN_APP

    async def deliver(
        self,
        ctx: EmitContext,
        *,
        notification_id: uuid.UUID,
        user_id: uuid.UUID,
        event_type: str,
    ) -> None:
        return None


_channels: dict[str, NotificationChannel] = {}


def register_channel(channel: NotificationChannel) -> None:
    _channels[channel.key] = channel


def get_channel(key: str) -> NotificationChannel | None:
    return _channels.get(key)


def channels() -> list[NotificationChannel]:
    return list(_channels.values())


register_channel(InAppChannel())
