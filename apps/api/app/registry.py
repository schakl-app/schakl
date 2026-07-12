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

    from app.core.impex.spec import ImpexDescriptor
    from app.core.permissions.spec import PermissionSpec
    from app.core.tenancy import RequestContext

# A panel provider fetches this module's data for one target entity instance.
PanelProvider = Callable[["RequestContext", "uuid.UUID"], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class AutomationActionSpec:
    """An action a module contributes to the automation rule engine (issue #27).

    ``handler`` is an async callable ``(action_ctx, config) -> dict`` — the concrete
    ``ActionContext`` lives in ``app.modules.automation.actions`` (kept opaque here so
    contributing a spec never requires the automation module to be importable). The v1 set
    ships on the automation module's own descriptor; other modules add theirs the same way
    they add panels, so core holds no action list.
    """

    key: str                      # e.g. "task.create" — unique across modules
    handler: Any                  # async (ActionContext, config: dict) -> dict (step result)
    title_key: str = ""           # i18n key for the editor; default automation.action.<key>
    position: int = 100


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
    # One-off ARQ job functions the API may enqueue by name (app.core.jobs.enqueue); the
    # worker registers these alongside its cron jobs. Names must be globally unique.
    worker_functions: list[Any] = field(default_factory=list)
    # CSV import/export descriptors (issue #77): the entities this module opts into the core
    # impex engine. ``app.core.impex.router`` mounts one export + one import route per entry,
    # each declaring that entity's own read/write permission — core owns the mechanics,
    # modules only describe shape (the custom-fields/panels pattern, CLAUDE.md §13/§6).
    impex: list[ImpexDescriptor] = field(default_factory=list)
    # Actions this module contributes to the automation rule engine (issue #27).
    automation_actions: list[AutomationActionSpec] = field(default_factory=list)


class ModuleRegistry:
    def __init__(self) -> None:
        self._modules: dict[str, ModuleDescriptor] = {}
        # Panels core contributes to *every* host entity, regardless of which modules are
        # enabled — the activity trail is a core capability (issue #67), not a module.
        self._core_panels: list[PanelSpec] = []

    def register_core_panel(self, panel: PanelSpec) -> None:
        self._core_panels.append(panel)

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
        """All panels attached to ``entity_type``, ordered — core's plus the enabled modules'."""
        panels: list[PanelSpec] = [p for p in self._core_panels if p.entity_type == entity_type]
        for module in self.enabled(names):
            panels.extend(p for p in module.panels if p.entity_type == entity_type)
        return sorted(panels, key=lambda p: (p.position, p.key))


registry = ModuleRegistry()
