"""Permissions the websites module contributes (issue #19, CLAUDE.md §6, §15)."""

from __future__ import annotations

from app.core.permissions import ROLE_ADMIN, ROLE_CLIENT, ROLE_MEMBER, PermissionSpec

WEBSITE_PERMISSIONS: list[PermissionSpec] = [
    PermissionSpec(
        "websites.website.read",
        position=10,
        default_roles=(ROLE_ADMIN, ROLE_MEMBER, ROLE_CLIENT),
    ),
    PermissionSpec("websites.website.write", position=20),
    PermissionSpec("websites.website.delete", position=30),
]
