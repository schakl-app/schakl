"""Permissions the tasks module contributes (issue #19, CLAUDE.md §6).

``tasks.task.write`` is scoped, and *own* means **assignee** — that is the answer to #12: a
non-primary assignee may write the task assigned to them, and nothing else. The seeded ``member``
role holds ``:own``; ``:any`` is an admin capability.
"""

from __future__ import annotations

from app.core.permissions import (
    ROLE_ADMIN,
    ROLE_CLIENT,
    ROLE_MEMBER,
    SCOPES,
    PermissionSpec,
)

TASK_PERMISSIONS: list[PermissionSpec] = [
    PermissionSpec(
        "tasks.task.read",
        position=10,
        default_roles=(ROLE_ADMIN, ROLE_MEMBER, ROLE_CLIENT),
    ),
    PermissionSpec("tasks.task.create", position=20, default_roles=(ROLE_ADMIN, ROLE_MEMBER)),
    PermissionSpec(
        "tasks.task.write",
        scopes=SCOPES,
        position=30,
        default_roles=(ROLE_ADMIN,),
        default_own_roles=(ROLE_MEMBER,),
    ),
    PermissionSpec("tasks.task.delete", position=40),
    # Scheduling a task onto a calendar (#188) is its own capability, distinct from editing the
    # task: a member may plan their own time (``:own`` — a block for themselves), a manager may
    # schedule anyone (``:any``). Read is scoped the same way — ``:any`` is what lets a manager
    # overlay a colleague's schedule feed on the Agenda.
    PermissionSpec(
        "tasks.schedule.read",
        scopes=SCOPES,
        position=42,
        default_roles=(ROLE_ADMIN,),
        default_own_roles=(ROLE_MEMBER,),
    ),
    PermissionSpec(
        "tasks.schedule.write",
        scopes=SCOPES,
        position=44,
        default_roles=(ROLE_ADMIN,),
        default_own_roles=(ROLE_MEMBER,),
    ),
    PermissionSpec(
        "tasks.comment.write",
        scopes=SCOPES,
        position=50,
        default_roles=(ROLE_ADMIN,),
        default_own_roles=(ROLE_MEMBER,),
    ),
    PermissionSpec("tasks.label.write", position=60),
    PermissionSpec("tasks.status.write", position=65),
    PermissionSpec("tasks.checklist_template.write", position=70),
    PermissionSpec("tasks.template.write", position=80),
    PermissionSpec("tasks.template.apply", position=90),
]
