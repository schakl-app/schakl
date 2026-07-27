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
    # TLD price list (#250): members may *see* prices (the domain form shows the resolved
    # rate), managing the list and bulk increases is an admin's call by default.
    PermissionSpec(
        "domains.tld_price.read",
        position=40,
        default_roles=(ROLE_ADMIN, ROLE_MEMBER),
    ),
    PermissionSpec("domains.tld_price.manage", position=50),
]
