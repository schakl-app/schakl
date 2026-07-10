"""``PermissionSet`` — the effective permissions of one membership, resolved once per request.

The subtlety: a **scoped** permission is only ever stored suffixed. A member never holds a bare
``time.entry.write``; they hold ``time.entry.write:own``. So a check with no scope — what a route
declares — must mean *"holds this at some scope"*, and ``:any`` must satisfy a check for ``:own``.
A naive ``key in granted`` would 403 every member on every scoped endpoint.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from app.core.permissions.spec import SCOPE_ANY, SCOPE_OWN, WILDCARD


@dataclass(frozen=True)
class PermissionSet:
    granted: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def of(cls, permissions: Iterable[str] | None) -> PermissionSet:
        return cls(frozenset(permissions or ()))

    @property
    def wildcard(self) -> bool:
        """Only the ``owner`` system role holds ``*``. ``is_superuser`` never implies it."""
        return WILDCARD in self.granted

    def has(self, key: str, scope: str | None = None) -> bool:
        if self.wildcard:
            return True
        granted = self.granted
        if key in granted:  # genuinely unscoped permissions, e.g. tasks.task.create
            return True
        if scope == SCOPE_ANY:
            return f"{key}:{SCOPE_ANY}" in granted
        # scope is None (a route's floor) or "own": a broad grant satisfies a narrow ask.
        return f"{key}:{SCOPE_OWN}" in granted or f"{key}:{SCOPE_ANY}" in granted

    def keys(self) -> list[str]:
        return sorted(self.granted)
