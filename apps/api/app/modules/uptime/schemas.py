"""Request/response models for the uptime module (docs/UPTIME.md).

Names are prefixed ``Uptime*`` on purpose: a generic Pydantic name makes FastAPI qualify **both**
modules' OpenAPI components when a second module picks the same one, which rewrites somebody
else's generated client for a change they never made.

Nothing here ever carries a credential outward. ``token_configured`` says whether one is stored;
the token, the password and the connect headers' values have no read shape at all.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.uptime.client import normalise_base_url
from app.modules.uptime.models import InstanceMode


class UptimeInstanceBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    mode: InstanceMode = InstanceMode.MANAGED
    base_url: str | None = Field(default=None, max_length=500)
    ssl_verify: bool = True
    active: bool = True

    @field_validator("base_url")
    @classmethod
    def _normalise(cls, value: str | None) -> str | None:
        """Reject a relative URL here rather than at connect time.

        The subpath is preserved, because it is load-bearing (``client.socketio_path_for``) and
        because silently dropping it would point an agency's reverse-proxied Kuma at whatever
        else lives at the root of that host.
        """
        if value is None or not value.strip():
            return None
        try:
            return normalise_base_url(value)
        except ValueError as exc:
            raise ValueError("errors.uptime_invalid_url") from exc


class UptimeInstanceCreate(UptimeInstanceBase):
    pass


class UptimeInstanceUpdate(BaseModel):
    """Every field optional; absent means *leave alone*, which is what a partial save means.

    ``connect_headers`` is the exception worth naming: an explicit ``{}`` clears them, absent
    keeps them. That distinction is the difference between "I did not touch the tunnel settings"
    and "this instance is no longer behind Access".
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    mode: InstanceMode | None = None
    base_url: str | None = Field(default=None, max_length=500)
    ssl_verify: bool | None = None
    active: bool | None = None
    connect_headers: dict[str, str] | None = None

    _normalise = field_validator("base_url")(UptimeInstanceBase._normalise.__func__)  # type: ignore[attr-defined]


class UptimeEnrol(BaseModel):
    """The one request that carries a password — and the only one, by design.

    Kuma has no service accounts, so this is the instance's administrator credential. It is used
    once to obtain a token and is never stored: see ``UptimeService.enrol``.
    """

    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=500)
    totp: str | None = Field(default=None, max_length=16)
    #: Sent on every handshake afterwards. Where a Cloudflare Access service token goes.
    connect_headers: dict[str, str] | None = None


class UptimeInstanceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    mode: InstanceMode
    base_url: str | None
    username: str | None
    ssl_verify: bool
    active: bool
    status: str
    server_version: str | None
    last_error: str | None
    last_checked_at: datetime | None
    last_synced_at: datetime | None
    created_at: datetime
    updated_at: datetime

    #: Whether a token is stored — never the token. A write-only credential still has to be
    #: *visible as configured*, or an admin cannot tell "not set up" from "set up and failing".
    token_configured: bool = False
    #: Whether handshake headers are stored, and which header **names** (never their values):
    #: seeing `CF-Access-Client-Id` listed is how an admin confirms the tunnel is wired.
    connect_header_names: list[str] = Field(default_factory=list)
    #: True when TLS verification is off — surfaced so the list can badge it (§5).
    insecure: bool = False
    monitor_count: int = 0
    #: How many of ``monitor_count`` are groups — the folders, counted inside the total because
    #: a group is a monitor here and a number that quietly excluded them would disagree with the
    #: list this screen links to.
    group_count: int = 0


