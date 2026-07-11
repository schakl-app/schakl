"""Permissions the hosting module contributes (issue #19, CLAUDE.md §6, §15)."""

from __future__ import annotations

from app.core.permissions import ROLE_ADMIN, ROLE_CLIENT, ROLE_MEMBER, PermissionSpec

HOSTING_PERMISSIONS: list[PermissionSpec] = [
    PermissionSpec(
        "hosting.hosting.read",
        position=10,
        default_roles=(ROLE_ADMIN, ROLE_MEMBER, ROLE_CLIENT),
    ),
    PermissionSpec("hosting.hosting.write", position=20),
    PermissionSpec("hosting.hosting.delete", position=30),
]
