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
    # only an admin configures them (#17).
    PermissionSpec("notifications.channels.manage", position=40),
]
