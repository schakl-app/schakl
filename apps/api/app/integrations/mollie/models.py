"""``mollie`` models (epic #269, issue #267) — the payment credential, and nothing else.

One org-scoped, RLS-forced table. What it deliberately does **not** hold is as important as
what it does: there is no Mollie-side mirror of a payment here. The attempt, its status and
its settlement live in ``invoice_payment_intents``, because "what has been paid" is invoicing's
question and a second copy of it in every provider module is how two screens start disagreeing.
This module's whole job is *a credential and a conversation*.

A **row, not a settings singleton**, for the reason ``cloudflare_accounts`` and
``oxxa_accounts`` are rows: an agency mid-merger holds two Mollie profiles, and — far more
commonly here — an agency integrating holds a live key *and* a test key at the same time. A
singleton would have made the second one an overwrite, which for a payment credential means
either taking real money in a test or failing to take any in production.
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
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.activity import AuditableMixin
from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base


class MollieAccountStatus(StrEnum):
    """Whether the stored credential still works. ``error`` is set by whatever found out."""

    ACTIVE = "active"
    ERROR = "error"


class MollieMode(StrEnum):
    """Which world this credential acts in. **Mollie's keys say so themselves** — a key is
    literally prefixed ``test_`` or ``live_`` — so this is derived on save and never entered:
    a field an admin can get wrong about money is a field that should not exist."""

    LIVE = "live"
    TEST = "test"


class MollieAccount(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, AuditableMixin, Base):
    """One Mollie API key the tenant has handed us.

    Auditable (§16): rotating the credential that collects an agency's money is exactly the
    change somebody needs attributed later. The key itself is never part of the trail — only
    the fact that it changed, and by whom.
    """

    __tablename__ = "mollie_accounts"
    __entity_type__ = "mollie_account"
    __activity_read_permission__ = "mollie.settings.manage"

    __table_args__ = (
        UniqueConstraint("org_id", "name", name="uq_mollie_accounts_org_name"),
        Index("ix_mollie_accounts_org_active", "org_id", "active"),
    )

    #: Tenant free text ("Mollie — Breik", "Mollie test"). Not i18n'd: it names a thing the
    #: tenant owns, like ``providers.name``.
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    #: Fernet at rest (:mod:`app.core.crypto`), write-only through the API. **Never** in a
    #: response, a log line or an error. Mollie authenticates with a Bearer header rather than
    #: a query parameter, so a leaked URL does not leak this — but an error body echoed into
    #: ``last_error`` still could, which is why ``client.redact`` exists anyway.
    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)

    #: ``live`` or ``test``, derived from the key's own prefix on every save.
    mode: Mapped[str] = mapped_column(
        String(10), nullable=False, default=MollieMode.LIVE.value, server_default="live"
    )

    #: The secret half of the callback URL (``app.core.payments.tokens``). Regenerated whenever
    #: the API key is rotated: a credential replaced because it leaked must not leave the
    #: previous URL answering.
    webhook_secret: Mapped[str] = mapped_column(String(64), nullable=False)

    #: The payment methods this credential reported as enabled, last time we asked. An
    #: **observation**, never a setting: enabling a method happens in Mollie's own dashboard,
    #: and a list stored here that pretended otherwise would be a second source of truth
    #: (CLAUDE.md §10). It is also what an admin actually uses to tell two keys apart, which
    #: is why there is no ``profile_id`` column: issue #267 asked for one, and the Profiles
    #: API turns out to need an advanced access token or OAuth — a plain API key cannot read
    #: it. Storing a field we can only sometimes fill would have been worse than not having it.
    methods: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    #: The user-facing "which provider is this" label (#89). SET NULL: deleting a catalog row
    #: must never take a working credential with it.
    provider_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("providers.id", ondelete="SET NULL"), nullable=True
    )

    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=MollieAccountStatus.ACTIVE.value,
        server_default=MollieAccountStatus.ACTIVE.value,
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Mollie's own untranslatable text for the last failure. Read by a human on the settings
    #: screen; never in an error envelope, whose ``message`` is an i18n key (§9).
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
