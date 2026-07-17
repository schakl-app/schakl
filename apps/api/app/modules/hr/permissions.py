"""Permissions the hr module contributes (§15)."""

from __future__ import annotations

from app.core.permissions import ROLE_ADMIN, ROLE_MEMBER, PermissionSpec

SCOPES = ("own", "any")

HR_PERMISSIONS: list[PermissionSpec] = [
    # Reading a dossier: every member their own; managing roles anyone's.
    PermissionSpec(
        "hr.dossier.read",
        scopes=SCOPES,
        position=10,
        default_roles=(ROLE_ADMIN,),
        default_own_roles=(ROLE_MEMBER,),
    ),
    # Filing/removing documents is an employer act.
    PermissionSpec("hr.document.manage", position=20, default_roles=(ROLE_ADMIN,)),
]
