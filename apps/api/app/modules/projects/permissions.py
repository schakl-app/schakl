"""Permissions the projects module contributes (issue #19, CLAUDE.md §6)."""

from __future__ import annotations

from app.core.permissions import ROLE_ADMIN, ROLE_CLIENT, ROLE_MEMBER, PermissionSpec

PROJECT_PERMISSIONS: list[PermissionSpec] = [
    PermissionSpec(
        "projects.project.read",
        position=10,
        default_roles=(ROLE_ADMIN, ROLE_MEMBER, ROLE_CLIENT),
    ),
    PermissionSpec("projects.project.write", position=20),
    PermissionSpec("projects.project.delete", position=30),
    # Org-wide projects settings (the budget alert). Admin-only by default: a global threshold
    # is agency policy, not a per-member preference.
    PermissionSpec("projects.settings.manage", position=40),
]
