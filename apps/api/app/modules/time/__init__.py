"""time module (CLAUDE.md §6, §10) — timer + manual entries + weekly timesheet.

Importing this package self-registers the module (router, company panel, mcp seam, i18n
namespace, cron jobs) into the shared registry.
"""

from __future__ import annotations

from arq import cron

from app.modules.time.impex import TIME_ENTRY_IMPEX
from app.modules.time.jobs import purge_stale_time_drafts
from app.modules.time.mcp import TIME_MCP_TOOLS
from app.modules.time.panels import time_company_panel
from app.modules.time.permissions import TIME_PERMISSIONS
from app.modules.time.reminders import send_timesheet_reminders
from app.modules.time.router import router
from app.registry import ModuleDescriptor, registry

module = ModuleDescriptor(
    name="time",
    router=router,
    i18n_namespace="time",
    panels=[time_company_panel],
    permissions=TIME_PERMISSIONS,
    impex=[TIME_ENTRY_IMPEX],
    mcp_tools=TIME_MCP_TOOLS,
    cron_jobs=[
        # Monday morning, once the week has actually started (weekday 0 = Monday).
        cron(send_timesheet_reminders, weekday=0, hour=7, minute=0),
        # Nightly draft retention (#44), off-peak alongside the other maintenance jobs.
        cron(purge_stale_time_drafts, hour=3, minute=45),
    ],
)

registry.register(module)
