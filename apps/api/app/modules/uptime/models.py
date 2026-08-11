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
