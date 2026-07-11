"""hosting module (CLAUDE.md §6, issue #93) — hosting entities a website points at.

Importing this package self-registers the module (router, company panel, permissions, i18n
namespace) into the shared registry.
"""

from __future__ import annotations

from app.modules.hosting.panels import hosting_company_panel
from app.modules.hosting.permissions import HOSTING_PERMISSIONS
from app.modules.hosting.router import router
from app.registry import ModuleDescriptor, registry

module = ModuleDescriptor(
    name="hosting",
    router=router,
    i18n_namespace="hosting",
    panels=[hosting_company_panel],
    permissions=HOSTING_PERMISSIONS,
)

registry.register(module)
