"""The per-org e-mail transport configuration (#17)."""

from __future__ import annotations

from sqlalchemy import String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base

#: Supported transports. The three named services go through their official HTTP APIs
#: (delivery feedback, no port juggling); ``smtp`` is the bring-your-own-relay case.
EMAIL_PROVIDERS: tuple[str, ...] = ("smtp", "brevo", "sendgrid", "smtp2go")


class EmailSettings(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """One row per org: which provider sends this tenant's mail, and as whom.

    ``config_enc`` is the provider-specific configuration (API key, or SMTP host/port/credentials)
    as an **encrypted** JSON document (:mod:`app.core.crypto`) — it holds secrets, so it is never
    returned by the API; reads expose only the non-secret fields plus a configured-flag.
    """

    __tablename__ = "email_settings"
    __table_args__ = (UniqueConstraint("org_id", name="uq_email_settings_org"),)

    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    config_enc: Mapped[str] = mapped_column(Text, nullable=False)
    from_email: Mapped[str] = mapped_column(String(320), nullable=False)
    from_name: Mapped[str] = mapped_column(String(255), nullable=False)
    reply_to: Mapped[str | None] = mapped_column(String(320), nullable=True)
