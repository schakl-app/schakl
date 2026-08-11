"""``uptime`` models (docs/UPTIME.md) — instances and the monitor mirror.

Two org-scoped, RLS-forced tables (§5), shaped by one rule inherited from ``cloudflare``:
**schakl stores what it decided, and separately what it last observed at Uptime Kuma** — never
one column pretending to be both. A monitor row is the tenant's intent (name, type, target,
interval); the ``kuma_monitor_id`` / ``remote_snapshot`` / ``sync_status`` beside it are the last
thing Kuma said. That split is what makes "somebody edited this in Kuma's own UI" expressible at
all, and here it matters more than it did for Cloudflare: Kuma *has* a good web UI and agencies
use it.

Company horizon (#285): ``uptime_monitors`` carries a **nullable ``company_id``**, denormalised
rather than resolved through website → domain. Two reasons the clause other modules declare would
be wrong here. A monitor need not hang off a website at all — an agency also watches a client's
mail server, VPN endpoint and NAS — so a third of the rows have no chain to walk, and a clause
with a null branch is exactly the shape that silently filters nothing. And ``NULL`` already means
the right thing to the repository: not attached to a client, therefore not company data,
therefore visible to restricted staff. Keeping it in step is what ``domain.company_changed``
exists for.

``uptime_instances`` carries no ``company_id``: an instance is org-wide configuration with no
client of its own, readable behind its own admin-only manage permission exactly as
``cloudflare_accounts`` is.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.activity import AuditableMixin
from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base


class InstanceMode(StrEnum):
    """Whether we reach into this instance, or only hear from it.

    Not a degraded pair. ``LINKED`` is the mode for a client-hosted Kuma behind a firewall whose
    owner will never hand over a credential — which is a sensible position for them to take,
    because Uptime Kuma has no user management and the only account there is is the
    administrator's. It delivers the status timeline, the alerts and the automation trigger at no
    infrastructure cost, because the traffic runs the other way.
    """

    MANAGED = "managed"
    LINKED = "linked"


class InstanceStatus(StrEnum):
    """What we last learned about this instance.

    ``NEEDS_REAUTH`` is deliberately not ``ERROR``. Kuma's token dies when its password changes
    or its user is deactivated, and it answers ``authInvalidToken`` — a *state* an admin resolves
    by re-enrolling, not a failure to retry and not a wrong credential. Collapsing it into
    ``ERROR`` sends somebody to rotate something that was never wrong.
    """

    PENDING = "pending"
    ACTIVE = "active"
    ERROR = "error"
    NEEDS_REAUTH = "needs_reauth"


class SyncStatus(StrEnum):
    """The last thing a sync learned about one monitor.

    Five values and not a boolean, for ``RedirectStatus``' reason: each needs a different button.
    ``DRIFT`` offers two of them — *Overschrijven* and *Overnemen* — because an agency editing a
    monitor in Kuma because that screen was closer to hand is the normal case, and a reconcile
    that can only overwrite teaches people to stop using the tool they already had.
    """

    PENDING = "pending"
    ACTIVE = "active"
    DRIFT = "drift"
    MISSING = "missing"
    ERROR = "error"


class UptimeInstance(
    UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, AuditableMixin, Base
):
    """One Uptime Kuma server this tenant works with.

    A row, not a per-org setting: the agency runs one for itself and clients bring theirs, the
    same reason ``cloudflare_accounts`` is a table. Auditable (§16) because repointing or
    re-enrolling a credential that can rewrite a client's monitoring is exactly the change
    somebody needs to attribute six months later — the trail records *that* it changed, never
    the value.
    """

    __tablename__ = "uptime_instances"
    __entity_type__ = "uptime_instance"
    __activity_read_permission__ = "uptime.instance.manage"

    __table_args__ = (
        UniqueConstraint("org_id", "name", name="uq_uptime_instances_org_name"),
        Index("ix_uptime_instances_org_mode", "org_id", "mode"),
    )

    #: Tenant free text ("Breik monitoring", "Klant X — eigen Kuma"). Not i18n'd: it names a
    #: thing the tenant owns.
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default=InstanceMode.MANAGED.value
    )

    #: Absolute, subpath preserved. A reverse-proxied Kuma at ``https://host/kuma/`` is ordinary,
    #: and the subpath is load-bearing — see ``client.socketio_path_for``.
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: The Kuma account we enrolled as. Kept for the settings screen and for re-enrolment; it is
    #: not a credential and is safe to show.
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)

    #: Kuma's JWT, Fernet at rest (``app.core.crypto``), write-only through the API. **Never a
    #: password**: we authenticate once interactively and store the token instead, because Kuma
    #: has no service accounts and the password is the instance's only administrator credential.
    #: The token holds neither the password nor a second factor and is revoked by a password
    #: change, a deactivated user, or a ``jwtSecret`` rotation.
    token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Headers sent on the socket.io handshake, Fernet at rest. **This is the entire tunnel
    #: feature**: a Cloudflare Access service token is ``CF-Access-Client-Id`` +
    #: ``CF-Access-Client-Secret`` and nothing else, so a tunnelled instance, a public one and a
    #: LAN one are the same row with different values.
    connect_headers_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Off is permitted only for a private-address target on a self-hosted deployment, and the
    #: row is badged wherever it is listed. A self-signed Kuma on a client's LAN is ordinary and
    #: refusing it outright would push agencies to expose the box publicly instead — the worse
    #: outcome. A pinned certificate fingerprint is the better answer and the obvious follow-up.
    ssl_verify: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    #: Per-instance random salt for the secret fingerprints in ``UptimeMonitor.remote_snapshot``
    #: (:mod:`.redaction`). Per instance so a fingerprint only compares inside the instance it
    #: was taken from, and an exported database is not one dictionary against every tenant.
    secret_salt: Mapped[str] = mapped_column(String(64), nullable=False)

    #: The shared secret in the inbound webhook URL, compared in constant time. The only
    #: credential a ``linked`` instance has, and the only one that travels *towards* us.
    webhook_secret: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=InstanceStatus.PENDING.value
    )
    #: What ``info`` reported after authentication. 2.x withholds it from unauthenticated
    #: clients, so this is NULL until an enrolment or a sync has actually succeeded.
    server_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    #: Kuma's own text or i18n key for the last failure. Cleared by any successful call — a flag
    #: that only ever turns on is a bug with a long tail.
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class UptimeMonitorProfile(
    UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, AuditableMixin, Base
):
    """A set of monitor defaults the tenant named — "Standaard website", "Klant met SLA".

    Profiles exist because no agency is going to type ``interval=60, retries=3, resend=30,
    accepted=[200-299], expiryNotification=true`` three hundred times. They are the **middle** of
    three layers that must not fuse (:mod:`.profiles`): product invariants are code, the
    tenant's editorial default is this row, and what is true about one monitor is that monitor.

    ``defaults`` is JSONB rather than columns because what a profile may carry grows with every
    monitor type Uptime Kuma adds, and a migration per option is how a defaults table becomes
    something nobody extends.
    """

    __tablename__ = "uptime_monitor_profiles"
    __entity_type__ = "uptime_monitor_profile"
    __activity_read_permission__ = "uptime.profile.manage"

    __table_args__ = (
        UniqueConstraint("org_id", "name", name="uq_uptime_profiles_org_name"),
        Index("ix_uptime_profiles_org_type", "org_id", "monitor_type"),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    monitor_type: Mapped[str] = mapped_column(String(40), nullable=False, default="http")
    defaults: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    #: Uptime Kuma's own notification channel ids. Assigned, never managed: an agency
    #: configuring Slack in Kuma is doing the right thing and Kuma delivers better than we would.
    notification_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class UptimeMonitor(

    UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, AuditableMixin, Base
):
    """One monitor, as we decided it and as Kuma last reported it.

    A **group is a monitor** (``monitor_type == "group"``) with children pointing at it through
    ``parent_id``: Kuma has no group entity, only ``MonitorType.GROUP`` and an integer ``parent``,
    so a second table would be inventing a concept the far end does not have.
    """

    __tablename__ = "uptime_monitors"
    __entity_type__ = "uptime_monitor"
    __activity_read_permission__ = "uptime.monitor.read"

    __table_args__ = (
        UniqueConstraint(
            "org_id", "instance_id", "kuma_monitor_id", name="uq_uptime_monitors_instance_kuma"
        ),
        Index("ix_uptime_monitors_org_instance", "org_id", "instance_id"),
        Index("ix_uptime_monitors_org_company", "org_id", "company_id"),
        Index("ix_uptime_monitors_org_website", "org_id", "website_id"),
        Index("ix_uptime_monitors_org_sync", "org_id", "sync_status"),
    )

    instance_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("uptime_instances.id", ondelete="CASCADE"),
        nullable=False,
    )

    # ---- what the tenant decided -------------------------------------------------------
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    monitor_type: Mapped[str] = mapped_column(String(40), nullable=False)
    #: The target, whichever field this type uses — a URL, a hostname, a container name. Stored
    #: as one column because it is one thing to a reader, and the type says how to read it.
    target: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retries: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("uptime_monitors.id", ondelete="SET NULL"), nullable=True
    )
    #: Paused/resumed at Kuma. Our intent; ``remote_snapshot["active"]`` is theirs.
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Which defaults this monitor follows. ``NULL`` means *the tenant's default profile*,
    #: resolved at read time — inherit, not unfilled.
    profile_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("uptime_monitor_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ---- what it is attached to --------------------------------------------------------
    website_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("websites.id", ondelete="SET NULL"), nullable=True
    )
    domain_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("domains.id", ondelete="SET NULL"), nullable=True
    )
    hosting_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("hosting.id", ondelete="SET NULL"), nullable=True
    )
    #: Denormalised from the link, nullable (see the module docstring). NULL is "attached to no
    #: client", which the repository already reads as *not company data*.
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )

    # ---- what Kuma last said -----------------------------------------------------------
    #: Kuma's own integer id. NULL while a monitor exists here and not yet there.
    kuma_monitor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: The last payload Kuma returned, **redacted** (:mod:`.redaction`): every secret replaced by
    #: ``{"set", "fp"}``. Never round-trip this to Kuma — re-read first, or a client's database
    #: password becomes a JSON object.
    remote_snapshot: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    last_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=SyncStatus.PENDING.value
    )
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: Which of *our* fields Uptime Kuma disagrees with, as of the last sync. A list and not a
    #: boolean: "this monitor drifted" is not actionable, "its interval and its URL drifted" is.
    drift_fields: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    #: Whether we found this monitor (``True``) or created it (``False``).
    #:
    #: It is what makes a *first* sync meaningful. An adopted monitor has no intent of its own
    #: for Kuma to disagree with — its observed state simply *is* the truth, and copying it in
    #: is not an overwrite. One schakl created does have intent, so a difference is drift and
    #: must be reported rather than quietly absorbed.
    adopted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


    @classmethod
    def __portal_horizon_clause__(cls, scope: frozenset[uuid.UUID] | None):  # noqa: ANN206
        """The stricter rule for a client-portal login (#266, #274).

        A client sees monitors for **their own** companies and nothing else — emphatically
        including monitors attached to *no* client, which staff can see and which are exactly
        the agency's own internal infrastructure. That is the difference between the staff
        horizon (where ``NULL`` means "not company data, stays visible") and this one, and it is
        why the rule lives on the model rather than in each read: ``entity_visible`` and the
        directory seam both prefer this clause, and one of the two callers always forgets.

        A client with no company scope sees nothing at all, rather than everything.
        """
        if scope is None:
            return cls.company_id.is_(None) & cls.company_id.is_not(None)  # always false
        return cls.company_id.in_(scope)


class UptimeHeartbeat(UUIDPrimaryKeyMixin, OrgScopedMixin, Base):
    """One state change a monitor reported. A **bounded rolling window**, not a warehouse.

    Uptime Kuma keeps the real history and answers questions about it better than a mirror
    would, so this holds only what a panel and a report section draw, pruned by cron. It is also
    the one table an unauthenticated caller can cause a row in (:mod:`.webhook`), which is why
    it carries no free text from the request beyond a bounded message.

    No ``TimestampMixin``: ``observed_at`` is the only time that means anything here, and a
    row is never updated — a heartbeat that changed would not be a heartbeat.
    """

    __tablename__ = "uptime_heartbeats"
    __entity_type__ = "uptime_heartbeat"

    __table_args__ = (
        # The idempotency guarantee, at the database rather than in application code. A monitor
        # flapping delivers the same transition twice and Uptime Kuma retries besides, so
        # "have we recorded this?" followed by an insert leaves a window every retry enters —
        # including across two API replicas that share no memory (docs/PAYMENTS.md's lesson).
        UniqueConstraint(
            "org_id", "monitor_id", "status", "observed_at", name="uq_uptime_heartbeat_event"
        ),
        Index("ix_uptime_heartbeats_org_monitor_time", "org_id", "monitor_id", "observed_at"),
    )

    monitor_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("uptime_monitors.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: ``up`` / ``down`` / ``pending`` / ``maintenance`` — Uptime Kuma's own vocabulary.
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Kuma's own message, truncated. Not translatable — it is the far end's text about the
    #: far end's check, and inventing an i18n key for it would be inventing its meaning.
    message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    ping_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Whether this arrived by webhook (reported) or by an authenticated read (observed). A
    #: `linked` instance can only ever produce the first, and the screen says so rather than
    #: presenting a claim as a measurement.
    reported: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
