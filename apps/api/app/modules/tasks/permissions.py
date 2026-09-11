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
        # `client` holds `:own` too: a client's contact person owns no block, so for an
        # external login *own* resolves to "the blocks on a task I may read" — when the agency
        # has planned the work on their account (`TaskScheduleService.list_in_range`).
        default_own_roles=(ROLE_MEMBER, ROLE_CLIENT),
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
        # `client` joined for the portal: commenting on the visible tasks of their own
        # companies is the collaboration the visibility checkbox exists for.
        default_own_roles=(ROLE_MEMBER, ROLE_CLIENT),
    ),
    PermissionSpec("tasks.label.write", position=60),
    PermissionSpec("tasks.status.write", position=65),
    PermissionSpec("tasks.checklist_template.write", position=70),
    PermissionSpec("tasks.template.write", position=80),
    PermissionSpec("tasks.template.apply", position=90),
    # Instellingen → Taken: the e-mail intake address and its defaults. Org-wide configuration,
    # so admin-only by default like every other settings.manage key; reading a parked mail and
    # finishing it rides ``tasks.task.create`` — it is the sender's own mail, and finishing it
    # *is* creating a task.
    PermissionSpec("tasks.settings.manage", position=95),
]
