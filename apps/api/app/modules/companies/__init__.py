"""companies module — the reference implementation of the module pattern (CLAUDE.md §6).

Importing this package self-registers the module (router, panels, mcp seam, i18n namespace)
into the shared registry. ``main.py`` imports it for each enabled module.
"""

from __future__ import annotations

from app.core.scope import register_company_scope_resolver
from app.modules.companies.groups import resolve_membership_company_scope
from app.modules.companies.impex import COMPANY_IMPEX
from app.modules.companies.mcp import COMPANY_MCP_TOOLS
from app.modules.companies.panels import company_details_panel
from app.modules.companies.permissions import COMPANY_PERMISSIONS
from app.modules.companies.router import router
from app.registry import ModuleDescriptor, registry

# The company data horizon (#191): this module owns the assignment tables, so it hands core
# the resolver through the seam — core never imports a module's internals (§6).
register_company_scope_resolver(resolve_membership_company_scope)

module = ModuleDescriptor(
    name="companies",
    router=router,
    i18n_namespace="companies",
    panels=[company_details_panel],
    permissions=COMPANY_PERMISSIONS,
    mcp_tools=COMPANY_MCP_TOOLS,
    impex=[COMPANY_IMPEX],
)

registry.register(module)
