"""Permissions the companies module contributes (issue #19, CLAUDE.md §6)."""

from __future__ import annotations

from app.core.permissions import ROLE_ADMIN, ROLE_CLIENT, ROLE_MEMBER, PermissionSpec

COMPANY_PERMISSIONS: list[PermissionSpec] = [
    PermissionSpec(
        "companies.company.read",
        position=10,
        default_roles=(ROLE_ADMIN, ROLE_MEMBER, ROLE_CLIENT),
    ),
    PermissionSpec("companies.company.write", position=20),
    PermissionSpec("companies.company.delete", position=30),
    # The company data horizon (#191): group CRUD, company assignment and per-member
    # visibility assignment are one administrative capability. Admin-only by default;
    # reaches existing orgs through the startup reconciler (§15).
    PermissionSpec("companies.group.manage", position=40, default_roles=(ROLE_ADMIN,)),
]
