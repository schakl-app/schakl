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

    def narrowed_to(self, ceiling: PermissionSet) -> PermissionSet:
        """This set, with everything ``ceiling`` does not hold removed — never wider than either.

        What a **portal impersonation** actually runs as (#296 as revised by #266): the target's
        permissions, capped by the impersonator's. It replaces a ``covers`` *refusal*, and the
        security property is the same one stated more directly — the result is a subset of
        ``ceiling`` by construction, so entering a session can never hand the caller a capability
        they did not already have. The refusal only ever expressed that invariant indirectly, and
        it made every grant to the ``client`` role narrow the set of staff who could impersonate:
        #266 gave clients an invoice read and locked out every member without one.

        A scope is **degraded, not dropped**: a caller holding ``x:own`` against a target's
        ``x:any`` keeps ``x:own``, because they do hold *some* of it. Dropping it would hide a
        screen the caller can genuinely open on their own account, which is the opposite of the
        point. A wildcard ceiling changes nothing (an owner already holds everything); a wildcard
        *target* — which no real client role has — collapses to the ceiling itself.
        """
        if ceiling.wildcard:
            return self
        if self.wildcard:
            return ceiling
        kept: set[str] = set()
        for stored in self.granted:
            key, _, scope = stored.partition(":")
            scope = scope or None
            if ceiling.has(key, scope):
                kept.add(stored)
            elif scope == SCOPE_ANY and ceiling.has(key, SCOPE_OWN):
                kept.add(f"{key}:{SCOPE_OWN}")
        return PermissionSet(frozenset(kept))

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
