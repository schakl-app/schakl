"""Permissions the time module contributes (issue #19, CLAUDE.md §6).

``time.entry.read`` is scoped because the difference is real: a member reads their own entries,
a manager reads anyone's. Note that ``/time/logged`` and ``/time/entries`` declare it with **no
scope** — a project's logged hours are team-visible, and that is what draws every budget bar. The
service escalates to ``scope="any"`` only for the unscoped, org-wide ``all_users`` report.
"""

from __future__ import annotations

from app.core.permissions import ROLE_ADMIN, ROLE_MEMBER, SCOPES, PermissionSpec

TIME_PERMISSIONS: list[PermissionSpec] = [
    PermissionSpec(
        "time.entry.read",
        scopes=SCOPES,
        position=10,
        default_roles=(ROLE_ADMIN,),
        default_own_roles=(ROLE_MEMBER,),
    ),
    PermissionSpec(
        "time.entry.write",
        scopes=SCOPES,
        position=20,
        default_roles=(ROLE_ADMIN,),
        default_own_roles=(ROLE_MEMBER,),
    ),
    # Signing hours off — and therefore also the right to edit an already-approved entry.
    PermissionSpec("time.entry.approve", position=30),
    PermissionSpec("time.entry.invoice", position=40),
    PermissionSpec("time.report.read", position=50),
]
