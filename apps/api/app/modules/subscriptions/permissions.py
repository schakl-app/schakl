"""Permissions the subscriptions module introduces (issue #30, CLAUDE.md §15).

Money — a client's recurring fee is commercially sensitive, so reads default to admins, like
the revenue report. A tenant may widen it per role.
"""

from __future__ import annotations

from app.core.permissions import PermissionSpec

SUBSCRIPTION_PERMISSIONS: list[PermissionSpec] = [
    PermissionSpec("subscriptions.subscription.read", position=10),
    PermissionSpec("subscriptions.subscription.write", position=20),
    PermissionSpec("subscriptions.subscription.delete", position=30),
    # Tenant-configurable types + presets (issue #142): reading rides the subscription read
    # grant (types label money-bearing rows); managing the catalogs is admin-only by default.
    PermissionSpec("subscriptions.type.manage", position=40),
    PermissionSpec("subscriptions.template.manage", position=50),
]
