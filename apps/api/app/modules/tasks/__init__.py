"""tasks module (CLAUDE.md §6, §10) — to-dos, attachable to companies, assignable to employees.

Importing this package self-registers the module (router, company panel, mcp seam, i18n
namespace, cron jobs) into the shared registry, and subscribes the template automation to
the company lifecycle events.
"""

from __future__ import annotations

from arq import cron

from app.core.events import subscribe
from app.modules.tasks.mcp import TASK_MCP_TOOLS
from app.modules.tasks.panels import tasks_company_panel
from app.modules.tasks.permissions import TASK_PERMISSIONS
from app.modules.tasks.recurrence import spawn_scheduled_recurrences
from app.modules.tasks.reminders import send_task_reminders
from app.modules.tasks.router import router
from app.modules.tasks.templates import on_company_status
from app.registry import ModuleDescriptor, registry

module = ModuleDescriptor(
    name="tasks",
    router=router,
    i18n_namespace="tasks",
    panels=[tasks_company_panel],
    permissions=TASK_PERMISSIONS,
    mcp_tools=TASK_MCP_TOOLS,
    # 04:00 UTC ≈ early morning in Europe/Amsterdam; the job reasons in local dates itself.
    cron_jobs=[
        cron(spawn_scheduled_recurrences, hour=4, minute=0),
        # After the recurrences exist, so a task spawned this morning can already be overdue.
        cron(send_task_reminders, hour=5, minute=30),
    ],
)

registry.register(module)

# Client onboarding automation: instantiate matching templates when a company is created
# with — or transitions into — a template's trigger status.
subscribe("company.created", on_company_status)
subscribe("company.status_changed", on_company_status)
