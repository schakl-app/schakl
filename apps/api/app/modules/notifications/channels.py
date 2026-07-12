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

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from app.core.events import EmitContext
from app.modules.notifications.events import CHANNEL_IN_APP
from app.modules.notifications.models import Notification, NotificationEvent


@runtime_checkable
class NotificationChannel(Protocol):
    """One way to reach a person. ``key`` matches ``notification_preferences.channel``."""

    key: str

    async def deliver(
        self,
        ctx: EmitContext,
        *,
        event: NotificationEvent,
        notifications: Sequence[Notification],
    ) -> None:
        """Hand this event's freshly written inbox rows to the transport, as one batch.

        Called once per event with every row the fan-out just wrote, so an implementation
        must issue a bounded number of queries however many recipients there are (the fan-out
        N+1 test counts them). Runs inside the emitter's transaction, so it must not do
        blocking or fallible network I/O here — it enqueues (delivery rows / an ARQ job) and
        returns.
        """
        ...


class InAppChannel:
    """The bell. A pull channel: the ``notifications`` row *is* the delivery."""

    key = CHANNEL_IN_APP

    async def deliver(
        self,
        ctx: EmitContext,
        *,
        event: NotificationEvent,
        notifications: Sequence[Notification],
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
