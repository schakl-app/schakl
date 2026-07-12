"""google module (CLAUDE.md §6, issue #22) — the Workspace integration, licensed as one sku.

One registry module holding the core (OAuth connect, encrypted token vault, "act as user X"
client factory) plus the ``calendar/``, ``drive/`` and ``gmail/`` surface subpackages of
docs/GOOGLE.md §3. They stay separate packages internally — each owns its models, sync logic
and routes — but license, enablement and i18n are one unit: ``sku="google"`` is the whole
commercial boundary (issue #137), so an expired license turns every Google mutation 402 at the
mount-time gate, and the module's own crons additionally stand down (they write on a schedule,
not on a request, so the route gate alone would not stop them).

Importing this package self-registers the module.
"""

from __future__ import annotations

from app.modules.google.permissions import GOOGLE_PERMISSIONS
from app.modules.google.router import router
from app.registry import ModuleDescriptor, registry

module = ModuleDescriptor(
    name="google",
    router=router,
    i18n_namespace="google",
    # Licensed module (issue #137): enabling the Google integration requires a license
    # covering this sku; past expiry+grace it goes read-only (mutations 402, crons stand down).
    sku="google",
    permissions=GOOGLE_PERMISSIONS,
)

registry.register(module)
