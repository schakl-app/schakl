"""Entitlements core (issue #137) — licensed modules behind a signed license key.

A core, cross-cutting capability like custom fields (§13) and RBAC (§15): modules declare a
``sku`` on their :class:`~app.registry.ModuleDescriptor` and this package owns everything
else — offline Ed25519 verification of the license key, the single instance-level license
row, the enable-time gate, the read-only-after-expiry write gate, and the license router.

Design rules (issue #137 / epic #140): verification is **offline** against a baked-in public
key; expiry is **graceful** (grace window → read-only, never data loss, exports always work);
gating is **one seam** (module enablement + a mount-time dependency), never sprinkled.

On the **cloud** posture that last rule gains a second authority: the instance key is the
operator's, and a tenant's own modules follow the tenant's ``orgs.plan``. ``sku_writable`` is
the one place that decides which of the two applies — see ``service.py``'s docstring.
"""

from app.core.entitlements.service import (
    OrgPlan,
    ensure_modules_enableable,
    ensure_requirements_met,
    invalidate_license_cache,
    invalidate_plan_cache,
    license_exempt,
    license_state,
    license_write_gate,
    refusal_for,
    sku_cron_enabled,
    sku_writable,
)

__all__ = [
    "OrgPlan",
    "ensure_modules_enableable",
    "ensure_requirements_met",
    "invalidate_license_cache",
    "invalidate_plan_cache",
    "license_exempt",
    "license_state",
    "license_write_gate",
    "refusal_for",
    "sku_cron_enabled",
    "sku_writable",
]
