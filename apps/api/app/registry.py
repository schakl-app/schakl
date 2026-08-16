"""Module registry (CLAUDE.md §3, §6).

Every domain module self-registers a :class:`ModuleDescriptor`. ``main.py`` mounts the routers
of enabled modules; the company detail view composes :class:`PanelSpec`s that modules attach to
an entity (the "attach to company" hub). The ``mcp_tools`` field is the Phase-4 seam — captured
now, served later. Modules never import each other's internals; they meet here.

A module also declares the **permissions** it introduces (issue #19), which is why core holds no
module permission list: adding a module ships its ``<module>.<resource>.<action>`` keys with it.

**Modules and integrations are the same machinery and different things** (CLAUDE.md §6a). A
module is a domain capability schakl provides; an integration holds a credential for somebody
else's service and mirrors state that lives there. They register the same descriptor, mount the
same way and are enabled in the same list — :data:`ModuleDescriptor.kind` says which it is, and
:data:`ModuleDescriptor.requires` names the modules an integration has nowhere to put its data
without. The packages differ (``app.modules`` / ``app.integrations``) so the tree says it too;
:func:`module_package` is the one place that knows both roots.
"""

from __future__ import annotations

import importlib.util
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import APIRouter

    from app.core.bulk.spec import BulkDescriptor
    from app.core.email.kinds import EmailTemplateKind
    from app.core.impex.spec import ImpexDescriptor, ImpexExtension
    from app.core.permissions.spec import PermissionSpec
    from app.core.tenancy import RequestContext

#: The two package roots a self-registering descriptor may live in (CLAUDE.md §6a). Order is the
#: lookup order, so a name present in both resolves the way it did before the split.
MODULE_ROOTS = ("app.modules", "app.integrations")


def module_package(name: str) -> str | None:
    """The dotted package path of module/integration ``name``, or ``None`` if this build has none.

    Every dynamic load goes through here — the app's mount, the worker's, the model importer's,
    the permission catalog's — because the alternative is five copies of the two-root lookup and
    a sixth that was never updated. It uses ``find_spec``, which locates a package without
    executing it: a module whose own imports are broken must still raise from the *import*, not
    be quietly reported as absent (an instance booting with a module missing from the registry
    404s every route it owns, with nothing having said why).
    """
    for root in MODULE_ROOTS:
        try:
            if importlib.util.find_spec(f"{root}.{name}") is not None:
                return f"{root}.{name}"
        except ModuleNotFoundError:
            continue
    return None


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
    #: i18n key naming **where this section's data comes from** — "Google Analytics", "Search
    #: Console of SE Ranking" (#373). Choosing what goes in a client's document is a decision
    #: about sources, and a picker that lists nine section names while saying nothing about what
    #: feeds them cannot be reasoned about: an agency switching one off wants to know whether it
    #: is empty because the client has no social traffic or because nobody linked the property.
    source_key: str = ""
    audience: str = AUDIENCE_CLIENT
    #: The permission the *generating* caller must hold for this section to be gathered. A
    #: section is skipped, never 403'd: a report is assembled from whatever the generator may
    #: read, and a member without ad-spend access simply produces a report without it.
    requires_permission: str | None = None
    position: int = 100


#: A panel's weight on the page it composes into (#364).
#:
#: Only the module knows whether its card is a *working surface* — something the reader acts on
#: today (open tasks, this week's hours, the contactmomenten stream) — or a **register**: correct,
#: occasionally consulted, never news (the domain list, the website list, the invoice ledger). The
#: page cannot tell them apart from a ``position`` integer, so it laid every card out at the same
#: width and the same weight and had no foreground at all.
PROMINENCE_PRIMARY = "primary"
PROMINENCE_REGISTER = "register"

#: How wide a panel wants to be in the hub's two-column desktop grid (#364). A two-word row laid
#: out across 1150 px is not a layout; ``half`` is where such a row belongs.
SIZE_FULL = "full"
SIZE_HALF = "half"

#: Does this panel's payload amount to "nothing has happened yet"? (#364)
#:
#: A module with nothing to show does not earn a heading, a border and 100 px — it earns a chip in
#: one "nog niets vastgelegd" strip. Only the module can read its own payload, so only the module
#: can answer; a panel that declares no predicate is never absorbed.
PanelEmptyCheck = Callable[[dict[str, Any]], bool]


