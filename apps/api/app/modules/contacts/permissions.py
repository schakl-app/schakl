"""Permissions the contacts module contributes (issue #19, CLAUDE.md §6)."""

from __future__ import annotations

from app.core.permissions import ROLE_ADMIN, ROLE_CLIENT, ROLE_MEMBER, PermissionSpec

CONTACT_PERMISSIONS: list[PermissionSpec] = [
    PermissionSpec(
        "contacts.contact.read",
        position=10,
        default_roles=(ROLE_ADMIN, ROLE_MEMBER, ROLE_CLIENT),
    ),
    PermissionSpec("contacts.contact.write", position=20),
    PermissionSpec("contacts.contact.delete", position=30),
    # Attaching a contact to a company is a distinct capability from editing the contact.
    PermissionSpec("contacts.link.write", position=40),
    # Tenant-configurable contact types (issue #91): everyone reads them (to type a link and to
    # filter); managing the catalog under Instellingen is admin-only.
    PermissionSpec(
        "contacts.type.read",
        position=50,
        default_roles=(ROLE_ADMIN, ROLE_MEMBER, ROLE_CLIENT),
    ),
    PermissionSpec("contacts.type.manage", position=60),
]
