"""``mollie`` request/response models (issue #267).

Every name is **prefixed**. A bare ``AccountRead`` or ``PaymentRead`` here would collide with
another module's component of the same name, and FastAPI resolves a collision by qualifying
*both* — silently renaming the other module's schema in the generated client and breaking its
web callers on the next ``gen:client``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.mollie.models import MollieAccountStatus, MollieMode


def _blank_to_none(value: object) -> object:
    return None if isinstance(value, str) and not value.strip() else value


class MollieAccountRead(BaseModel):
    """One connected Mollie key, as the settings screen sees it. **Never the key.**"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    #: Whether a key is stored at all. The key itself never leaves the server.
    api_key_configured: bool = True
    #: ``live`` or ``test``, derived from the stored key's own prefix — never entered, so it
    #: cannot disagree with reality.
    mode: MollieMode
    #: The methods Mollie reported at the last verify. Empty until one has run.
    methods: list[str] = Field(default_factory=list)
    provider_id: uuid.UUID | None = None
    active: bool
    status: MollieAccountStatus
    last_verified_at: datetime | None = None
    #: Mollie's own words for the last failure. Untranslatable, and shown as-is.
    last_error: str | None = None
    #: The callback URL Mollie must be able to reach. Shown because a deployment behind an
    #: access proxy has to allow it explicitly, and "payments never arrive" is otherwise a
    #: mystery with no clue on screen.
    webhook_url: str = ""
    created_at: datetime
    updated_at: datetime


class MollieAccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    #: A Mollie API key (``test_…`` or ``live_…``). Stored encrypted, never returned.
    api_key: str = Field(min_length=8, max_length=255)
    provider_id: uuid.UUID | None = None
    active: bool = True

    @field_validator("name")
    @classmethod
    def _trim(cls, value: str) -> str:
        return value.strip()

    @field_validator("api_key")
    @classmethod
    def _looks_like_a_key(cls, value: str) -> str:
        """Refuse anything that is not shaped like a Mollie key.

        Not security — the credential proves itself by working — but a paste of the wrong
        secret is a mistake worth catching before it is encrypted, stored, and then reported
        as "Mollie rejected your credential" with no hint that it was never one.
        """
        key = value.strip()
        if not (key.startswith("test_") or key.startswith("live_")):
            raise ValueError("errors.mollie.not_an_api_key")
        return key


class MollieAccountUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    #: Omit to keep the stored key; send a new one to rotate. Never send an empty string to
    #: clear it — a payment credential is removed by deleting the account, not by blanking it.
    api_key: str | None = Field(default=None, max_length=255)
    provider_id: uuid.UUID | None = None
    active: bool | None = None

    _blank_key = field_validator("api_key", mode="before")(_blank_to_none)

    @field_validator("api_key")
    @classmethod
    def _looks_like_a_key(cls, value: str | None) -> str | None:
        key = (value or "").strip()
        if not key:
            return None
        if not (key.startswith("test_") or key.startswith("live_")):
            raise ValueError("errors.mollie.not_an_api_key")
        return key


class MollieAccountVerifyResult(BaseModel):
    """The outcome of testing a credential. **Never raises** — see the service.

    ``ok=False`` with the row still saved is a real and common state: a rejected credential is
    still a stored credential, and telling somebody their key is wrong is more useful than
    refusing to remember what they typed.
    """

    ok: bool
    mode: MollieMode | None = None
    methods: list[str] = Field(default_factory=list)
    error: str | None = None
