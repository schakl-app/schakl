"""tasks module (CLAUDE.md §6, §10) — to-dos, attachable to companies, assignable to employees.

Importing this package self-registers the module (router, company panel, mcp seam, i18n
namespace) into the shared registry.
"""

from __future__ import annotations

from app.modules.tasks.mcp import TASK_MCP_TOOLS
from app.modules.tasks.panels import tasks_company_panel
from app.modules.tasks.router import router
from app.registry import ModuleDescriptor, registry

module = ModuleDescriptor(
    name="tasks",
    router=router,
    i18n_namespace="tasks",
    panels=[tasks_company_panel],
    mcp_tools=TASK_MCP_TOOLS,
)

registry.register(module)
