"""Permissions the marketing module introduces (epic #134, CLAUDE.md §15).

Three capabilities: reading a client's marketing performance (the panel + tab), managing the
account links, and the cross-client overview grid. Reading a client's own chips rides the
company read; the *metrics* get their own read grant so ad spend can be narrowed per role.
"""

from __future__ import annotations

from app.core.permissions import PermissionSpec

MARKETING_PERMISSIONS: list[PermissionSpec] = [
    # A client's marketing performance is what the account manager works with, so members see
    # it by default; a tenant that treats ad spend as sensitive narrows this to admin.
    # `client` joined for the portal (#193): a logged-in contact reads their companies'
    # curated dashboards. Reaches existing orgs once via the startup reconciler (§15).
    PermissionSpec(
        "marketing.metrics.read", position=10, default_roles=("admin", "member", "client")
    ),
    # Linking/unlinking GA4/GSC/Ads accounts (and listing what a connection can access) is
    # configuration — admin by default, like the Google settings themselves.
    PermissionSpec("marketing.link.manage", position=20),
    # The cross-client morning-coffee grid (Overzicht → Marketing) is a reporting screen, and
    # reporting is manager-only here (docs/UX.md).
    PermissionSpec("marketing.report.read", position=30),
]
