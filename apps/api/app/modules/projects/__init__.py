"""projects module (CLAUDE.md §6, §10, P2) — client engagements that own to-dos and budgets.

Importing this package self-registers the module (router, company panel, mcp seam, i18n
namespace, cron jobs) into the shared registry.
"""

from __future__ import annotations

from arq import cron

from app.core.events import subscribe
from app.modules.projects.attachments import on_file_event
from app.modules.projects.budget_watch import watch_project_budgets
from app.modules.projects.impex import PROJECT_IMPEX
from app.modules.projects.mcp import PROJECT_MCP_TOOLS
from app.modules.projects.panels import projects_company_panel
from app.modules.projects.permissions import PROJECT_PERMISSIONS
from app.modules.projects.router import router
from app.registry import ModuleDescriptor, registry

module = ModuleDescriptor(
    name="projects",
    router=router,
    i18n_namespace="projects",
    panels=[projects_company_panel],
    permissions=PROJECT_PERMISSIONS,
    impex=[PROJECT_IMPEX],
    mcp_tools=PROJECT_MCP_TOOLS,
    # Offset from the tasks crons (04:00 recurrence, 05:30 reminders) and the 05:00 update check.
    cron_jobs=[cron(watch_project_budgets, hour=5, minute=45)],
)

registry.register(module)

# Document attachments (#123 follow-up): validate the target project, record on its trail.
subscribe("file.attached", on_file_event)
subscribe("file.removed", on_file_event)
