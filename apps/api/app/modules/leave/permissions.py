"""Permissions the leave module contributes (issue #19, CLAUDE.md §6, §14)."""

from __future__ import annotations

from app.core.permissions import ROLE_ADMIN, ROLE_MEMBER, SCOPES, PermissionSpec

LEAVE_PERMISSIONS: list[PermissionSpec] = [
    PermissionSpec(
        "leave.request.read",
        scopes=SCOPES,
        position=10,
        default_roles=(ROLE_ADMIN,),
        default_own_roles=(ROLE_MEMBER,),
    ),
    PermissionSpec(
        "leave.request.write",
        scopes=SCOPES,
        position=20,
        default_roles=(ROLE_ADMIN,),
        default_own_roles=(ROLE_MEMBER,),
    ),
    # The approver picker is built from the holders of this one.
    PermissionSpec("leave.request.approve", position=30),
    PermissionSpec("leave.type.write", position=40),
    PermissionSpec("leave.profile.manage", position=50),
    # Reading the calendar rides on ``leave.request.read`` — everyone needs to see the holidays.
    PermissionSpec("leave.holiday.write", position=55),
    PermissionSpec(
        "leave.entitlement.read",
        scopes=SCOPES,
        position=60,
        default_roles=(ROLE_ADMIN,),
        default_own_roles=(ROLE_MEMBER,),
    ),
    PermissionSpec("leave.entitlement.write", position=70),
    # Hourly rate (#82) — salary-adjacent, kept off ``leave.profile.manage`` so a tenant can let
    # someone manage schedules without seeing pay. Read is own/any (a member sees their own rate,
    # like their own entitlement); write is admin-only (setting pay is not a member's act).
    PermissionSpec(
        "leave.rate.read",
        scopes=SCOPES,
        position=80,
        default_roles=(ROLE_ADMIN,),
        default_own_roles=(ROLE_MEMBER,),
    ),
    PermissionSpec("leave.rate.write", position=90, default_roles=(ROLE_ADMIN,)),
]
