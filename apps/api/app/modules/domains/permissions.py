"""Permissions the domains module contributes (issue #19, CLAUDE.md §6, §15)."""

from __future__ import annotations

from app.core.permissions import ROLE_ADMIN, ROLE_CLIENT, ROLE_MEMBER, PermissionSpec

DOMAIN_PERMISSIONS: list[PermissionSpec] = [
    PermissionSpec(
        "domains.domain.read",
        position=10,
        default_roles=(ROLE_ADMIN, ROLE_MEMBER, ROLE_CLIENT),
    ),
    PermissionSpec("domains.domain.write", position=20),
    PermissionSpec("domains.domain.delete", position=30),
]
