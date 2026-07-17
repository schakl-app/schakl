"""websites module (CLAUDE.md §6, issue #94) — an optional website per domain.

Importing this package self-registers the module (router, permissions, i18n namespace) into the
shared registry. It contributes no company panel — a website renders under *its domain*.
"""

from __future__ import annotations

from app.modules.websites.permissions import WEBSITE_PERMISSIONS
from app.modules.websites.router import router
from app.registry import ModuleDescriptor, registry

module = ModuleDescriptor(
    name="websites",
    router=router,
    i18n_namespace="websites",
    # Licensed module (issue #137). Its own sku — the web-assets trio (domains, websites,
    # hosting) is bundled in *license documents* (a plan lists all three skus), never in code.
    sku="websites",
    permissions=WEBSITE_PERMISSIONS,
)

registry.register(module)
