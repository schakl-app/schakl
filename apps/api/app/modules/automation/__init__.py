"""automation module (issue #27, stage 1; #96 webhook recipes) — trigger → condition → action.

Per-tenant rules: a bus event triggers, a declarative JSONB condition tree filters, an ordered
action list executes — asynchronously, in the ARQ worker, with every firing recorded in
``automation_runs``. Importing this package self-registers the module (router, permissions,
the v1 action set, the worker job + requeue cron, i18n namespace) and subscribes the trigger
handler to every event in the trigger catalog.

Stages 2–4 of #27 (gated status transitions, AI actions, approval surfaces) are deliberately
not here.
"""

from __future__ import annotations

from arq import cron

from app.core.events import subscribe
from app.modules.automation.actions import BUILTIN_ACTIONS
from app.modules.automation.engine import make_trigger_handler
from app.modules.automation.jobs import automation_execute_run, requeue_stale_runs
from app.modules.automation.permissions import AUTOMATION_PERMISSIONS
from app.modules.automation.router import router
from app.modules.automation.triggers import TRIGGERS
from app.registry import ModuleDescriptor, registry

module = ModuleDescriptor(
    name="automation",
    router=router,
    i18n_namespace="automation",
    permissions=AUTOMATION_PERMISSIONS,
    # The v1 actions live on this module's own descriptor (issue #27); other modules
    # contribute theirs the same way, so core holds no action list.
    automation_actions=BUILTIN_ACTIONS,
    # Safety net for runs whose enqueue was lost (Redis blip / emitter race): every 5 minutes.
    cron_jobs=[cron(requeue_stale_runs, minute=set(range(0, 60, 5)), second=30)],
    # The on-demand execution job the trigger handler enqueues.
    worker_functions=[automation_execute_run],
)

registry.register(module)

for _event in TRIGGERS:
    subscribe(_event, make_trigger_handler(_event))
