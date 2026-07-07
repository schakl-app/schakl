"""contacts module (CLAUDE.md §6) — client people, attachable to companies.

Importing this package self-registers the module (router, company panel, mcp seam, i18n
namespace) into the shared registry. ``main.py`` imports it for each enabled module.
"""

from __future__ import annotations

from app.modules.contacts.mcp import CONTACT_MCP_TOOLS
from app.modules.contacts.panels import contacts_company_panel
from app.modules.contacts.router import router
from app.registry import ModuleDescriptor, registry

module = ModuleDescriptor(
    name="contacts",
    router=router,
    i18n_namespace="contacts",
    panels=[contacts_company_panel],
    mcp_tools=CONTACT_MCP_TOOLS,
)

registry.register(module)