@dataclass(frozen=True)
class PanelSpec:
    """A panel a module contributes to a host entity's detail view (e.g. a company)."""

    key: str                      # unique panel key, e.g. "companies.details"
    entity_type: str              # host entity it attaches to, e.g. "company"
    title_key: str                # i18n key for the panel title
    provider: PanelProvider       # async (ctx, target_id) -> data dict
    position: int = 100
    #: The permission the viewer must hold before this panel's provider is **called** (#365).
    #:
    #: Mirrors ``EntityPanelSpec.requiresPermission`` on the web registry, which closed exactly
    #: this hole for the contact/project/task pages while the company hub — the page the rule was
    #: written about — kept composing thirteen providers behind one ``companies.company.read``.
    #: A check that still runs the query saves no round trip and leaks the answer anyway, so the
    #: filter lives in :meth:`Registry.panels_for` rather than inside each provider.
    #:
    #: ``None`` is a *declaration*, not an omission: it means "any member who may open this
    #: record may read this panel", and ``explicit_public`` says why. A panel declaring neither
    #: is a build break (``tests/test_company_panels_permissions.py``), exactly as a route
    #: declaring neither ``require_permission`` nor ``no_permission_required`` is.
    requires_permission: str | None = None
    #: The scope that permission is required at — ``None`` means the floor ("holds it at some
    #: scope"), which is right for almost every panel.
    requires_scope: str | None = None
    #: Why this panel needs no permission. Required whenever ``requires_permission`` is ``None``.
    explicit_public: str | None = None
    #: Working surface vs register (#364) — see :data:`PROMINENCE_PRIMARY`.
    prominence: str = PROMINENCE_REGISTER
    #: Preferred width in the hub's desktop grid (#364) — see :data:`SIZE_FULL`.
    size: str = SIZE_FULL
    #: ``(data) -> bool``: this payload is "nothing yet" (#364). See :data:`PanelEmptyCheck`.
    empty_when: PanelEmptyCheck | None = None


#: A vital sign a module contributes to a host entity's header (#364).
#:
#: The panels answer "what is on file"; these answer *"are we all right with this client"* —
#: openstaand bedrag, uren deze maand, open taken waarvan n over tijd, laatste contactmoment,
#: eerstvolgende verlenging. Every one of those was already derivable from a panel the reader had
#: to scroll to and add up by eye, which is the same seam applied one level up: the module owns
#: the number, core owns the strip, and the company page gains no per-module code.
#:
#: A provider returns a list because one module may own more than one sign (tasks: open *and*
#: overdue), and an empty list because "this client has no invoices" is not a tile — a vital sign
#: that is always on screen saying zero is chrome, and #364's whole complaint is chrome.
SummaryProvider = Callable[
    ["RequestContext", "uuid.UUID"], Awaitable[list["SummaryTile"]]
]


@dataclass(frozen=True)
class SummaryTile:
    """One number in a host entity's vital-signs strip (#364)."""

    key: str                      # module-namespaced, e.g. "invoicing.outstanding"
    label_key: str                # i18n key for the tile's caption
    #: The raw value: a decimal string, an integer, an ISO date, or free text. Raw on purpose —
    #: money, dates and hours are formatted in the **reader's** locale (§8), and a number
    #: rendered server-side is a currency symbol and a decimal comma decided for someone else.
    value: str
    #: How to read ``value``: ``money`` | ``number`` | ``hours`` | ``date`` | ``text``. The
    #: module owns the *units*; the browser owns the punctuation.
    format: str = "number"
    #: ISO currency for ``format="money"``; ignored otherwise.
    currency: str | None = None
    #: How it reads: neutral / good / warn / bad. A *tone*, not a colour — dark mode and the
    #: tenant's brand both decide colours, and brand gold cannot carry state.
    tone: str = "neutral"
    #: One short line under the number ("3 over tijd", "op 12 sep") — an i18n key + params, so
    #: the sentence is built in the reader's locale, never assembled server-side.
    hint_key: str | None = None
    hint_params: dict[str, Any] = field(default_factory=dict)
    #: Where the number opens (docs/UX.md principle 7, "every number opens") — an in-app path
    #: the module owns, the same shape ``interactions`` already emits as ``deep_link``. ``None``
    #: for a sign with nothing behind it.
    href: str | None = None
    position: int = 100


@dataclass(frozen=True)
class SummarySpec:
    """A module's contribution to a host entity's vital-signs strip (#364)."""

    key: str                      # unique, module-namespaced: "invoicing.company"
    entity_type: str              # host entity it attaches to, e.g. "company"
    provider: SummaryProvider
    #: Same contract as :class:`PanelSpec` — the provider is never *called* without it (#365).
    requires_permission: str | None = None
    requires_scope: str | None = None
    explicit_public: str | None = None
    position: int = 100


#: A domain capability schakl itself provides: it owns entities, screens and a data model, and it
#: is worth having with every third-party account cancelled.
KIND_MODULE = "module"
#: A conversation with somebody else's service. It holds a **credential**, and what it stores is a
#: mirror of — or a pointer into — state that lives outside. Cancel the vendor and a module is
#: poorer; an integration is gone. That is the whole test (CLAUDE.md §6a).
KIND_INTEGRATION = "integration"


