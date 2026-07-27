"""Permissions the notifications module contributes (issue #19, CLAUDE.md §6).

Reading your inbox, marking it read and setting your own delivery preferences are things every
role does for itself. Editing the org-wide preference *defaults* is the manager capability — and
it is the one gate in this module that lives in the router rather than a service.
"""

from __future__ import annotations

from app.core.permissions import ROLE_ADMIN, ROLE_CLIENT, ROLE_MEMBER, PermissionSpec

_EVERYONE = (ROLE_ADMIN, ROLE_MEMBER, ROLE_CLIENT)

NOTIFICATION_PERMISSIONS: list[PermissionSpec] = [
    PermissionSpec("notifications.notification.read", position=10, default_roles=_EVERYONE),
    PermissionSpec("notifications.notification.write", position=20, default_roles=_EVERYONE),
    PermissionSpec("notifications.defaults.manage", position=30),
    # External channels embed bot tokens and can be pointed at arbitrary webhooks (SSRF), so
    # only an admin configures the *org's* channels (#17) — the shared rooms everyone sees.
    PermissionSpec("notifications.channels.manage", position=40),
    # Connecting **my own** Slack DM or webhook is a personal setting, like my e-mail cadence
    # (#283). It is a separate key rather than a scope on the one above, because the startup
    # reconciler only grants *new* catalog keys: re-scoping `channels.manage` would have left
    # every already-installed org's members without it, with no data migration able to help
    # (a migration must never import the catalog — docs/WORKFLOW.md).
    PermissionSpec(
        "notifications.channels.manage_own",
        position=50,
        default_roles=(ROLE_ADMIN, ROLE_MEMBER),
    ),
]
