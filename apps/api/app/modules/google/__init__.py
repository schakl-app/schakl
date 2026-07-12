"""google module (CLAUDE.md §6, issue #22) — the Workspace integration, licensed as one sku.

One registry module holding the core (OAuth connect, encrypted token vault, "act as user X"
client factory) plus the ``calendar/``, ``drive/`` and ``gmail/`` surface subpackages of
docs/GOOGLE.md §3. They stay separate packages internally — each owns its models, sync logic
and routes — but license, enablement and i18n are one unit: ``sku="google"`` is the whole
commercial boundary (issue #137), so an expired license turns every Google mutation 402 at the
mount-time gate, and the module's own crons additionally stand down (they write on a schedule,
not on a request, so the route gate alone would not stop them).

Importing this package self-registers the module.
"""

from __future__ import annotations

from arq import cron

import app.modules.google.calendar  # noqa: F401 — wires the leave events onto the bus
import app.modules.google.drive  # noqa: F401 — wires the folder-provisioning events
from app.modules.google.calendar.jobs import (
    google_calendar_poll_fallback,
    google_calendar_push_link,
    google_calendar_renew_channels,
    google_calendar_sweep_outbox,
    google_calendar_sync_connection,
)
from app.modules.google.calendar.router import router as calendar_router
from app.modules.google.drive.jobs import (
    google_drive_provision_folder,
    google_drive_sweep_folder_jobs,
)
from app.modules.google.drive.panels import drive_company_panel
from app.modules.google.drive.router import router as drive_router
from app.modules.google.permissions import GOOGLE_PERMISSIONS
from app.modules.google.router import router
from app.registry import ModuleDescriptor, registry

router.include_router(calendar_router)
router.include_router(drive_router)

module = ModuleDescriptor(
    name="google",
    router=router,
    i18n_namespace="google",
    # Licensed module (issue #137): enabling the Google integration requires a license
    # covering this sku; past expiry+grace it goes read-only (mutations 402, crons stand down).
    sku="google",
    panels=[drive_company_panel],
    permissions=GOOGLE_PERMISSIONS,
    cron_jobs=[
        # Minute offsets keep clear of the platform's 04:00/05:00/05:30 jobs and each other.
        cron(google_calendar_renew_channels, minute=20),
        cron(google_calendar_poll_fallback, minute={0, 15, 30, 45}, second=40),
        cron(google_calendar_sweep_outbox, minute=set(range(0, 60, 5)), second=10),
        cron(google_drive_sweep_folder_jobs, minute=set(range(0, 60, 5)), second=20),
    ],
    worker_functions=[
        google_calendar_sync_connection,
        google_calendar_push_link,
        google_drive_provision_folder,
    ],
)

registry.register(module)
