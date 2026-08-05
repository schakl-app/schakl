"""contacts module (CLAUDE.md §6) — client people, attachable to companies.

Importing this package self-registers the module (router, company panel, mcp seam, i18n
namespace) into the shared registry. ``main.py`` imports it for each enabled module.
"""

from __future__ import annotations

from app.core.portal import (
    register_portal_subject_provider,
    register_portal_user_resolver,
)
from app.core.scope import SCOPE_SOURCE_PORTAL, register_company_scope_resolver
from app.modules.contacts.bulk import CONTACT_BULK
from app.modules.contacts.impex import CONTACT_IMPEX, CONTACT_ON_COMPANY_EXTENSION
from app.modules.contacts.mcp import CONTACT_MCP_TOOLS
from app.modules.contacts.panels import contacts_company_panel
from app.modules.contacts.permissions import CONTACT_PERMISSIONS
from app.modules.contacts.portal import (
    ContactPortalSubjectProvider,
    resolve_portal_company_scope,
    resolve_portal_users,
)
from app.modules.contacts.router import router
from app.registry import ModuleDescriptor, registry

# The client portal's data horizon (#193, on #191's seam): a contact-linked membership sees
# exactly its contact's companies — live, and never unrestricted. Whether this source
# restricted *is* "is this user contact-linked", so ``require_context`` reads the answer off
# the resolution rather than asking us again.
register_company_scope_resolver(resolve_portal_company_scope, key=SCOPE_SOURCE_PORTAL)
# …and lets other modules ask "is this user a portal login?" without importing our models
# (notification fan-out keeps staff events out of client inboxes).
register_portal_user_resolver(resolve_portal_users)
# A contact is a **portal subject**: the person a client login can belong to. The portal module
# invites, disables and impersonates through this handle, so it never learns that the link is a
# column on our table — and registering it here, rather than there, is what keeps the
# dependency pointing the one direction §6 allows.
register_portal_subject_provider(ContactPortalSubjectProvider())

module = ModuleDescriptor(
    name="contacts",
    router=router,
    i18n_namespace="contacts",
    panels=[contacts_company_panel],
    permissions=CONTACT_PERMISSIONS,
    mcp_tools=CONTACT_MCP_TOOLS,
    impex=[CONTACT_IMPEX],
    bulk=[CONTACT_BULK],
    # The client's contact person, carried in the company import's own row — contributed the
    # way panels are, so companies never learns contacts' internals (CLAUDE.md §17).
    impex_extensions=[CONTACT_ON_COMPANY_EXTENSION],
)

registry.register(module)
