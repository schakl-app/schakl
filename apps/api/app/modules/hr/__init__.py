"""hr module (§6) — the employee dossier behind the personal page.

Importing this package self-registers the module (router, permissions, i18n namespace).
"""

from __future__ import annotations

from app.modules.hr.permissions import HR_PERMISSIONS
from app.modules.hr.router import router
from app.registry import ModuleDescriptor, registry

module = ModuleDescriptor(
    name="hr",
    router=router,
    i18n_namespace="hr",
    # Licensed module (issue #137): enabling hr requires a license covering this sku;
    # past expiry+grace it goes read-only (mutations 402) — reads and exports stay.
    sku="hr",
    permissions=HR_PERMISSIONS,
)

registry.register(module)
