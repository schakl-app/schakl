"""Permissions the subscriptions module introduces (issue #30, CLAUDE.md §15).

Money — a client's recurring fee is commercially sensitive, so reads default to admins, like
the revenue report. A tenant may widen it per role.
"""

from __future__ import annotations

from app.core.permissions import ROLE_ADMIN, ROLE_CLIENT, SCOPES, PermissionSpec

SUBSCRIPTION_PERMISSIONS: list[PermissionSpec] = [
    # Scoped the way `invoicing.invoice.read` is (#266): `:own` is a client reading the
    # agreements on their own companies through the portal — what they pay for, and when the
    # next invoice comes — while the module's own surfaces (the MRR summary, the preset
    # library with its prices) ride `:any`, because there is no client whose price list that
    # is. The horizon decides *whose*; the scope fences what a horizon cannot.
    PermissionSpec(
        "subscriptions.subscription.read",
        scopes=SCOPES,
        position=10,
        default_roles=(ROLE_ADMIN,),
        default_own_roles=(ROLE_CLIENT,),
    ),
    PermissionSpec("subscriptions.subscription.write", position=20),
    PermissionSpec("subscriptions.subscription.delete", position=30),
    # Tenant-configurable types + presets (issue #142): reading rides the subscription read
    # grant (types label money-bearing rows); managing the catalogs is admin-only by default.
    PermissionSpec("subscriptions.type.manage", position=40),
    PermissionSpec("subscriptions.template.manage", position=50),
]
