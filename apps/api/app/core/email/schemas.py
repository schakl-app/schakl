"""Request/response models for the org e-mail settings surface (#17)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, EmailStr, Field

EmailProvider = Literal["smtp", "brevo", "sendgrid", "smtp2go"]


class EmailSettingsWrite(BaseModel):
    """Upsert payload. Secret fields left empty on an update keep the stored value
    (as long as the provider is unchanged) — the API never plays the secret back."""

    provider: EmailProvider
    from_email: EmailStr
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
