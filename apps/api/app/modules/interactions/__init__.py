"""interactions module (CLAUDE.md §6, issue #22) — contactmomenten, the touchpoint timeline.

Free and standalone: meetings, calls and notes are logged by hand; the licensed ``google``
module feeds matched emails in through :mod:`app.modules.interactions.system`. Importing this
package self-registers the module (router, company panel, permissions, i18n namespace).
"""

from __future__ import annotations

from app.modules.interactions.panels import interactions_company_panel
from app.modules.interactions.permissions import INTERACTION_PERMISSIONS
from app.modules.interactions.router import router
from app.registry import ModuleDescriptor, registry

module = ModuleDescriptor(
    name="interactions",
    router=router,
    i18n_namespace="interactions",
    panels=[interactions_company_panel],
    permissions=INTERACTION_PERMISSIONS,
)

registry.register(module)
