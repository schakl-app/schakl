"""Module registry (CLAUDE.md §3, §6).

Every domain module self-registers a :class:`ModuleDescriptor`. ``main.py`` mounts the routers
of enabled modules; the company detail view composes :class:`PanelSpec`s that modules attach to
an entity (the "attach to company" hub). The ``mcp_tools`` field is the Phase-4 seam — captured
now, served later. Modules never import each other's internals; they meet here.

A module also declares the **permissions** it introduces (issue #19), which is why core holds no
module permission list: adding a module ships its ``<module>.<resource>.<action>`` keys with it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import uuid

    from fastapi import APIRouter

    from app.core.permissions.spec import PermissionSpec
    from app.core.tenancy import RequestContext

# A panel provider fetches this module's data for one target entity instance.
PanelProvider = Callable[["RequestContext", "uuid.UUID"], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class PanelSpec:
    """A panel a module contributes to a host entity's detail view (e.g. a company)."""

    key: str                      # unique panel key, e.g. "companies.details"
    entity_type: str              # host entity it attaches to, e.g. "company"
    title_key: str                # i18n key for the panel title
    provider: PanelProvider       # async (ctx, target_id) -> data dict
    position: int = 100


@dataclass
class ModuleDescriptor:
    name: str
    router: APIRouter | None = None
    i18n_namespace: str | None = None
    panels: list[PanelSpec] = field(default_factory=list)
    # The capabilities this module introduces (issue #19). Aggregated into the permission
    # catalog by ``app.core.permissions.catalog.all_permissions``.
    permissions: list[PermissionSpec] = field(default_factory=list)
    # Phase-4 MCP seam: opaque tool specs, not served in P0.
    mcp_tools: list[Any] = field(default_factory=list)
    # ARQ cron job specs; the worker collects these from enabled modules.
    cron_jobs: list[Any] = field(default_factory=list)


class ModuleRegistry:
    def __init__(self) -> None:
        self._modules: dict[str, ModuleDescriptor] = {}

    def register(self, module: ModuleDescriptor) -> ModuleDescriptor:
        if module.name in self._modules:
            raise ValueError(f"Module '{module.name}' is already registered")
        self._modules[module.name] = module
        return module

    def get(self, name: str) -> ModuleDescriptor | None:
        return self._modules.get(name)

    def all(self) -> list[ModuleDescriptor]:
        return list(self._modules.values())

    def enabled(self, names: list[str]) -> list[ModuleDescriptor]:
        """Modules whose name is in ``names``, preserving registration order."""
        allowed = set(names)
        return [m for m in self._modules.values() if m.name in allowed]

    def panels_for(self, entity_type: str, names: list[str]) -> list[PanelSpec]:
        """All panels attached to ``entity_type`` by the given enabled modules, ordered."""
        panels: list[PanelSpec] = []
        for module in self.enabled(names):
            panels.extend(p for p in module.panels if p.entity_type == entity_type)
        return sorted(panels, key=lambda p: (p.position, p.key))


registry = ModuleRegistry()
