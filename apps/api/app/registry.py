"""Module registry (CLAUDE.md §3, §6).

Every domain module self-registers a :class:`ModuleDescriptor`. ``main.py`` mounts the routers
of enabled modules; the company detail view composes :class:`PanelSpec`s that modules attach to
an entity (the "attach to company" hub). The ``mcp_tools`` field is the Phase-4 seam — captured
now, served later. Modules never import each other's internals; they meet here.

A module also declares the **permissions** it introduces (issue #19), which is why core holds no
module permission list: adding a module ships its ``<module>.<resource>.<action>`` keys with it.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import APIRouter

    from app.core.email.kinds import EmailTemplateKind
    from app.core.impex.spec import ImpexDescriptor, ImpexExtension
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


#: What a report section is generated *for*: one client, one period, one comparison.
#: A period, not "the last 30 days" — the difference between a panel and a report.
@dataclass(frozen=True)
class ReportWindow:
    """The subject of one report run, handed to every section provider (issue #300)."""

    company_id: uuid.UUID
    start: date
    end: date
    #: The period this one is measured against — the same span a year earlier by default.
    #: ``None`` when the client has no comparable history, which a section must *say* rather
    #: than print as a row of zeros (the n8n workflow's silent "N/A" is what this avoids).
    compare_start: date | None
    compare_end: date | None
    #: The **document's** language, not the caller's UI locale. A Dutch agency reporting to a
    #: German client sends German from a Dutch screen (docs/INVOICING.md's rule for documents).
    locale: str = "nl"


#: ``async (ctx, window) -> dict | None``. ``None`` means "this module has nothing for this
#: client" — a client with no GA4 link simply has no traffic section, which is not an error and
#: must not print an empty table.
ReportSectionProvider = Callable[
    ["RequestContext", ReportWindow], Awaitable[dict[str, Any] | None]
]

#: Who a section is for. ``client`` prints in the document the agency's customer reads;
#: ``internal`` only in the marketeer's analysis. A section is never *both* by accident —
#: the split is what lets the client document ban the word "advies" while the internal one is
#: made of it.
AUDIENCE_CLIENT = "client"
AUDIENCE_INTERNAL = "internal"
AUDIENCE_BOTH = "both"


@dataclass(frozen=True)
class ReportSectionSpec:
    """A section a module contributes to a periodic report (issue #300).

    The panels pattern (below) applied to documents, and for the same reason: adding
    "zoekwoordposities" to every client's monthly report must be a change to the module that
    owns rankings, not an edit to the reporting module. Reporting composes what it is given
    and knows the name of no module.

    A section carries more than a panel because a document needs more: the period it covers,
    the comparison, a table shape the renderer can lay out, and — the piece that makes the
    narrative possible — a ``brief_key`` naming the i18n text that tells the model what this
    section is *about*. The tenant's tone says how to write; the brief says what to write
    about; the data says what is true.
    """

    key: str                      # unique, module-namespaced: "marketing.traffic_channels"
    title_key: str                # i18n key for the section heading
    provider: ReportSectionProvider
    #: i18n key of the default narrative brief handed to the model for this section.
    brief_key: str = ""
    audience: str = AUDIENCE_CLIENT
    #: The permission the *generating* caller must hold for this section to be gathered. A
    #: section is skipped, never 403'd: a report is assembled from whatever the generator may
    #: read, and a member without ad-spend access simply produces a report without it.
    requires_permission: str | None = None
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
    # Licensed module (issue #137): the entitlement sku a license must cover before a tenant
    # may enable this module. None = free. "Which modules are paid" lives here and in license
    # documents — never as module names hardcoded in gating logic.
    sku: str | None = None
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
    # Columns this module contributes to *another* module's import/export shape (issue #77) —
    # the same contribution model as `panels`, so a company import can carry its client's
    # contact person without companies importing contacts' internals (CLAUDE.md §6, §17).
    impex_extensions: list[ImpexExtension] = field(default_factory=list)
    # Actions this module contributes to the automation rule engine (issue #27).
    automation_actions: list[AutomationActionSpec] = field(default_factory=list)
    # Sections this module contributes to a periodic client report (issue #300) — the panels
    # contribution model, applied to documents. The reporting module composes these and names
    # no module; disabling the contributor removes its section from every future report, while
    # already-generated ones keep theirs (a report stores its own snapshot).
    report_sections: list[ReportSectionSpec] = field(default_factory=list)
    # Outgoing mails this module lets the tenant rewrite (Instellingen -> E-mail), the same
    # contribution model as `panels`: core declares the auth mails and holds no module list
    # (`app.core.email.kinds`). Keys are namespaced by the module and asserted at mount time.
    email_templates: list[EmailTemplateKind] = field(default_factory=list)


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

    def report_sections_for(
        self, audience: str, names: list[str]
    ) -> list[ReportSectionSpec]:
        """Every enabled module's report sections for one audience, ordered (issue #300).

        ``both`` matches either audience, so a section that reads the same to a client and to
        the marketeer (the traffic table) is declared once rather than twice.
        """
        sections = [
            spec
            for module in self.enabled(names)
            for spec in module.report_sections
            if spec.audience in (audience, AUDIENCE_BOTH)
        ]
        return sorted(sections, key=lambda s: (s.position, s.key))

    def report_section(self, key: str, names: list[str]) -> ReportSectionSpec | None:
        """One section by key — what "regenerate just this paragraph" resolves against."""
        for module in self.enabled(names):
            for spec in module.report_sections:
                if spec.key == key:
                    return spec
        return None

    def impex_extensions_for(self, entity_type: str, names: list[str]) -> list[ImpexExtension]:
        """Columns the enabled modules contribute to ``entity_type``'s import/export shape.

        Disabling the contributing module removes its columns — which is the point of routing
        this through the registry rather than letting the host descriptor name them.
        """
        extensions = [
            extension
            for module in self.enabled(names)
            for extension in module.impex_extensions
            if extension.entity_type == entity_type
        ]
        return sorted(extensions, key=lambda e: (e.position, e.module))


registry = ModuleRegistry()
