"""notifications module (CLAUDE.md §6, issue #16) — in-app inbox, activity feed, delivery prefs.

Importing this package self-registers the module (router + i18n namespace) into the shared
registry and subscribes the fan-out to **every** event the platform emits. It owns no domain
of its own: other modules emit what happened, this one decides who hears about it, when.

Because the fan-out is one generic handler per event, adding an event is a two-line change —
name it in ``events.py``, emit it from the module that owns the data. No cross-module imports
(Golden Rule 3): the recipients ride in the event payload.
"""

from __future__ import annotations

from arq import cron

from app.core.events import subscribe
from app.modules.notifications.channels import register_channel
from app.modules.notifications.events import EVENT_TYPES
from app.modules.notifications.external import EmailChannel, ExternalChannel
from app.modules.notifications.jobs import dispatch_notification_deliveries
from app.modules.notifications.permissions import NOTIFICATION_PERMISSIONS
from app.modules.notifications.router import router
from app.modules.notifications.service import make_handler
from app.registry import ModuleDescriptor, registry

# External transports (SMTP, Slack, Teams, Google Chat via Apprise — #17) register here, behind
# the same channel seam as the in-app bell. The push happens in a worker cron, not the request.
register_channel(ExternalChannel())
register_channel(EmailChannel())

# The record's activity panel is core now (issue #67) — a real audit trail, not the notifiable
# subset this module logs. Notifications keeps the inbox, the bell and the event log; it no
# longer contributes the "what happened to this record" panel.
module = ModuleDescriptor(
    name="notifications",
    router=router,
    i18n_namespace="notifications",
    permissions=NOTIFICATION_PERMISSIONS,
    # Drain pending external deliveries every minute, per org, with backoff (#17).
    cron_jobs=[cron(dispatch_notification_deliveries, second=30)],
)

registry.register(module)

for _event_type in EVENT_TYPES:
    subscribe(_event_type, make_handler(_event_type))
