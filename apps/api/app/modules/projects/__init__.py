"""projects module (CLAUDE.md §6, §10, P2) — client engagements that own to-dos and budgets.

Importing this package self-registers the module (router, company panel, mcp seam, i18n
namespace) into the shared registry.
"""

from __future__ import annotations

from app.modules.projects.mcp import PROJECT_MCP_TOOLS
from app.modules.projects.panels import projects_company_panel
from app.modules.projects.router import router
from app.registry import ModuleDescriptor, registry

module = ModuleDescriptor(
    name="projects",
    router=router,
    i18n_namespace="projects",
    panels=[projects_company_panel],
    mcp_tools=PROJECT_MCP_TOOLS,
)

registry.register(module)
