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

    def covers(self, other: PermissionSet) -> bool:
        """Does this set hold everything ``other`` holds, at least as broadly?

        The one question impersonation has to answer before it hands someone another account
        (#296): entering a login must never *gain* the impersonator a capability. Roles are
        tenant-editable, so "the target is only a client" is not a bound on what the client role
        was granted — this is. A wildcard holder covers everything; nothing but a wildcard
        covers a wildcard.
        """
        if self.wildcard:
            return True
        if other.wildcard:
            return False
        for stored in other.granted:
            key, _, scope = stored.partition(":")
            if not self.has(key, scope or None):
                return False
        return True

    def keys(self) -> list[str]:
        return sorted(self.granted)
