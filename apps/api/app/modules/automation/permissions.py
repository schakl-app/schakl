"""Permissions the automation module contributes (issues #19, #27).

Rule authoring is effectively privileged — a rule can act as the org (create tasks, notify
people, call external webhooks) — so nothing here defaults to members: admins only (and the
owner's ``*``), which a tenant can widen per role.
"""

from __future__ import annotations

from app.core.permissions import PermissionSpec

AUTOMATION_PERMISSIONS: list[PermissionSpec] = [
    PermissionSpec("automation.rule.read", position=10),
    PermissionSpec("automation.rule.write", position=20),
    PermissionSpec("automation.run.read", position=30),
]