@dataclass
class ModuleDescriptor:
    name: str
    router: APIRouter | None = None
    i18n_namespace: str | None = None
    #: :data:`KIND_MODULE` or :data:`KIND_INTEGRATION`. The default is ``module`` because that is
    #: what most of them are and because a wrong default should be the harmless one: an
    #: integration mislabelled a module is a screen in the wrong group, while a module mislabelled
    #: an integration would claim a credential it does not have.
    kind: str = KIND_MODULE
    #: Modules this one has **nowhere to put its data** without — a hard requirement, checked on
    #: the enable path in both directions (``app.core.entitlements.service``).
    #:
    #: Deliberately not "modules this one is nicer with". Google Workspace enriches
    #: ``interactions`` and ``tasks`` and requires neither: with both off it is still a Drive
    #: browser and a calendar mirror. Cloudflare without ``domains`` has no row to hang a zone
    #: on, so it declares it. The failure direction matters — an over-declared requirement makes
    #: a tenant switch on a module they did not want in order to use one they did.
    requires: tuple[str, ...] = ()
    # Licensed module (issue #137): the entitlement sku a license must cover before a tenant
    # may enable this module. None = free. "Which modules are paid" lives here and in license
    # documents — never as module names hardcoded in gating logic.
    sku: str | None = None
    panels: list[PanelSpec] = field(default_factory=list)
    # The vital signs this module contributes to a host entity's header (#364) — the panels
    # seam one level up, so the client page's foreground needs no per-module code either.
    summaries: list[SummarySpec] = field(default_factory=list)
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
    # Bulk edit/delete descriptors: the entities this module opts into acting on a whole
    # selection at once. ``app.core.bulk.router`` mounts one update + one delete route per
    # entry, each declaring that entity's own write/delete permission — and each descriptor
    # borrows the module's own import shape, so a bulk edit is the form's write path repeated,
    # never a second one (CLAUDE.md §17's pattern, applied to a selection instead of a file).
    bulk: list[BulkDescriptor] = field(default_factory=list)
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

    def kinds(self) -> dict[str, str]:
        """name → :data:`KIND_MODULE` / :data:`KIND_INTEGRATION` for everything registered.

        One dict rather than two lists because every caller wants to *classify* a name it already
        has (a settings screen, the modules payload, the first-run wizard), and two lists make
        "which is this?" a pair of membership tests that can disagree.
        """
        return {m.name: m.kind for m in self._modules.values()}

    def requirements(self) -> dict[str, list[str]]:
        """name → the modules it cannot run without. Only entries that have any."""
        return {m.name: list(m.requires) for m in self._modules.values() if m.requires}

    def unmet_requirements(self, names: list[str]) -> dict[str, list[str]]:
        """``{name: [missing modules]}`` for everything in ``names`` whose needs are not met.

        A requirement naming a module this build does not ship at all is *not* reported: an
        instance may mount a subset (``SCHAKL_ENABLED_MODULES``), and refusing to enable
        Cloudflare on a box that has no ``domains`` package to enable would be a dead end rather
        than a fixable one.
        """
        chosen = set(names)
        out: dict[str, list[str]] = {}
        for module in self.enabled(names):
            missing = [
                need
                for need in module.requires
                if need not in chosen and need in self._modules
            ]
            if missing:
                out[module.name] = missing
        return out

    def dependants(self, name: str, names: list[str]) -> list[str]:
        """Everything in ``names`` that requires ``name`` — the other direction of the same rule.

        Without it, switching a module off is the way round the enable gate: turn ``domains``
        off and Cloudflare stays enabled with nothing to attach a zone to.
        """
        return sorted(m.name for m in self.enabled(names) if name in m.requires)

    def panels_for(
        self,
        entity_type: str,
        names: list[str],
        can: Callable[[str, str | None], bool] | None = None,
    ) -> list[PanelSpec]:
        """The panels attached to ``entity_type`` **this viewer may read**, ordered (#365).

        ``can`` is ``ctx.can`` — required in spirit, optional in signature only because the AI
        digest gathers facts for a caller it has already narrowed itself. Pass it and a panel the
        viewer may not read is never *called*: a permission check that still runs the query saves
        no round trip and answers the question anyway.
        """
        panels: list[PanelSpec] = [p for p in self._core_panels if p.entity_type == entity_type]
        for module in self.enabled(names):
            panels.extend(p for p in module.panels if p.entity_type == entity_type)
        if can is not None:
            panels = [
                p
                for p in panels
                if p.requires_permission is None
                or can(p.requires_permission, p.requires_scope)
            ]
        return sorted(panels, key=lambda p: (p.position, p.key))

    def summaries_for(
        self,
        entity_type: str,
        names: list[str],
        can: Callable[[str, str | None], bool] | None = None,
    ) -> list[SummarySpec]:
        """The vital signs attached to ``entity_type`` this viewer may read, ordered (#364)."""
        specs: list[SummarySpec] = [
            spec
            for module in self.enabled(names)
            for spec in module.summaries
            if spec.entity_type == entity_type
        ]
        if can is not None:
            specs = [
                s
                for s in specs
                if s.requires_permission is None
                or can(s.requires_permission, s.requires_scope)
            ]
        return sorted(specs, key=lambda s: (s.position, s.key))

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
