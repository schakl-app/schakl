"""``PermissionSpec`` — one capability, declared in code (issue #19).

A permission is never free text. Core declares its own capabilities here-adjacent
(:mod:`app.core.permissions.catalog`) and every module declares its own on its
:class:`~app.registry.ModuleDescriptor`, the same way it declares routers and panels
(CLAUDE.md §6). Core therefore holds no module permission list.

Naming is ``<module>.<resource>.<action>``. A spec may carry a **scope qualifier** when the
distinction between "mine" and "anyone's" is real: ``scopes=("own", "any")`` means the
permission is only ever *stored* suffixed (``time.entry.write:own``), never bare.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The only two scopes. ``own`` is the narrow grant, ``any`` the broad one, and ``any``
#: always satisfies a check for ``own`` (see :class:`~app.core.permissions.permset.PermissionSet`).
SCOPE_OWN = "own"
SCOPE_ANY = "any"
SCOPES: tuple[str, str] = (SCOPE_OWN, SCOPE_ANY)

#: The one permission string that is not a spec key: the owner role's blanket grant.
WILDCARD = "*"


@dataclass(frozen=True)
class PermissionSpec:
    """A capability a role can be granted.

    ``default_roles``  — system roles that get this permission at its **broadest** scope
                          (unsuffixed when unscoped, ``:any`` when scoped).
    ``default_own_roles`` — system roles that get it at ``:own`` only.

    Both feed the seeding of a fresh org *and* the startup reconciler that grants a later
    module's new permissions to existing orgs' system roles.
    """

    key: str
    scopes: tuple[str, ...] = ()
    label_key: str = ""
    group: str = ""
    position: int = 100
    default_roles: tuple[str, ...] = ("admin",)
    default_own_roles: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.scopes not in ((), SCOPES):
            raise ValueError(f"{self.key}: scopes must be () or {SCOPES}, got {self.scopes}")
        if not self.scopes and self.default_own_roles:
            raise ValueError(f"{self.key}: default_own_roles needs a scoped permission")

    @property
    def i18n_key(self) -> str:
        """Where the UI finds this permission's label (``messages/{en,nl}.json``)."""
        return self.label_key or f"permissions.{self.key}"

    @property
    def module(self) -> str:
        """The matrix group this renders under; defaults to the key's first segment."""
        return self.group or self.key.split(".", 1)[0]

    def scoped(self, scope: str | None) -> str:
        """The stored permission string for ``scope`` (``None`` → the broadest)."""
        if not self.scopes:
            return self.key
        return f"{self.key}:{scope or self.scopes[-1]}"

    def default_grants(self) -> dict[str, str]:
        """``{role_key: stored permission string}`` for the system roles this seeds into."""
        grants = {role: self.scoped(None) for role in self.default_roles}
        for role in self.default_own_roles:
            grants.setdefault(role, self.scoped(SCOPE_OWN))
        return grants
