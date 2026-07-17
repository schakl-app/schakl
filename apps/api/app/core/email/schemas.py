"""Request/response models for the org e-mail settings surface (#17)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, EmailStr, Field

EmailProvider = Literal["smtp", "brevo", "sendgrid", "smtp2go", "instance"]


class EmailSettingsWrite(BaseModel):
    """Upsert payload. Secret fields left empty on an update keep the stored value
    (as long as the provider is unchanged) — the API never plays the secret back.

    ``provider="instance"`` (the operator-provided transport) needs no ``from_email`` —
    mail leaves from the instance's own address; the handler enforces presence for every
    other provider."""

    provider: EmailProvider
    from_email: EmailStr | None = None
    from_name: str = Field(min_length=1, max_length=255)
    reply_to: EmailStr | None = None
    # smtp
    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    security: Literal["starttls", "ssl", "none"] | None = None
    username: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, max_length=1024)
    # brevo / sendgrid / smtp2go
    api_key: str | None = Field(default=None, max_length=1024)


class EmailSettingsRead(BaseModel):
    """The stored configuration minus its secrets: enough to repopulate the form."""

    provider: EmailProvider
    from_email: str
    from_name: str
    reply_to: str | None = None
    host: str | None = None
    port: int | None = None
    security: str | None = None
    username: str | None = None
    #: A secret (password / API key) is stored; the value itself is never returned.
    has_secret: bool = False


class EmailTestResult(BaseModel):
    ok: bool
    error: str | None = None


# --------------------------------------------------------------------------- #
# Tenant-customisable auth email templates (#161 tier 2)
# --------------------------------------------------------------------------- #
EmailTemplateKind = Literal["reset", "invite"]


class EmailTemplateItem(BaseModel):
    """One ``(kind, locale)`` slot: the tenant override (``None`` = default) plus the built-in
    default, so the editor can show placeholders and a "reset to default" is just clearing it."""

    kind: EmailTemplateKind
    locale: str
    subject: str | None = None
    body_html: str | None = None
    default_subject: str
    default_body_html: str


class EmailTemplatesRead(BaseModel):
    locales: list[str]
    variables: list[str]
    templates: list[EmailTemplateItem]


class EmailTemplateWrite(BaseModel):
    kind: EmailTemplateKind
    locale: str = Field(min_length=2, max_length=8)
    #: Blank subject *and* body deletes the override — the built-in default applies again.
    subject: str | None = Field(default=None, max_length=500)
    body_html: str | None = Field(default=None, max_length=20000)


class EmailTemplateTest(BaseModel):
    """Send a preview of the draft on screen (falls back to the stored/default when omitted)."""

    kind: EmailTemplateKind
    locale: str = Field(min_length=2, max_length=8)
    subject: str | None = Field(default=None, max_length=500)
    body_html: str | None = Field(default=None, max_length=20000)
