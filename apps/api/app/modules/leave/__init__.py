"""leave module (CLAUDE.md §6, §14) — employee PTO: requests, approvals, balances.

Importing this package self-registers the module (router + i18n namespace) into the shared
registry. Leave attaches to *employees* (users/memberships), not companies, so it
contributes no company panels; the calendar and timesheet read it via its REST feed.
"""

from __future__ import annotations

from app.modules.leave.router import router
from app.registry import ModuleDescriptor, registry

module = ModuleDescriptor(
    name="leave",
    router=router,
    i18n_namespace="leave",
)

registry.register(module)
