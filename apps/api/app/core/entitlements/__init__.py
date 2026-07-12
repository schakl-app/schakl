"""Entitlements core (issue #137) — licensed modules behind a signed license key.

A core, cross-cutting capability like custom fields (§13) and RBAC (§15): modules declare a
``sku`` on their :class:`~app.registry.ModuleDescriptor` and this package owns everything
else — offline Ed25519 verification of the license key, the single instance-level license
row, the enable-time gate, the read-only-after-expiry write gate, and the license router.

Design rules (issue #137 / epic #140): verification is **offline** against a baked-in public
key; expiry is **graceful** (grace window → read-only, never data loss, exports always work);
gating is **one seam** (module enablement + a mount-time dependency), never sprinkled.
"""

from app.core.entitlements.service import (
    ensure_modules_enableable,
    invalidate_license_cache,
    license_state,
    license_write_gate,
)

__all__ = [
    "ensure_modules_enableable",
    "invalidate_license_cache",
    "license_state",
    "license_write_gate",
]
