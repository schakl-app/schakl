"""Request/response shapes for google_tag_manager. Business-licensed — see LICENSE.

Every name is prefixed ``Gtm``: a generic Pydantic schema name makes FastAPI qualify *both*
modules' components in the OpenAPI document, which silently rewrites somebody else's generated
client.

Two shapes are worth explaining, because the choice went the other way for each.

**Triggers are written in the recipe's vocabulary, tags in GTM's.** A trigger is where the vendor
vocabulary bites hardest — six trigger types, a typed singleton per option, and a *Check
Validation* flag that is only legal alongside a filter — and it is also where the vocabulary is
short enough to model completely. A tag is the opposite: the legal parameter keys are decided by
the tag *template*, of which there are hundreds, so a modelled tag would be a permanent lie about
what GTM accepts. So the tag write is the parameter array itself, and GTM's own validator judges
it (§17's rule that a refusal names a parameter, applied to somebody else's validator).

**A read is a projection, not a passthrough.** ``fingerprint`` rides every read because a write
needs it — that is GTM's optimistic concurrency and the only thing standing between a schakl edit
and a client's marketeer's edit disappearing.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# --------------------------------------------------------------------------- settings
class GtmSettingsRead(BaseModel):
    writes_enabled: bool
    own_workspace: bool
    workspace_name: str


class GtmSettingsWrite(BaseModel):
    writes_enabled: bool | None = None
    own_workspace: bool | None = None
    workspace_name: str | None = Field(default=None, max_length=120)


# --------------------------------------------------------------------------- containers
class GtmContainerRead(BaseModel):
    #: schakl's own id — the one every route below takes in its path. Google's numeric ids ride
    #: alongside under ``gtm_``-prefixed names, because a payload carrying two things both called
    #: ``container_id`` is a payload an agent picks the wrong one out of.
    id: uuid.UUID
    gtm_account_id: str
    gtm_container_id: str
    public_id: str
    path: str
    company_id: uuid.UUID | None
    company_name: str | None = None
    website_id: uuid.UUID | None
    connection_id: uuid.UUID | None
    name: str
    usage_context: list[str]
    domain_names: list[str]
    tagging_server_urls: list[str]
    live_version_id: str | None
    live_version_name: str | None
    tag_count: int
    trigger_count: int
    variable_count: int
    workspace_changes: int
    observed_at: datetime | None
    active: bool
    #: Tenant prose (#442): what this container is for the client, and what the tracking is
    #: supposed to prove. Empty strings when nobody has written them.
    summary: str = ""
    goal: str = ""
    status: str
    last_error: str | None
    last_verified_at: datetime | None
    last_synced_at: datetime | None
    #: Deep link into Tag Manager itself. Built here rather than in the browser so one rule
    #: produces it — the ⧉ on the panel, the detail screen and an MCP answer all agree.
    tag_manager_url: str


class GtmContainerCreate(BaseModel):
    """Link a container, named either the way Google addresses it or the way a human reads it.

    ``public_id`` (``GTM-NPGFR9W9``) is what is on the client's website and in the e-mail their
    developer sent; the numeric pair is what the API uses. Accepting only the second would make
    every link start with a lookup somebody has to do by hand, so ``containers:lookup`` does it.
    """

    gtm_account_id: str | None = Field(default=None, max_length=32)
    gtm_container_id: str | None = Field(default=None, max_length=32)
    public_id: str | None = Field(default=None, max_length=32)
    company_id: uuid.UUID | None = None
    website_id: uuid.UUID | None = None


class GtmContainerUpdate(BaseModel):
    """Only the fields schakl *decided*. What Google said is refreshed by verify, never typed.

    §18 semantics throughout: a field left out of the payload is left alone, and for the two
    prose fields an explicit ``null`` clears (they store ``""``, never NULL) — the Ads policy
    write's shape (``GoogleAdsPolicyWrite``).
    """

    company_id: uuid.UUID | None = None
    website_id: uuid.UUID | None = None
    active: bool | None = None
    summary: str | None = Field(default=None, max_length=8_000)
    goal: str | None = Field(default=None, max_length=8_000)


class GtmAvailableContainer(BaseModel):
    """One pickable container, as the live picker offers it."""

    gtm_account_id: str
    account_name: str
    gtm_container_id: str
    public_id: str
    name: str
    path: str
    usage_context: list[str]
    already_linked: bool = False


class GtmPickerRead(BaseModel):
    containers: list[GtmAvailableContainer]
    #: i18n keys for anything that limited the answer — a cap hit, an account that refused.
    warnings: list[str] = Field(default_factory=list)
    #: What the search was, echoed so a late answer can be discarded against a newer keystroke.
    query: str = ""
    #: Every Tag Manager account this grant reaches. Tag Manager's quota is per user per minute,
    #: so opening all of them is not affordable past a handful — and a result that cannot say
    #: "8 of 44" reads as "you are not in those accounts", which is a different and wrong fact.
    accounts_total: int = 0
    #: How many of them this search actually opened.
    accounts_read: int = 0


# --------------------------------------------------------------------------- workspaces
class GtmWorkspaceRead(BaseModel):
    workspace_id: str
    name: str
    description: str | None = None
    path: str
    fingerprint: str | None = None


class GtmWorkspaceStatusRead(BaseModel):
    """What is staged in a workspace and not live, plus anything that will not merge cleanly."""

    workspace_id: str
    changes: int
    #: ``[{kind, change, name}]`` — one line per staged change, in the reader's own words.
    entries: list[dict[str, Any]] = Field(default_factory=list)
    merge_conflicts: int = 0


# --------------------------------------------------------------------------- resources
class GtmParameter(BaseModel):
    """One GTM ``Parameter``. Recursive: ``list`` and ``map`` hold more of them.

    The field names carry aliases because ``list`` and ``map`` are builtins; every serialisation
    to Google goes ``by_alias=True``, so what leaves is Google's own spelling.
    """

    model_config = ConfigDict(populate_by_name=True)

    type: str = "template"
    key: str | None = None
    value: str | None = None
    list_items: list[GtmParameter] | None = Field(default=None, alias="list")
    map_items: list[GtmParameter] | None = Field(default=None, alias="map")


class GtmTagRead(BaseModel):
    tag_id: str
    name: str
    type: str
    paused: bool = False
    notes: str | None = None
    firing_trigger_id: list[str] = Field(default_factory=list)
    blocking_trigger_id: list[str] = Field(default_factory=list)
    parameter: list[dict[str, Any]] = Field(default_factory=list)
    #: Required by any later update — see the module docstring.
    fingerprint: str | None = None
    path: str = ""
    tag_manager_url: str | None = None


class GtmTagWrite(BaseModel):
    """The escape hatch, and the surface an agent uses most.

    ``type`` and ``parameter`` are GTM's own; nothing here validates them, because the tag
    template does and its refusal names the field. What *is* validated is the shape of the
    envelope and the fact that this container is one this caller may write to.
    """

    name: str = Field(min_length=1, max_length=255)
    type: str = Field(min_length=1, max_length=120)
    parameter: list[GtmParameter] = Field(default_factory=list)
    firing_trigger_id: list[str] = Field(default_factory=list)
    blocking_trigger_id: list[str] = Field(default_factory=list)
    notes: str | None = Field(default=None, max_length=1000)
    paused: bool | None = None


class GtmTagUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    parameter: list[GtmParameter] | None = None
    firing_trigger_id: list[str] | None = None
    blocking_trigger_id: list[str] | None = None
    notes: str | None = Field(default=None, max_length=1000)
    paused: bool | None = None


class GtmTriggerRead(BaseModel):
    trigger_id: str
    name: str
    type: str
    notes: str | None = None
    fingerprint: str | None = None
    path: str = ""


class GtmTriggerWrite(BaseModel):
    """The recipe's vocabulary, not GTM's — see the module docstring for why this one differs."""

    name: str = Field(min_length=1, max_length=255)
    #: ``page_view`` | ``form_submit`` | ``link_click`` | ``element_click`` |
    #: ``element_visibility`` | ``custom_event``.
    kind: str
    #: Narrow any kind to pages whose address contains this.
    url_contains: str | None = Field(default=None, max_length=500)
    #: Required for ``custom_event``: the ``dataLayer`` event to listen for.
    event_name: str | None = Field(default=None, max_length=255)
    #: Required for ``element_click`` and ``element_visibility``: a CSS selector.
    selector: str | None = Field(default=None, max_length=500)
    #: How much of the element must be on screen for ``element_visibility``. Nullable rather than
    #: defaulted, so the generated client makes it optional and the default lives in exactly one
    #: place (:mod:`app.integrations.google_tag_manager.recipes`) instead of being restated by
    #: every caller that does not care.
    visible_percent: int | None = Field(default=None, ge=1, le=100)


class GtmVariableRead(BaseModel):
    variable_id: str
    name: str
    type: str
    notes: str | None = None
    parameter: list[dict[str, Any]] = Field(default_factory=list)
    fingerprint: str | None = None
    path: str = ""


class GtmWorkspaceContentsRead(BaseModel):
    """One workspace, whole: what is in it and what of that is staged.

    The shape a *screen* reads. Its four per-resource siblings each resolve the workspace for
    themselves — which means listing the container's workspaces — so asking for all four cost
    eight Google requests where this costs five, on a provider whose quota is per user per minute.
    The siblings stay for the caller who wants one of them (an agent asking only for tags).
    """

    #: Empty when the container has no workspace at all — an empty page, never an error, and
    #: never a workspace brought into existence by somebody opening a screen.
    workspace_id: str
    status: GtmWorkspaceStatusRead | None = None
    tags: list[GtmTagRead] = Field(default_factory=list)
    triggers: list[GtmTriggerRead] = Field(default_factory=list)
    variables: list[GtmVariableRead] = Field(default_factory=list)


class GtmVariableWrite(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    type: str = Field(min_length=1, max_length=120)
    parameter: list[GtmParameter] = Field(default_factory=list)
    notes: str | None = Field(default=None, max_length=1000)


# --------------------------------------------------------------------------- versions
class GtmVersionRead(BaseModel):
    version_id: str
    name: str
    deleted: bool = False
    live: bool = False
    num_tags: int = 0
    num_triggers: int = 0
    num_variables: int = 0
    path: str = ""


class GtmVersionCreate(BaseModel):
    name: str = Field(default="", max_length=255)
    notes: str = Field(default="", max_length=1000)
    #: Which workspace to freeze. Absent means the one schakl writes in (Instellingen → Tag
    #: Manager), which is the only one this integration can vouch for the contents of.
    workspace_id: str | None = Field(default=None, max_length=32)


class GtmVersionCreated(BaseModel):
    version_id: str | None
    name: str
    compiler_error: bool = False
    #: True when the workspace had nothing staged — GTM answers 200 with no version, which is
    #: not a failure and is emphatically not a version anybody can publish.
    empty: bool = False
    sync_conflicts: int = 0


class GtmPublishResult(BaseModel):
    version_id: str
    name: str
    compiler_error: bool = False
    #: What the container row now says is live, so the caller need not re-read it.
    live_version_id: str | None = None


class GtmSnippetRead(BaseModel):
    """The install snippet, for the developer who has to put it on the site."""

    public_id: str
    snippet: str


# --------------------------------------------------------------------------- conversions
class GtmConversionRead(BaseModel):
    id: uuid.UUID
    container_id: uuid.UUID
    name: str
    key: str
    kind: str
    status: str
    config: dict[str, Any]
    workspace_id: str | None
    trigger_id: str | None
    tag_id: str | None
    published_version_id: str | None
    last_error: str | None
    observed_at: datetime | None
    created_by_name: str
    created_at: datetime


class GtmConversionCreate(BaseModel):
    """Set up one conversion: the trigger, the tag, and the record that they belong together.

    It lands in a workspace and is live for nobody until a version is published — which is a
    separate permission, on purpose.
    """

    name: str = Field(min_length=1, max_length=255)
    #: ``ga4_event`` | ``ads_conversion``.
    kind: str
    trigger: GtmTriggerWrite | None = None
    #: Reuse a trigger that already exists instead of making a second one that fires identically.
    trigger_id: str | None = Field(default=None, max_length=32)

    # -- ga4_event ---------------------------------------------------------------------------- #
    event_name: str | None = Field(default=None, max_length=255)
    #: ``G-XXXXXXX``. Never guessed — see :mod:`app.integrations.google_tag_manager.recipes`.
    measurement_id: str | None = Field(default=None, max_length=64)

    # -- ads_conversion ------------------------------------------------------------------------ #
    conversion_id: str | None = Field(default=None, max_length=64)
    conversion_label: str | None = Field(default=None, max_length=64)
    conversion_value: str | None = Field(default=None, max_length=64)
    currency_code: str | None = Field(default=None, max_length=3)
