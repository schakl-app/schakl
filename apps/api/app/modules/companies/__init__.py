"""companies module — the reference implementation of the module pattern (CLAUDE.md §6).

Importing this package self-registers the module (router, panels, mcp seam, i18n namespace)
into the shared registry. ``main.py`` imports it for each enabled module.
"""

from __future__ import annotations

from app.modules.companies.mcp import COMPANY_MCP_TOOLS
from app.modules.companies.panels import company_details_panel
from app.modules.companies.router import router
from app.registry import ModuleDescriptor, registry

module = ModuleDescriptor(
    name="companies",
    router=router,
    i18n_namespace="companies",
    panels=[company_details_panel],
    mcp_tools=COMPANY_MCP_TOOLS,
)

registry.register(module)
