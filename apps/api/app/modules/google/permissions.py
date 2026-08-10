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
    # Giving a record its *first* Drive folder is ordinary work and stays on
    # ``google.drive.write``; **re-pointing or detaching one** is this permission (#21
    # follow-up). The two are different acts: the first is additive, the second silently
    # redirects where every colleague's uploads land and where project folders nest, while the
    # history stays behind in a folder nobody is looking at any more. Admin-only by default,
    # like the org-wide Drive layout it is the per-client half of.
    PermissionSpec("google.drive.manage", position=60),
]
