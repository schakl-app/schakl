"""leave module (CLAUDE.md §6, §14) — employee PTO: requests, approvals, balances.

Importing this package self-registers the module (router + i18n namespace) into the shared
registry. Leave attaches to *employees* (users/memberships), not companies, so it
contributes no company panels; the calendar and timesheet read it via its REST feed.
"""

from __future__ import annotations

from arq import cron

from app.modules.leave.jobs import (
    generate_next_year_entitlements,
    generate_recurring_free_days,
    import_next_year_holidays,
)
from app.modules.leave.mcp import LEAVE_MCP_TOOLS
from app.modules.leave.permissions import LEAVE_PERMISSIONS
from app.modules.leave.router import router
from app.registry import ModuleDescriptor, registry

module = ModuleDescriptor(
    name="leave",
    router=router,
    i18n_namespace="leave",
    # Licensed module (issue #137): enabling leave requires a license covering this sku;
    # past expiry+grace the module goes read-only (mutations 402) — reads and exports stay.
    sku="leave",
    permissions=LEAVE_PERMISSIONS,
    mcp_tools=LEAVE_MCP_TOOLS,
    # Next year's holidays, imported in December while there is still time to correct them
    # (#47). Idempotent and per-org; a tenant can switch it off with `holiday_auto_import`.
    # Next year's entitlements follow an hour later (#108), so the whole staff's next-year
    # balance exists before anyone books their summer — first touch seeds an individual pot
    # earlier if someone plans further ahead.
    cron_jobs=[
        cron(import_next_year_holidays, month=12, day=1, hour=3, minute=0),
        cron(generate_next_year_entitlements, month=12, day=1, hour=4, minute=0),
        # Rostered free days roll forward monthly (#107), *after* the December entitlement
        # top-up above so a January occurrence finds its next-year ADV pot already seeded.
        cron(generate_recurring_free_days, day=1, hour=4, minute=30),
    ],
)

registry.register(module)
