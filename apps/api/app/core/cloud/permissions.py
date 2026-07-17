"""Permissions the cloud posture adds (epic #199). Business-licensed — see LICENSE here.

Included by the core catalog only when ``SCHAKL_DEPLOYMENT=cloud`` (the same conditional
that mounts the routers), so a self-hosted permission matrix never shows a capability the
box does not have.
"""

from __future__ import annotations

from app.core.permissions.spec import PermissionSpec

CLOUD_PERMISSIONS: tuple[PermissionSpec, ...] = (
    # Issuing/revoking the service PIN opens the org's data to the platform operator —
    # admin-only, like the surfaces that hold credentials.
    PermissionSpec("settings.service_access.manage", group="settings", position=130),
)
