"""time module (CLAUDE.md §6, §10) — timer + manual entries + weekly timesheet.

Importing this package self-registers the module (router, company panel, mcp seam, i18n
namespace) into the shared registry.
"""

from __future__ import annotations

from app.modules.time.mcp import TIME_MCP_TOOLS
from app.modules.time.panels import time_company_panel
from app.modules.time.router import router
from app.registry import ModuleDescriptor, registry

module = ModuleDescriptor(
    name="time",
    router=router,
    i18n_namespace="time",
    panels=[time_company_panel],
    mcp_tools=TIME_MCP_TOOLS,
)

registry.register(module)
