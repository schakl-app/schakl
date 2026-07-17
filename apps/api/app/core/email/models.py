"""The per-org e-mail transport configuration (#17)."""

from __future__ import annotations

from sqlalchemy import String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base

#: Supported transports. The three named services go through their official HTTP APIs
#: (delivery feedback, no port juggling); ``smtp`` is the bring-your-own-relay case;
#: ``instance`` is the explicit choice for the operator-provided transport (config.py
#: ``SCHAKL_INSTANCE_EMAIL_*`` — the cloud "included e-mail", epic #199) and stores no
#: credentials of its own.
EMAIL_PROVIDERS: tuple[str, ...] = ("smtp", "brevo", "sendgrid", "smtp2go", "instance")


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


#: The auth mails a tenant may customise (#161 tier 2). Both ride the reset-token mechanism.
EMAIL_TEMPLATE_KINDS: tuple[str, ...] = ("reset", "invite")


class OrgEmailTemplate(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """A tenant's override of an auth email's subject + HTML body, per ``(kind, locale)``.

    One row per ``(org_id, kind, locale)``; a **missing row means "use the built-in default"**
    (the catalog-rendered plaintext, #161 tier 1). ``subject``/``body_html`` are tenant-authored
    text, not secrets, so plain ``Text`` (unlike the encrypted transport config). The HTML is
    sanitised on write *and* on send (:mod:`app.core.email.templates`); variables ``{brand}``,
    ``{name}`` and ``{link}`` are substituted at send time.
    """

    __tablename__ = "org_email_templates"
    __table_args__ = (
        UniqueConstraint("org_id", "kind", "locale", name="uq_org_email_templates_kind_locale"),
    )

    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    locale: Mapped[str] = mapped_column(String(8), nullable=False)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_html: Mapped[str | None] = mapped_column(Text, nullable=True)
