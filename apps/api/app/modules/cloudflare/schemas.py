"""Pydantic schemas for the cloudflare module (epic #278).

Two conventions worth stating once:

* **The API token is write-only.** It goes in on create/update and never comes back out — not in
  a read model, not in the OpenAPI spec, not masked. ``token_configured`` is the only thing a
  client learns about it, exactly as ``google.settings``' client secret works.
* **A status report names its problems as keys, not sentences.** ``issues`` carries stable
  machine strings that the web resolves to ``cloudflare.issue.*`` messages, so the API never
  picks a locale for someone else's screen (CLAUDE.md §8, §17's "labels are a display concern").
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.cloudflare.models import REDIRECT_STATUS_CODES

# --- accounts --------------------------------------------------------------------------- #


class AccountRead(BaseModel):
    """A configured Cloudflare account. Never carries the token."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    cf_account_id: str | None = None
    cf_account_name: str | None = None
    provider_id: uuid.UUID | None = None
    provider_name: str | None = None
    active: bool
    status: str
    #: Observed at verify time — see ``client.CAPABILITIES``. A missing key means "not probed".
    capabilities: dict[str, bool] = Field(default_factory=dict)
    last_verified_at: datetime | None = None
    last_synced_at: datetime | None = None
    last_error: str | None = None
    #: Whether a token is stored at all. The token itself never leaves the server.
    token_configured: bool = True
    #: How many synced zones point at this account — the number the settings row prints.
    zone_count: int = 0


class AccountOption(BaseModel):
    """An account as a *picker* needs it: a name to choose between, nothing else.

    Separate from :class:`AccountRead` because the two have different readers. Choosing which
    Cloudflare account to create a zone in is part of ``cloudflare.zone.manage``; seeing how a
    credential is configured, what it may do and why it last failed is ``settings.manage``. One
    endpoint serving both would have forced the picker's holder to hold the credential screen's
    permission (docs/UX.md: a control that renders without checking `can()` — inverted).
    """

    id: uuid.UUID
    name: str
    active: bool


class AccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    #: A **scoped API token**. Cloudflare's legacy Global API Key is refused on purpose: it is
    #: unscoped, unrevocable per-integration, and grants everything the account can do.
    api_token: str = Field(min_length=20, max_length=512)
    #: Pin the account explicitly when the token can see more than one (rare, but real for an
    #: agency whose Cloudflare login sits on several accounts).
    cf_account_id: str | None = Field(default=None, max_length=64)
    provider_id: uuid.UUID | None = None
    active: bool = True


class AccountUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    #: Omit to keep the stored token; send a new one to rotate. Never send an empty string to
    #: clear it — an account without a token is not a state this module has a use for.
    api_token: str | None = Field(default=None, min_length=20, max_length=512)
    cf_account_id: str | None = Field(default=None, max_length=64)
    provider_id: uuid.UUID | None = None
    active: bool | None = None


class AccountVerifyResult(BaseModel):
    """What a verify learned. ``account`` is filled when the token sees exactly one account."""

    ok: bool
    capabilities: dict[str, bool] = Field(default_factory=dict)
    cf_account_id: str | None = None
    cf_account_name: str | None = None
    #: More than one account behind the token: the admin must pick, so both are named.
    account_choices: list[dict[str, str]] = Field(default_factory=list)
    error: str | None = None


class AccountSyncResult(BaseModel):
    """The outcome of pulling an account's inventory. Counts, not rows — the lists are paginated
    endpoints of their own."""

    zones_synced: int = 0
    zones_matched: int = 0
    pages_projects_synced: int = 0
    #: Non-fatal problems (Pages unreadable with this token, for instance): the zone sync still
    #: succeeded and saying so beats failing the whole action.
    warnings: list[str] = Field(default_factory=list)


# --- zones ------------------------------------------------------------------------------ #


class ZoneRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_id: uuid.UUID
    account_name: str | None = None
    cf_zone_id: str
    name: str
    status: str
    plan: str | None = None
    paused: bool = False
    name_servers: list[str] = Field(default_factory=list)
    original_name_servers: list[str] = Field(default_factory=list)
    domain_id: uuid.UUID | None = None
    domain_name: str | None = None
    last_synced_at: datetime | None = None


class ZoneLink(BaseModel):
    domain_id: uuid.UUID


# --- DNS records -------------------------------------------------------------------------- #


class DnsRecordRead(BaseModel):
    """One record as Cloudflare reports it. Read live — never stored (a DNS record is not our
    data, and a cached copy would be wrong within minutes of any change)."""

    id: str
    type: str
    name: str
    content: str
    ttl: int = 1
    proxied: bool = False
    priority: int | None = None
    comment: str | None = None


class DnsRecordWrite(BaseModel):
    type: str = Field(min_length=1, max_length=16)
    name: str = Field(min_length=1, max_length=253)
    content: str = Field(min_length=1, max_length=2048)
    #: ``1`` is Cloudflare's "automatic". A proxied record ignores TTL entirely.
    ttl: int = Field(default=1, ge=1, le=86400)
    proxied: bool = False
    priority: int | None = Field(default=None, ge=0, le=65535)
    comment: str | None = Field(default=None, max_length=100)


# --- redirects ---------------------------------------------------------------------------- #


class RedirectWrite(BaseModel):
    """The tenant's intent for a domain-wide redirect."""

    target_url: str = Field(min_length=1, max_length=2048)
    status_code: int = 301
    preserve_path: bool = True
    preserve_query: bool = True
    include_subdomains: bool = True
    #: Create the proxied placeholder records a redirect needs when the zone has none. A
    #: Redirect Rule only fires for traffic that *reaches* Cloudflare's edge, and a zone whose
    #: apex has no proxied record never sends any — the rule saves fine and does nothing, which
    #: is the single most confusing failure this feature has.
    ensure_origin: bool = True

    @field_validator("status_code")
    @classmethod
    def _known_status(cls, value: int) -> int:
        if value not in REDIRECT_STATUS_CODES:
            raise ValueError("unsupported redirect status code")
        return value


class RedirectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    zone_id: uuid.UUID
    domain_id: uuid.UUID
    target_url: str
    status_code: int
    preserve_path: bool
    preserve_query: bool
    include_subdomains: bool
    last_status: str
    last_error: str | None = None
    last_checked_at: datetime | None = None
    last_pushed_at: datetime | None = None


# --- Pages -------------------------------------------------------------------------------- #


class PagesProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_id: uuid.UUID
    account_name: str | None = None
    name: str
    subdomain: str | None = None
    production_branch: str | None = None


class PagesLinkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    project_name: str | None = None
    domain_id: uuid.UUID
    hostname: str
    status: str | None = None
    last_error: str | None = None
    last_checked_at: datetime | None = None


class PagesLinkCreate(BaseModel):
    project_id: uuid.UUID
    #: The hostname to serve from the project. Defaults to the domain's apex when omitted.
    hostname: str | None = Field(default=None, max_length=253)


# --- connect + status ---------------------------------------------------------------------- #


class ConnectRequest(BaseModel):
    """"Connect this domain to Cloudflare" — adopt the existing zone, or create one."""

    #: Which of the tenant's Cloudflare accounts. Required whenever more than one is active:
    #: guessing would put a client's zone in the wrong account, and moving a zone between
    #: accounts at Cloudflare means deleting and recreating it.
    account_id: uuid.UUID | None = None
    #: Create the zone when the account does not have it yet. False = adopt-only, which is the
    #: safe first step when taking over a client's existing Cloudflare setup.
    create_if_missing: bool = True


class ZoneCandidate(BaseModel):
    """One account's answer to "do you have this zone?" — the shape ambiguity is reported in."""

    account_id: uuid.UUID
    account_name: str
    cf_zone_id: str
    status: str
    name_servers: list[str] = Field(default_factory=list)


