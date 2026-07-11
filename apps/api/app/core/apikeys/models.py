"""API keys and service accounts (#20).

Two principal kinds authenticate with a key:

* a **personal** key acts as a real ``user`` — its effective permissions are always its owner's
  live permissions intersected with the key's scopes, re-evaluated per request, so a demoted
  member's key is demoted with them;
* a **service-account** key acts as a synthetic principal (a ``service_accounts`` row) that
  survives the employee who created it leaving — automation is not a shared login.

Both tables are org-scoped and RLS-forced like every domain table. The secret is never stored:
only its SHA-256 hash (the secret is 256-bit random, so a slow KDF would tax every request for
no security gain) and a plaintext ``prefix`` for lookup.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base

PRINCIPAL_USER = "user"
PRINCIPAL_SERVICE_ACCOUNT = "service_account"


class ServiceAccount(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """A non-human principal automation authenticates as. Outlives its creator (#20)."""

    __tablename__ = "service_accounts"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class ApiKey(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """A hashed API secret bound to a principal, with scopes and a mandatory expiry.

    ``prefix`` is stored plaintext and is what a request is looked up by (indexed, unique per
    org); the full secret is compared against ``hash`` in constant time. ``scopes`` is the set of
    permission strings the key may exercise — capped by the creator, and for a personal key
    further capped by the owner's *live* permissions on every request.
    """

    __tablename__ = "api_keys"
    __table_args__ = (UniqueConstraint("org_id", "prefix", name="uq_api_keys_org_id_prefix"),)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    prefix: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    hash: Mapped[str] = mapped_column(String(64), nullable=False)
    principal_type: Mapped[str] = mapped_column(String(20), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    service_account_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("service_accounts.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    #: The permission strings this key may exercise (stored suffixed for scoped permissions).
    scopes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    #: Mandatory (#20): no immortal keys. Enforced with a maximum at creation.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Updated out-of-band from a Redis marker by a cron — never on the request hot path.
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