class UptimeInstanceOption(BaseModel):
    """One instance as the *create-a-monitor* form needs it, and nothing more (#366).

    A second, leaner read of the same rows exists for the reason ``list_profiles`` is readable on
    ``monitor.read``: the form that creates a monitor has to **show which Uptime Kuma it lands
    on**, and gating that on ``instance.manage`` would leave a member who holds exactly the
    permission the create route declares with a picker they cannot populate (#310). Every field
    here is already visible to such a caller — ``instance_name`` rides every monitor row under
    ``meta=true`` — so this reveals nothing new, which is what makes the wider gate safe.

    What is **not** here is the whole point: no ``base_url``, no ``username``, no
    ``token_configured``, no connect-header names. Those are facts about a credential, and they
    stay behind ``instance.manage`` where the settings screen reads them.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    mode: InstanceMode

    #: Whether a monitor created here can actually reach Uptime Kuma. A ``linked`` instance has no
    #: credential by definition (docs/UPTIME.md §4), so ``_push`` cannot write to it and a monitor
    #: created against one would sit at ``pending`` for ever. Offering it would be #253's control
    #: that always refuses — so the picker draws only the instances where this is true, and says
    #: so in words when there are none.
    writable: bool = False


class UptimeLinkCandidate(BaseModel):
    """One anchor a found monitor could belong to (#321).

    ``company_id`` is what the *match* saw, so the screen can say whose it is; the link route
    re-resolves it rather than trusting this, because a domain that changed hands since the
    sync would otherwise write yesterday's client onto today's monitor.
    """

    entity_type: str
    entity_id: uuid.UUID
    label: str
    company_id: uuid.UUID | None = None


class UptimeMonitorRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    instance_id: uuid.UUID
    name: str
    monitor_type: str
    target: str | None
    port: int | None
    interval_seconds: int | None
    retries: int | None
    parent_id: uuid.UUID | None
    active: bool
    website_id: uuid.UUID | None
    domain_id: uuid.UUID | None
    hosting_id: uuid.UUID | None
    company_id: uuid.UUID | None
    kuma_monitor_id: int | None
    sync_status: str
    #: Which of *our* fields Uptime Kuma disagrees with. A list, not a boolean: "this monitor
    #: drifted" is not actionable and "its interval and its URL drifted" is.
    drift_fields: list[str] = Field(default_factory=list)
    #: Whether we found this monitor or created it — what makes a difference *drift* rather
    #: than simply the truth.
    adopted: bool = True
    profile_id: uuid.UUID | None = None
    last_error: str | None
    last_observed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    #: Resolved for display only when the caller asked for it (`meta=true`) — a picker throws
    #: these away and paying for them on every list is the shape `docs/PERFORMANCE.md` bans.
    company_name: str | None = None
    instance_name: str | None = None
    #: The name of the group this monitor sits in, resolved under the same `meta=true`. `None`
    #: means top-level, which is a real answer and not a missing one: an agency that groups
    #: nothing is ordinary, and Kuma's own list is flat until somebody makes a folder.
    parent_name: str | None = None
    #: How many monitors sit in this group, under the same `meta=true` and only for a group.
    #: What makes the delete guard predictable rather than a surprise 409 (#321).
    child_count: int = 0
    #: Kuma's last reported up/down for this monitor, read from the redacted snapshot. `None`
    #: means we have never observed it, which is not the same as "down".
    remote_active: bool | None = None

    #: What the last match found (#321) — proposals a person confirms, never links already made.
    link_candidates: list[UptimeLinkCandidate] = Field(default_factory=list)
    #: When that match ran. `None` is *nobody has ever looked*, which an empty candidate list
    #: cannot say on its own — the distinction the screen needs to avoid telling an admin
    #: "niets gevonden" about an instance that has never synced.
    link_checked_at: datetime | None = None
    #: `linked` / `matched` / `ambiguous` / `unmatched`, derived from the columns above
    #: (`matching.link_status`). Derived rather than stored, so a link made by hand changes it
    #: at once and there is no second column to keep in step.
    link_status: str = "unmatched"


class UptimeMonitorLink(BaseModel):
    """Attach this monitor to one website, domain or hosting account — or to nothing.

    **One anchor, not three columns.** A shape with three optional ids would let a caller set
    two of them, and a monitor that claims to watch one client's website and another client's
    hosting is a row no screen can render honestly.

    ``entity_type: null`` detaches (§18's *explicit null means clear*). It is spelled as an
    explicit null rather than a missing body so that "I opened the dialog and changed nothing"
    can never mean "unlink it".
    """

    model_config = ConfigDict(extra="forbid")

    entity_type: Literal["website", "domain", "hosting"] | None = None
    entity_id: uuid.UUID | None = None

    @field_validator("entity_id")
    @classmethod
    def _both_or_neither(cls, value: uuid.UUID | None, info: Any) -> uuid.UUID | None:
        if bool(value) != bool(info.data.get("entity_type")):
            raise ValueError("errors.uptime_link_incomplete")
        return value


class UptimeLinkApplyResult(BaseModel):
    """What applying every unambiguous proposal did.

    ``skipped`` is the ambiguous ones and is deliberately not an error: they are the rows this
    button is *not* allowed to decide, and reporting them is how the screen says there is still
    work left rather than falling silent on it.
    """

    linked: int = 0
    skipped: int = 0


class UptimeSyncReport(BaseModel):
    """What a read-only sync did, reported rather than silently applied.

    A sync never writes to Kuma and never links a monitor to a client on a guess: ``ambiguous``
    and ``unmatched`` are handed back for a person to resolve, because two websites on the same
    apex is an ordinary thing and picking one attaches a client's monitoring to another client's
    record with every row valid.

    ``matched`` counts **proposals**, not links. Nothing here is applied by the sync, which is
    why the number can stay the same across two runs and still be honest: it describes what is
    waiting for somebody, and the reconciliation screen is where it stops waiting.
    """

    instance_id: uuid.UUID
    ok: bool
    server_version: str | None = None
    seen: int = 0
    #: How many of ``seen`` were groups. Reported because "34 monitors" and "30 monitors in 4
    #: groups" are different answers to "did my structure come across", and only the second one
    #: tells an admin the hierarchy survived the read.
    groups: int = 0
    created: int = 0
    updated: int = 0
    missing: int = 0
    #: Monitors schakl created whose settings Uptime Kuma now disagrees with.
    drifted: int = 0
    matched: int = 0
    ambiguous: int = 0
    unmatched: int = 0
    #: An i18n key when the sync failed. The instance's `last_error` holds Kuma's own text.
    error: str | None = None


class UptimeProbeResult(BaseModel):
    """What a connection check found — evidence, never a gate.

    A failed probe on one instance must not blank another's list, and must not hide this one's
    stored mirror: the screen keeps rendering what we last observed with a *"laatst gelezen om…"*
    line beside it.
    """

    ok: bool
    status: str
    server_version: str | None = None
    #: i18n key, so the message is translatable; Kuma's own text goes to `last_error`.
    error: str | None = None
    detail: str | None = None


# ---------------------------------------------------------------------- gate 2


class UptimeProfileBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    monitor_type: str = Field(default="http", max_length=40)
    #: Only the keys a profile may set survive (`profiles.PROFILE_KEYS`) — an allow-list, so a
    #: field added to a monitor tomorrow is not silently profile-writable today.
    defaults: dict[str, Any] = Field(default_factory=dict)
    notification_ids: list[int] = Field(default_factory=list)
    is_default: bool = False
    active: bool = True
    position: int = 0


class UptimeProfileCreate(UptimeProfileBase):
    pass


class UptimeProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    monitor_type: str | None = Field(default=None, max_length=40)
    defaults: dict[str, Any] | None = None
    notification_ids: list[int] | None = None
    is_default: bool | None = None
    active: bool | None = None
    position: int | None = None


class UptimeProfileRead(UptimeProfileBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class UptimeMonitorCreate(BaseModel):
    """A monitor schakl creates and pushes.

    Every settings field is optional and ``None`` means **inherit** — from the profile, then
    from the built-in defaults (`profiles.resolve`). That is what makes "volg de standaard" a
    thing the form can express rather than a value it has to guess.
    """

    model_config = ConfigDict(extra="forbid")

    instance_id: uuid.UUID
    name: str = Field(min_length=1, max_length=255)
    monitor_type: str = Field(default="http", max_length=40)
    target: str | None = Field(default=None, max_length=1000)
    port: int | None = Field(default=None, ge=1, le=65535)
    interval_seconds: int | None = Field(default=None, ge=20, le=86_400)
    retries: int | None = Field(default=None, ge=0, le=10)
    parent_id: uuid.UUID | None = None
    profile_id: uuid.UUID | None = None
    website_id: uuid.UUID | None = None
    domain_id: uuid.UUID | None = None
    hosting_id: uuid.UUID | None = None
    company_id: uuid.UUID | None = None
    active: bool = True


class UptimeMonitorUpdate(BaseModel):
    """Absent means leave alone. Note what is **not** here: `instance_id` and `kuma_monitor_id`.

    A monitor cannot change instances — that is a delete and a create, at two different Uptime
    Kumas — and its remote id is theirs, not a field anybody edits.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    target: str | None = Field(default=None, max_length=1000)
    port: int | None = Field(default=None, ge=1, le=65535)
    interval_seconds: int | None = Field(default=None, ge=20, le=86_400)
    retries: int | None = Field(default=None, ge=0, le=10)
    parent_id: uuid.UUID | None = None
    profile_id: uuid.UUID | None = None
    website_id: uuid.UUID | None = None
    domain_id: uuid.UUID | None = None
    hosting_id: uuid.UUID | None = None
    company_id: uuid.UUID | None = None


class UptimeReconcile(BaseModel):
    """Which way to resolve a drift.

    Two directions and no default, on purpose: a reconcile that silently picked one would be
    making the tenant's decision for them, and the two are not symmetrical — one overwrites a
    colleague's edit in Uptime Kuma, the other overwrites schakl's record.
    """

    model_config = ConfigDict(extra="forbid")

    direction: Literal["push", "adopt"]


class UptimeMonitorDeleteResult(BaseModel):
    deleted_at_kuma: bool
