"""Permissions the interactions module introduces (CLAUDE.md §15).

The timeline is team-visible by design (that is its CRM value), so ``read`` defaults to both
system roles. Writing and deleting are own/any scoped like time entries: a member manages their
own logged touchpoints, an admin anyone's. ``review`` gates the gmail approve/reject/remap
routes, but note the service enforces **strict ownership** on gmail-sourced rows regardless of
scope — there is deliberately no ``:any`` that lets an admin decide about a colleague's mailbox.
"""

from __future__ import annotations

from app.core.permissions import ROLE_ADMIN, ROLE_MEMBER, SCOPES, PermissionSpec

INTERACTION_PERMISSIONS: list[PermissionSpec] = [
    PermissionSpec(
        "interactions.interaction.read",
        position=10,
        default_roles=(ROLE_ADMIN, ROLE_MEMBER),
    ),
    PermissionSpec(
        "interactions.interaction.write",
        scopes=SCOPES,
        position=20,
        default_roles=(ROLE_ADMIN,),
        default_own_roles=(ROLE_MEMBER,),
    ),
    PermissionSpec(
        "interactions.interaction.delete",
        scopes=SCOPES,
        position=30,
        default_roles=(ROLE_ADMIN,),
        default_own_roles=(ROLE_MEMBER,),
    ),
    PermissionSpec(
        "interactions.interaction.review",
        position=40,
        default_roles=(ROLE_ADMIN, ROLE_MEMBER),
    ),
    # Tenant-configurable interaction kinds (#174): everyone who logs needs the list; managing
    # the catalog under Instellingen is admin-only, like contacts.type.manage.
    PermissionSpec(
        "interactions.kind.read",
        position=50,
        default_roles=(ROLE_ADMIN, ROLE_MEMBER),
    ),
    PermissionSpec("interactions.kind.manage", position=60),
]
