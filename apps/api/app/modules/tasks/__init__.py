"""tasks module (CLAUDE.md §6, §10) — to-dos, attachable to companies, assignable to employees.

Importing this package self-registers the module (router, company panel, mcp seam, i18n
namespace, cron jobs) into the shared registry, and subscribes the template automation to
the company lifecycle events.
"""

from __future__ import annotations

from arq import cron

from app.core.activity import register_auditable
from app.core.events import subscribe
from app.modules.tasks.attachments import on_file_event
from app.modules.tasks.impex import TASK_IMPEX
from app.modules.tasks.mcp import TASK_MCP_TOOLS
from app.modules.tasks.panels import tasks_company_panel
from app.modules.tasks.permissions import TASK_PERMISSIONS
from app.modules.tasks.recurrence import spawn_scheduled_recurrences
from app.modules.tasks.reminders import send_task_reminders
from app.modules.tasks.router import router
from app.modules.tasks.templates import on_company_status, on_subscription_activated
from app.registry import ModuleDescriptor, registry

module = ModuleDescriptor(
    name="tasks",
    router=router,
    i18n_namespace="tasks",
    panels=[tasks_company_panel],
    permissions=TASK_PERMISSIONS,
    impex=[TASK_IMPEX],
    mcp_tools=TASK_MCP_TOOLS,
    # 04:00 UTC ≈ early morning in Europe/Amsterdam; the job reasons in local dates itself.
    cron_jobs=[
        cron(spawn_scheduled_recurrences, hour=4, minute=0),
        # After the recurrences exist, so a task spawned this morning can already be overdue.
        cron(send_task_reminders, hour=5, minute=30),
    ],
)

registry.register(module)

# A task keeps its own legacy TaskActivity trail (the #67 fold-in is still pending), so it does
# not use ``AuditableMixin``. But contact-moment milestones (#152) are mirrored onto the core
# activity log under entity_type=task, and the read endpoint refuses any entity_type that is not
# registered — so register it explicitly, purely to make those mirror entries readable. This does
# not add a second activity panel (the core panel is wired for project/contact only).
register_auditable("task", read_permission="tasks.task.read")  # trail read gate (audit F7)

# Client onboarding automation: instantiate matching templates when a company is created
# with — or transitions into — a template's trigger status.
subscribe("company.created", on_company_status)
subscribe("company.status_changed", on_company_status)

# Subscription onboarding (#142): the type's templates spawn on an agreement's first
# activation — the payload names them, so this module never reads the subscriptions tables.
subscribe("subscription.activated", on_subscription_activated)

# Document attachments (#123 follow-up): validate the target task, write its activity trail.
subscribe("file.attached", on_file_event)
subscribe("file.removed", on_file_event)
