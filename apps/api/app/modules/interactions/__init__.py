"""interactions module (CLAUDE.md §6, issue #22) — contactmomenten, the touchpoint timeline.

Standalone: meetings, calls and notes are logged by hand; the separately licensed ``google``
module feeds matched emails in through :mod:`app.modules.interactions.system`. Importing this
package self-registers the module (router, company panel, permissions, i18n namespace).
"""

from __future__ import annotations

from arq import cron

from app.modules.interactions.bulk import INTERACTION_BULK
from app.modules.interactions.jobs import (
    interactions_enrich_task,
    interactions_reap_stale_enrichment,
)
from app.modules.interactions.panels import interactions_company_panel
from app.modules.interactions.permissions import INTERACTION_PERMISSIONS
from app.modules.interactions.router import router
from app.registry import ModuleDescriptor, registry

module = ModuleDescriptor(
    name="interactions",
    router=router,
    i18n_namespace="interactions",
    # Licensed module (issue #137): enabling interactions requires a license covering this
    # sku; past expiry+grace it goes read-only (mutations 402) — reads and exports stay.
    sku="interactions",
    panels=[interactions_company_panel],
    permissions=INTERACTION_PERMISSIONS,
    bulk=[INTERACTION_BULK],
    # "Laat schakl deze taak invullen" (#327) runs in the worker: an email's body lands *after*
    # the approving request has committed, so nothing about this can happen inside it.
    worker_functions=[interactions_enrich_task],
    # A run claimed by a worker that is no longer there has no other way back (#300).
    cron_jobs=[cron(interactions_reap_stale_enrichment, minute={0, 15, 30, 45})],
)

registry.register(module)
