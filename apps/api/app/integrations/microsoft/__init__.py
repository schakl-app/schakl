"""microsoft module (CLAUDE.md §6a, docs/MICROSOFT.md) — the Microsoft 365 integration, one sku.

One registry integration holding the core (OAuth connect against the Microsoft identity
platform, the encrypted token vault, the "act as user X" Graph client factory) plus the
``calendar/`` (Outlook calendar), ``onedrive/`` and ``outlook/`` (mail) surface subpackages —
the same three data problems the Google integration answers, on the same seams: the Agenda's
calendar sources and the busy provider, the company hub's panels, and the contactmomenten
timeline's published ``system`` surface. What is a rule about the agency rather than about a
vendor (``app/core/mailbox``, ``app/core/calendarmirror``) is shared with ``google``; what is
Graph's — ids, delta links, subscriptions, categories, drive items — lives here.

``sku="microsoft"`` is the whole commercial boundary (issue #137): an expired license turns every
mutation 402 at the mount-time gate, and the module's own crons additionally stand down.

Importing this package self-registers the module.
"""

from __future__ import annotations

from arq import cron

import app.integrations.microsoft.calendar  # noqa: F401 — wires the mirror events onto the bus
import app.integrations.microsoft.onedrive  # noqa: F401 — wires the folder-provisioning events
import app.integrations.microsoft.outlook  # noqa: F401 — wires the review-flow events
from app.integrations.microsoft.calendar.jobs import (
    microsoft_calendar_poll_fallback,
    microsoft_calendar_push_link,
    microsoft_calendar_renew_subscriptions,
    microsoft_calendar_sweep_outbox,
    microsoft_calendar_sync_connection,
)
from app.integrations.microsoft.calendar.router import router as calendar_router
from app.integrations.microsoft.onedrive.jobs import (
    onedrive_provision_folder,
    onedrive_sweep_folder_jobs,
)
from app.integrations.microsoft.onedrive.panels import onedrive_company_panel
from app.integrations.microsoft.onedrive.router import router as onedrive_router
from app.integrations.microsoft.outlook.jobs import (
    outlook_fetch_body,
    outlook_poll,
    outlook_poll_connection,
    outlook_reap_skips,
    outlook_sweep_bodies,
)
from app.integrations.microsoft.outlook.router import router as outlook_router
from app.integrations.microsoft.permissions import MICROSOFT_PERMISSIONS
from app.integrations.microsoft.router import router
from app.registry import KIND_INTEGRATION, ModuleDescriptor, registry

router.include_router(calendar_router)
router.include_router(onedrive_router)
router.include_router(outlook_router)

module = ModuleDescriptor(
    name="microsoft",
    # A conversation with somebody else's service, not a capability of our own.
    kind=KIND_INTEGRATION,
    # Requires nothing (CLAUDE.md §6a), for the reason ``google`` requires nothing: it enriches
    # ``interactions``, ``tasks`` and ``leave`` and needs none of them — with all three off it
    # is still a OneDrive browser.
    requires=(),
    router=router,
    i18n_namespace="microsoft",
    sku="microsoft",
    panels=[onedrive_company_panel],
    permissions=MICROSOFT_PERMISSIONS,
    cron_jobs=[
        # Minute offsets keep clear of the platform's 04:00/05:00/05:30 jobs, of each other,
        # and of the Google integration's own ticks (:20/:40/:50/:10/:30 seconds).
        cron(microsoft_calendar_renew_subscriptions, minute=25),
        cron(microsoft_calendar_poll_fallback, minute={5, 20, 35, 50}, second=40),
        cron(microsoft_calendar_sweep_outbox, minute=set(range(0, 60, 5)), second=15),
        cron(onedrive_sweep_folder_jobs, minute=set(range(0, 60, 5)), second=25),
        cron(outlook_poll, minute=set(range(0, 60, 5)), second=55),
        cron(outlook_sweep_bodies, minute=set(range(0, 60, 5)), second=35),
        cron(outlook_reap_skips, hour=3, minute=45),
    ],
    worker_functions=[
        microsoft_calendar_sync_connection,
        microsoft_calendar_push_link,
        onedrive_provision_folder,
        outlook_poll_connection,
        outlook_fetch_body,
    ],
)

registry.register(module)
