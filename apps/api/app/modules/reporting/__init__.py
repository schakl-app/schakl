"""reporting module (CLAUDE.md §6, issue #300) — the periodic client report.

Turns what the platform already knows about a client into a document the client reads: a
frozen snapshot of the month's numbers, a narrative written in the agency's own voice, a
branded PDF, and a delivery — on a schedule, with a human in the loop by default.

**Why it is its own module and not part of ``marketing``.** Marketing is *the numbers, live*: a
data integration with a dashboard. Reporting is a *document* with its own lifecycle (drafted,
reviewed, published, sent), its own audience (a client, and separately the agency's own
marketer), and its own commercial boundary. A tenant can license marketing dashboards without
buying document generation, which is a real product ladder rather than a technical split.

**What it knows about other modules: nothing.** Sections arrive through
``ModuleDescriptor.report_sections`` — the panels pattern applied to documents — so the whole
traffic/rankings/audit half of a report is contributed by ``marketing`` and this module names
no module anywhere.

Licensed (``sku="reporting"``). Past expiry + grace the module goes read-only: generating,
editing and sending answer 402 at the mount-time gate, the schedule stands down (a cron writes
on its own and the route gate does not cover it), and **every report already produced still
opens, prints and downloads** — data is never hostage (epic #140).

Importing this package self-registers the module.
"""

from __future__ import annotations

from arq import cron

from app.modules.reporting.emails import REPORTING_EMAIL_KINDS
from app.modules.reporting.jobs import (
    reporting_run_report,
    reporting_schedule_report,
    reporting_tick,
)
from app.modules.reporting.panels import reporting_company_panel
from app.modules.reporting.permissions import REPORTING_PERMISSIONS
from app.modules.reporting.router import router
from app.registry import ModuleDescriptor, registry

module = ModuleDescriptor(
    name="reporting",
    router=router,
    i18n_namespace="reporting",
    sku="reporting",
    panels=[reporting_company_panel],
    permissions=REPORTING_PERMISSIONS,
    email_templates=REPORTING_EMAIL_KINDS,
    # Hourly, not daily: the hour a report is produced is a per-org setting and the worker's
    # clock is UTC, so a tenant in Lisbon and one in Warsaw asking for 08:00 mean two
    # different instants. The tick itself is cheap — one query per org, and on most hours it
    # matches nothing.
    cron_jobs=[cron(reporting_tick, minute=5)],
    # Enqueued by name: the per-client scheduling decision, and the run itself.
    worker_functions=[reporting_schedule_report, reporting_run_report],
)

registry.register(module)