class RedirectObservation(BaseModel):
    """What Cloudflare currently has for our rule, next to what we asked for."""

    present: bool = False
    status_code: int | None = None
    target: str | None = None
    #: Field names of :class:`RedirectWrite` that Cloudflare disagrees with — empty when in sync.
    differences: list[str] = Field(default_factory=list)


class RedirectConflict(BaseModel):
    """Something *else* on this zone that already redirects, or could.

    Reported rather than resolved: Cloudflare evaluates redirect rules top-down and we cannot
    evaluate a tenant's filter expression to know whether it catches this hostname. Naming it
    lets the admin decide; silently appending our rule below it would look like it worked.
    """

    kind: Literal["redirect_rule", "page_rule"]
    description: str = ""
    detail: str = ""


class OriginState(BaseModel):
    """Whether traffic for this domain reaches Cloudflare's edge at all.

    A redirect rule on a zone with no proxied record for the apex is inert. This is the check
    that turns "I set the redirect and nothing happens" into a sentence.
    """

    apex_proxied: bool = False
    www_proxied: bool = False
    #: True when the zone has records but none of them are proxied — the "grey cloud" case.
    has_records: bool = False


class DomainStatusRead(BaseModel):
    """Everything known about one domain's Cloudflare state.

    ``live`` says whether Cloudflare was actually asked. The stored read (``GET .../status``) is
    the cheap one a page load uses; ``POST .../check`` is the one that talks to Cloudflare and
    fills in ``conflicts``, ``origin`` and the redirect observation (docs/PERFORMANCE.md — a
    detail page must not depend on an outside API being up).
    """

    domain_id: uuid.UUID
    domain_name: str
    live: bool = False

    zone: ZoneRead | None = None
    #: Every account that has this apex. More than one is legal at Cloudflare (only *activation*
    #: is exclusive) and is exactly the state a "connect" must refuse to guess through.
    candidates: list[ZoneCandidate] = Field(default_factory=list)

    #: The nameservers Cloudflare expects, and the ones public DNS currently answers — the
    #: latter from the domains module's own periodic lookup, never a second resolver here.
    expected_nameservers: list[str] = Field(default_factory=list)
    observed_nameservers: list[str] = Field(default_factory=list)
    nameservers_delegated: bool = False

    redirect: RedirectRead | None = None
    redirect_live: RedirectObservation | None = None
    conflicts: list[RedirectConflict] = Field(default_factory=list)
    origin: OriginState | None = None
    pages_links: list[PagesLinkRead] = Field(default_factory=list)

    #: What the domain record itself claims, so "schakl says redirect, Cloudflare does not" is
    #: visible without opening two screens.
    domain_status: str | None = None
    domain_redirect_url: str | None = None

    #: Stable keys resolved to ``cloudflare.issue.*`` by the client. Ordered most-actionable
    #: first; an empty list means there is nothing to do.
    issues: list[str] = Field(default_factory=list)
    #: A probe that could not run (the token lacks the scope) — named so the admin can widen the
    #: token instead of reading a silently incomplete report as "all clear".
    unavailable: list[str] = Field(default_factory=list)


class DnsExport(BaseModel):
    """A zone export. ``content`` is the file body; the client saves it under ``filename``."""

    filename: str
    content_type: str
    content: str


class ZoneRecords(BaseModel):
    """A zone's live records plus the state of the read itself."""

    zone_id: uuid.UUID
    zone_name: str
    records: list[DnsRecordRead] = Field(default_factory=list)


class PanelPayload(BaseModel):
    """Shape of the cloudflare panel's data (documented here, served as a plain dict)."""

    connected: bool
    zone_name: str | None = None
    zone_status: str | None = None
    redirect_target: str | None = None
    redirect_status: str | None = None
    issues: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)
