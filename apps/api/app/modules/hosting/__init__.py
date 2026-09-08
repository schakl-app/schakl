"""hosting module (CLAUDE.md §6, issue #93) — hosting entities a website points at.

Importing this package self-registers the module (router, permissions, i18n namespace) into
the shared registry. It contributes no company panel (owner feedback): hosting is shared
infrastructure administered under Instellingen — the client page shows the client's
*websites*, and each website names its hosting.
"""

from __future__ import annotations

from app.core.trash import register_trash_dependent
from app.modules.hosting.impex import HOSTING_IMPEX
from app.modules.hosting.permissions import HOSTING_PERMISSIONS
from app.modules.hosting.router import router
from app.modules.hosting.trash import HOSTING_TRASH_DEPENDENTS
from app.registry import ModuleDescriptor, registry

module = ModuleDescriptor(
    name="hosting",
    router=router,
    i18n_namespace="hosting",
    # Licensed module (issue #137). Its own sku — the web-assets trio (domains, websites,
    # hosting) is bundled in *license documents* (a plan lists all three skus), never in code.
    sku="hosting",
    permissions=HOSTING_PERMISSIONS,
    impex=[HOSTING_IMPEX],
)

registry.register(module)

# What deleting a client means for the rows this module holds about it (docs/TRASH.md).
for _dependent in HOSTING_TRASH_DEPENDENTS:
    register_trash_dependent("company", _dependent)
