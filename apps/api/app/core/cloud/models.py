"""Cloud instance-level tables (epic #199). Business-licensed — see this directory's LICENSE.

Both tables are **instance-level** (listed in ``app.db.INSTANCE_LEVEL_TABLES``): the instance
surface reads them before any tenant is bound, so they cannot sit under RLS. Writes are
org-bound by code and every mutation lands on the instance audit trail.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.mixins import UUIDPrimaryKeyMixin
from app.db import Base

#: Cloud plans (issue #200 slice). ``trial`` expires (the cloud cron suspends it past
#: ``orgs.trial_ends_at``); ``standard`` is billing-managed over the provisioning API;
#: ``unlimited`` never expires and the cron never touches it.
PLANS: tuple[str, ...] = ("trial", "standard", "unlimited")


class ServiceAccessGrant(UUIDPrimaryKeyMixin, Base):
    """An org-issued service PIN: the tenant's time-boxed consent for instance-owner access.

    On cloud the instance owner may not read an org's data (detail, export, impersonation)
    until an org admin generates a PIN and hands it over; claiming the PIN unlocks that one
    org, for the claiming owner only, until ``expires_at``. The org can revoke at any time.
    Only the PIN's SHA-256 is stored; ``created_by_email`` is snapshotted so the trail
    outlives the account (§16).
    """

    __tablename__ = "service_access_grants"

    org_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("orgs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    pin_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by_email: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InstanceApiKey(UUIDPrimaryKeyMixin, Base):
    """A provisioning credential for the cloud instance — a machine principal, not a user.

    Same secret mechanics as the org-scoped API keys (#20: ``schakl_<prefix>_<secret>``,
    SHA-256, constant-time compare) but deliberately a separate instance-level table: the
    org-scoped table is resolved *after* the host→org mapping, and a provisioning call has
    no org yet. ``expires_at`` NULL = never expires; revocation is a tombstone.
    """

    __tablename__ = "instance_api_keys"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    prefix: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    hash: Mapped[str] = mapped_column(String(64), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=lambda: ["provisioning"], server_default='["provisioning"]'
    )
    created_by_email: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
