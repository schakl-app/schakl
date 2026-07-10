"""leave module (CLAUDE.md §6, §14) — employee PTO: requests, approvals, balances.

Importing this package self-registers the module (router + i18n namespace) into the shared
registry. Leave attaches to *employees* (users/memberships), not companies, so it
contributes no company panels; the calendar and timesheet read it via its REST feed.
"""

from __future__ import annotations

from arq import cron

from app.modules.leave.jobs import import_next_year_holidays
from app.modules.leave.permissions import LEAVE_PERMISSIONS
from app.modules.leave.router import router
from app.registry import ModuleDescriptor, registry

module = ModuleDescriptor(
    name="leave",
    router=router,
    i18n_namespace="leave",
    permissions=LEAVE_PERMISSIONS,
    # Next year's holidays, imported in December while there is still time to correct them
    # (#47). Idempotent and per-org; a tenant can switch it off with `holiday_auto_import`.
    cron_jobs=[cron(import_next_year_holidays, month=12, day=1, hour=3, minute=0)],
)

registry.register(module)
