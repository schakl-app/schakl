"""What a ``/mcp/<section>`` URL offers — and why almost none of it is written down here.

``/mcp`` is every ``/api/v1`` operation: 623 tools, about two megabytes of ``tools/list``.
That is the right answer for a coding agent, which reads the list once. It is the wrong answer
for everything else, and the reason is not politeness — a chat client loads every tool into the
model's context on every turn, and a specialist agent given 623 tools picks worse than the same
agent given 45. A **section** is one URL that answers ``tools/list`` with less.

Three kinds, and the differences between them are the whole design:

* a **module section** (``/mcp/google-ads``) is *derived* from that module's own router prefix.
  Nothing lists its tools, because a hand-written list of one module's tools is a second copy of
  its router — and the copy is only ever wrong later, silently, in the direction of a tool the
  module ships and the section does not offer. A module that grows an endpoint tomorrow serves
  it here tomorrow.
* a **bundle** (``/mcp/infra``) names **modules, never tools**, for exactly the same reason. It
  exists because an agency job is not a module: "the domain register and what answers on it"
  spans seven of them, and no module boundary can express it. Naming modules is what keeps a
  bundle as self-maintaining as the sections it unions.
* a **curated** section (``/mcp/compact``) is the only one that names tools, and it may only
  exist where an *external* ceiling makes a module boundary useless — ChatGPT's 5,000 tokens for
  every tool's name, description and input schema together. A list is a specification, so it is
  pinned by a number in the tests rather than by intent (``test_mcp_compact_profile_fits_a_chat_
  client``): a name added without watching that budget fails weeks later in somebody else's
  settings screen, with an error nobody here ever sees.

**A section narrows a listing. It is not an authorization boundary.** A tool outside the
section still answers, still through ``require_context``, still capped by the calling key's
scopes. Saying otherwise would stand a second, weaker answer next to the one the API already
gives — and the weaker one would be the one a reader trusts, because it is the one printed on
the screen. What a credential may do is decided when it is minted (CLAUDE.md §12's read-first
rule) and re-decided on every request; what a URL *lists* is a context budget.

**Core routes belong to no section.** ``/settings``, ``/roles``, ``/api-keys``, ``/nav``,
``/prefs`` and the rest are the instance's own administration, and an agent doing a job does not
administer the instance. That is a rule rather than an oversight, so a core surface that turns
out to be worth offering gets added with a reason next to it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.config import settings
from app.registry import registry

#: The full surface, reached at ``/mcp`` itself. Not a section — the absence of one.
FULL = ""

KIND_MODULE = "module"
KIND_BUNDLE = "bundle"
KIND_CURATED = "curated"


@dataclass(frozen=True)
class Section:
    """One ``/mcp/<key>`` URL, resolved against the app that is actually running."""

    key: str
    kind: str
    #: i18n key for the human name. Resolved by the client — the API picks no locale (§17).
    label_key: str
    #: Tool names this section lists. Derived for module/bundle sections, literal for curated.
    tools: frozenset[str] = field(default_factory=frozenset)
    #: Registry module names, for the panel to explain what a bundle contains.
    modules: tuple[str, ...] = ()

    @property
    def path(self) -> str:
        return f"/mcp/{self.key}"


#: Bundles, as module names. Each answers a job somebody hires an agent to do; each is a union
#: of module sections and therefore inherits their derivation. A module that is not enabled on
#: this instance simply contributes nothing — a bundle is never a claim that a module exists.
#:
#: Deliberately few. Every bundle is a name somebody has to learn, and the per-module sections
#: below already cover the specialist case; a bundle earns its place only where the job it names
#: genuinely spans modules.
_BUNDLES: dict[str, tuple[str, ...]] = {
    # The agency's own day-to-day: who the clients are, what is being done for them, and where
    # the hours went. This is the general-purpose one — an automation or a custom agent that
    # works the business rather than one system inside it.
    "agent": ("companies", "contacts", "projects", "tasks", "time", "interactions"),
    # What we host, register and watch. Seven modules, because "is the client's site up and who
    # renews the name" was never one module's question.
    "infra": ("domains", "websites", "hosting", "cloudflare", "oxxa", "uptime", "wordpress"),
    # What is owed and what is collected.
    "finance": ("invoicing", "subscriptions", "mollie"),
    # What is being spent, what is measuring it, and what it returned. `google_analytics`
    # and `google_search_console` are members *and* keep their own sections: an agent asked
    # to work a client's Analytics wants seventeen tools and not the hundred this bundle unions.
    "growth": (
        "google_ads",
        "google_analytics",
        "google_search_console",
        "google_tag_manager",
        "marketing",
        "reporting",
    ),
}


#: The compact profile's tools, by the name ``_tool_names`` gives them.
#:
#: Chosen against one question — *what does somebody ask a chat assistant about an agency?* —
#: and then cut until the whole list fits ChatGPT's 5,000-token ceiling with room to spare.
#:
#: **Read-only, and that is a decision rather than an accident.** §12 already calls the surface
#: read-first; a chat client is where that matters most, because the tools a model may reach for
#: are the ones nobody explicitly asked it to call. The full surface at ``/mcp`` keeps every
#: write, gated by the calling key's scopes exactly as before.
#:
#: Picked for grounding first (a person says "AAZET", every other tool wants an id), then the
#: four questions an agency actually asks: what is running, what is owed, where did the hours
#: go, how are the campaigns doing.
_COMPACT_TOOLS = frozenset(
    {
        # Grounding: name → id, for everything below.
        "list_companies",
        "get_company",
        "list_contacts",
        # What is running.
        "list_projects",
        "my_open_tasks",
        "get_task",
        # What is owed.
        "list_invoices",
        "outstanding",
        "unbilled",
        # Where the hours went.
        "time_report",
        # What we host and renew.
        "list_domains",
        # How the campaigns are doing. ``list_google_ads_accounts`` is grounding again: the
        # rest take an ``account_id`` nobody types from memory.
        "list_google_ads_accounts",
        "google_ads_snapshot",
        "google_ads_search_terms",
    }
)

#: Curated sections, by key. The only place in this file that names a tool.
_CURATED: dict[str, frozenset[str]] = {"compact": _COMPACT_TOOLS}


def _under(path: str, base: str) -> bool:
    """Whether ``path`` lies under the route prefix ``base``, on a segment boundary.

    The boundary is the point: ``/api/v1/google-ads/...`` is not under ``/api/v1/google``, and a
    plain ``startswith`` says it is — which would fold every Google Ads tool into the Workspace
    module's section and leave nobody able to see why.
    """
    return path == base or path.startswith(f"{base}/")


def _module_prefixes() -> dict[str, str]:
    """Registry module name → the API path its router is mounted at.

    Read from the router rather than composed from the module name, because the two differ
    (``google_ads`` is served at ``/google-ads``) and only one of them is the URL.
    """
    prefixes: dict[str, str] = {}
    for module in registry.enabled(settings.enabled_modules):
        prefix = getattr(module.router, "prefix", None) if module.router else None
        if prefix:
            prefixes[module.name] = f"/api/v1{prefix}"
    return prefixes


def build_sections(tool_paths: dict[str, str]) -> dict[str, Section]:
    """Every section this instance serves, keyed by URL segment.

    ``tool_paths`` maps a tool name to the API path behind it — built once, where the tool names
    are (:func:`app.core.mcp.server._tool_names`), because that is the only place the mapping is
    known without asking FastMCP for internals it does not promise.

    A section resolving to no tools is dropped rather than served empty: on an instance where a
    module is switched off, its URL should read as "no such section", not as "the section is
    there and your agent has nothing to work with".
    """
    prefixes = _module_prefixes()
    sections: dict[str, Section] = {}

    for name, prefix in prefixes.items():
        tools = frozenset(tool for tool, path in tool_paths.items() if _under(path, prefix))
        if not tools:
            continue
        # The URL segment is the router's own prefix, so the section a caller types matches the
        # paths its tools actually hit — and `google-ads` never has to be spelled `google_ads`.
        key = prefix.rsplit("/", 1)[-1]
        # ``module.<name>.label`` is the key the web already resolves a module's display name
        # with (``$lib/core/registry.moduleLabel``), and it already falls back to the raw name
        # for a module this build does not know. Minting a second vocabulary here would put the
        # modules screen and this one one translation apart.
        sections[key] = Section(
            key=key,
            kind=KIND_MODULE,
            label_key=f"module.{name}.label",
            tools=tools,
            modules=(name,),
        )

    for key, members in _BUNDLES.items():
        present = tuple(name for name in members if name in prefixes)
        tools = frozenset(
            tool
            for tool, path in tool_paths.items()
            if any(_under(path, prefixes[name]) for name in present)
        )
        if not tools:
            continue
        sections[key] = Section(
            key=key,
            kind=KIND_BUNDLE,
            label_key=f"settings.api.section.{key}",
            tools=tools,
            modules=present,
        )

    for key, tools in _CURATED.items():
        # Intersected with what exists, so a renamed route degrades to a shorter list rather
        # than to a section advertising a tool no call could ever reach. The compact profile's
        # own test asserts the intersection is total — this is the safety net under it, not a
        # licence to let the list rot.
        present_tools = frozenset(tools & tool_paths.keys())
        if not present_tools:
            continue
        sections[key] = Section(
            key=key, kind=KIND_CURATED, label_key=f"settings.api.section.{key}", tools=present_tools
        )

    return sections


def assert_no_collisions(sections: dict[str, Section]) -> None:
    """A bundle or curated key that shadows a module's prefix is a build break.

    ``build_sections`` writes modules first, so a collision would silently *replace* a module
    section with a bundle of the same name — the module's tools would vanish from a URL that
    still resolves, which is the failure that takes longest to notice. Cheaper to refuse at
    import time, the way §6 refuses a duplicate module name.
    """
    module_keys = {key for key, section in sections.items() if section.kind == KIND_MODULE}
    clash = (set(_BUNDLES) | set(_CURATED)) & module_keys
    if clash:
        raise ValueError(
            f"MCP section key(s) {sorted(clash)} collide with a module's route prefix — "
            "rename the bundle, or the module's section is unreachable"
        )


#: What :func:`resolve_segment` returns for ``/mcp`` itself — the full surface, no section.
WHOLE_SURFACE = object()


def resolve_segment(
    sections: dict[str, Section], relative: str
) -> Section | object | None:
    """The section a path under the ``/mcp`` mount selects.

    Three answers, because there are three cases and collapsing any two of them loses something:
    :data:`WHOLE_SURFACE` for ``/mcp`` itself, the :class:`Section` for a known segment, and
    ``None`` for a segment naming nothing.

    That last one is refused rather than quietly widened. Falling back to the whole surface is
    tempting — a connector that adds is friendlier than one that does not — but it is the wrong
    friendliness: somebody who typed ``/mcp/google-add`` asked for 45 tools and would receive
    623, so the client either chokes on a budget or picks worse from a list nobody meant to give
    it, and nothing anywhere says why. A refusal that names the sections is recoverable in one
    read; a surface that is silently 14× too big is not recoverable at all, because it looks
    like it worked.
    """
    segment = relative.strip("/")
    if not segment:
        return WHOLE_SURFACE
    return sections.get(segment)


def describe(sections: dict[str, Section]) -> list[dict[str, Any]]:
    """The section list as the panel renders it — ordered so the answer is stable per deploy.

    Curated first (the smallest and the one a chat client needs), then bundles, then the long
    tail of module sections alphabetically.
    """
    order = {KIND_CURATED: 0, KIND_BUNDLE: 1, KIND_MODULE: 2}
    return [
        {
            "key": section.key,
            "kind": section.kind,
            "label_key": section.label_key,
            "path": section.path,
            "tool_count": len(section.tools),
            "modules": list(section.modules),
        }
        for section in sorted(
            sections.values(), key=lambda s: (order[s.kind], -len(s.tools), s.key)
        )
    ]
