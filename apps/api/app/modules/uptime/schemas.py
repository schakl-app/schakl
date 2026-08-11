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
    last_error: str | None
    last_observed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    #: Resolved for display only when the caller asked for it (`meta=true`) — a picker throws
    #: these away and paying for them on every list is the shape `docs/PERFORMANCE.md` bans.
    company_name: str | None = None
    instance_name: str | None = None
    #: Kuma's last reported up/down for this monitor, read from the redacted snapshot. `None`
    #: means we have never observed it, which is not the same as "down".
    remote_active: bool | None = None


class UptimeSyncReport(BaseModel):
    """What a read-only sync did, reported rather than silently applied.

    A sync never writes to Kuma and never links a monitor to a client on a guess: ``ambiguous``
    and ``unmatched`` are handed back for a person to resolve, because two websites on the same
    apex is an ordinary thing and picking one attaches a client's monitoring to another client's
    record with every row valid.
    """

    instance_id: uuid.UUID
    ok: bool
    server_version: str | None = None
    seen: int = 0
    created: int = 0
    updated: int = 0
    missing: int = 0
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
