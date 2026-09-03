"""tasks module (CLAUDE.md §6, §10) — to-dos, attachable to companies, assignable to employees.

Importing this package self-registers the module (router, company panel, mcp seam, i18n
namespace, cron jobs) into the shared registry, and subscribes the template automation to
the company lifecycle events.
"""

from __future__ import annotations

from arq import cron

from app.core.activity import register_auditable
from app.core.busy import register_busy_provider
from app.core.events import subscribe
from app.modules.tasks.attachments import on_file_event
from app.modules.tasks.bulk import TASK_BULK
from app.modules.tasks.emails import TASK_EMAIL_KINDS, tasks_send_contact_assigned
from app.modules.tasks.impex import TASK_IMPEX
from app.modules.tasks.mcp import TASK_MCP_TOOLS
from app.modules.tasks.panels import tasks_company_panel
from app.modules.tasks.permissions import TASK_PERMISSIONS
from app.modules.tasks.recurrence import spawn_scheduled_recurrences
from app.modules.tasks.reminders import send_task_reminders
from app.modules.tasks.router import router
from app.modules.tasks.scheduling import task_blocks_busy
from app.modules.tasks.summary import tasks_company_summary
from app.modules.tasks.templates import on_company_status, on_subscription_activated
from app.registry import ModuleDescriptor, registry

module = ModuleDescriptor(
    name="tasks",
    router=router,
    i18n_namespace="tasks",
    panels=[tasks_company_panel],
    # The client's vital-signs strip (#364) — the panels seam one level up.
    summaries=[tasks_company_summary],
    permissions=TASK_PERMISSIONS,
    impex=[TASK_IMPEX],
    bulk=[TASK_BULK],
    mcp_tools=TASK_MCP_TOOLS,
    # The mail a client's contact gets when a task is assigned to them (#454): a mail the
    # agency's client reads is theirs to reword (docs/EMAIL.md), so it is a kind here rather
    # than a string in the service.
    email_templates=TASK_EMAIL_KINDS,
    # Enqueued by name from the assignment, sent by the worker with its own session — never
    # inside the transaction that made the assignment.
    worker_functions=[tasks_send_contact_assigned],
    # 04:00 UTC is early morning across European zones; the job resolves each org's own local
    # date itself (CLAUDE.md §8), so the cron hour only has to be early enough for all of them.
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

# The scheduling dialog's conflict check (app/core/busy.py): this module's third of "when is
# this person already taken" is their planned blocks, titled under its own read rule.
register_busy_provider("tasks.schedule", task_blocks_busy)

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
subscribe("file.visibility_changed", on_file_event)
