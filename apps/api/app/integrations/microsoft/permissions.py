"""Permissions the microsoft module introduces (CLAUDE.md §15).

The same shape as the Google integration's, for the same reasons: connecting one's own account
and using the surfaces default to every member — the whole point of per-user OAuth is that each
person grants (and can revoke) their own access — while org-wide configuration (the Entra app
registration, the surface toggles, the OneDrive layout, the Outlook policy) is admin-only. The
OneDrive keys split the way Drive's do: giving a record its first folder is ordinary write work,
re-pointing or detaching one silently moves where every colleague's uploads land and is
``manage``.
"""

from __future__ import annotations

from app.core.permissions import ROLE_ADMIN, ROLE_MEMBER, PermissionSpec

MICROSOFT_PERMISSIONS: list[PermissionSpec] = [
    PermissionSpec("microsoft.settings.manage", position=10),
    PermissionSpec(
        "microsoft.connection.manage",
        position=20,
        default_roles=(ROLE_ADMIN, ROLE_MEMBER),
    ),
    PermissionSpec(
        "microsoft.calendar.read",
        position=30,
        default_roles=(ROLE_ADMIN, ROLE_MEMBER),
    ),
    PermissionSpec(
        "microsoft.onedrive.read",
        position=40,
        default_roles=(ROLE_ADMIN, ROLE_MEMBER),
    ),
    PermissionSpec(
        "microsoft.onedrive.write",
        position=50,
        default_roles=(ROLE_ADMIN, ROLE_MEMBER),
    ),
    PermissionSpec("microsoft.onedrive.manage", position=60),
]
