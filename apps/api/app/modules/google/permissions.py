"""Permissions the google module introduces (CLAUDE.md §15).

Connecting one's own account and using the surfaces default to every member — the whole point
of per-user OAuth is that each person grants (and can revoke) their own access. Org-wide
configuration (the OAuth client, surface toggles, Drive layout, gmail policy) is admin-only.
"""

from __future__ import annotations

from app.core.permissions import ROLE_ADMIN, ROLE_MEMBER, PermissionSpec

GOOGLE_PERMISSIONS: list[PermissionSpec] = [
    PermissionSpec("google.settings.manage", position=10),
    PermissionSpec(
        "google.connection.manage",
        position=20,
        default_roles=(ROLE_ADMIN, ROLE_MEMBER),
    ),
    PermissionSpec(
        "google.calendar.read",
        position=30,
        default_roles=(ROLE_ADMIN, ROLE_MEMBER),
    ),
    PermissionSpec(
        "google.drive.read",
        position=40,
        default_roles=(ROLE_ADMIN, ROLE_MEMBER),
    ),
    PermissionSpec(
        "google.drive.write",
        position=50,
        default_roles=(ROLE_ADMIN, ROLE_MEMBER),
    ),
]
