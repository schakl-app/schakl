"""contacts module (CLAUDE.md §6) — client people, attachable to companies.

Importing this package self-registers the module (router, company panel, mcp seam, i18n
namespace) into the shared registry. ``main.py`` imports it for each enabled module.
"""

from __future__ import annotations

from app.core.portal import register_portal_user_resolver
from app.core.scope import register_company_scope_resolver
from app.modules.contacts.impex import CONTACT_IMPEX
from app.modules.contacts.mcp import CONTACT_MCP_TOOLS
from app.modules.contacts.panels import contacts_company_panel
from app.modules.contacts.permissions import CONTACT_PERMISSIONS
from app.modules.contacts.portal import resolve_portal_company_scope, resolve_portal_users
from app.modules.contacts.router import router
from app.registry import ModuleDescriptor, registry

# The client portal's data horizon (#193, on #191's seam): a contact-linked membership sees
# exactly its contact's companies — live, and never unrestricted.
register_company_scope_resolver(resolve_portal_company_scope)
# …and lets other modules ask "is this user a portal login?" without importing our models
# (notification fan-out keeps staff events out of client inboxes).
register_portal_user_resolver(resolve_portal_users)

module = ModuleDescriptor(
    name="contacts",
    router=router,
    i18n_namespace="contacts",
    panels=[contacts_company_panel],
    permissions=CONTACT_PERMISSIONS,
    mcp_tools=CONTACT_MCP_TOOLS,
    impex=[CONTACT_IMPEX],
)

registry.register(module)
