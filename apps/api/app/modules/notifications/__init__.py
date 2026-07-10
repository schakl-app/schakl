"""notifications module (CLAUDE.md §6, issue #16) — in-app inbox, activity feed, delivery prefs.

Importing this package self-registers the module (router + i18n namespace) into the shared
registry and subscribes the fan-out to **every** event the platform emits. It owns no domain
of its own: other modules emit what happened, this one decides who hears about it, when.

Because the fan-out is one generic handler per event, adding an event is a two-line change —
name it in ``events.py``, emit it from the module that owns the data. No cross-module imports
(Golden Rule 3): the recipients ride in the event payload.
"""

from __future__ import annotations

from app.core.events import subscribe
from app.modules.notifications.events import EVENT_TYPES
from app.modules.notifications.panels import notifications_company_panel
from app.modules.notifications.permissions import NOTIFICATION_PERMISSIONS
from app.modules.notifications.router import router
from app.modules.notifications.service import make_handler
from app.registry import ModuleDescriptor, registry

module = ModuleDescriptor(
    name="notifications",
    router=router,
    i18n_namespace="notifications",
    panels=[notifications_company_panel],
    permissions=NOTIFICATION_PERMISSIONS,
)

registry.register(module)

for _event_type in EVENT_TYPES:
    subscribe(_event_type, make_handler(_event_type))
