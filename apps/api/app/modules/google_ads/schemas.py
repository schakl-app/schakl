"""Wire shapes for the google_ads module. Business-licensed — see LICENSE.

Named with a ``GoogleAds`` prefix throughout. A generic Pydantic model name makes FastAPI
qualify **both** modules' components in the OpenAPI document, which silently renames another
module's schema in the generated TypeScript client — a diff nobody reviewing this module would
think to look at.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class GoogleAdsSettingsRead(BaseModel):
    """The org's Ads configuration. **Never carries the developer token itself** — only whether
    one is configured, and where the effective one comes from."""

    developer_token_configured: bool
    #: ``true`` when the deprecated ``SCHAKL_GOOGLE_ADS_DEVELOPER_TOKEN`` env var is what would
    #: answer. Shown so an admin staring at an empty field understands why Ads works anyway.
    env_token_configured: bool
    default_login_customer_id: str | None = None
    writes_enabled: bool = True


class GoogleAdsSettingsWrite(BaseModel):
    """An **empty string keeps the stored secret**; an explicit ``null`` clears it.

    The write-only-secret contract every credential screen here uses. A form that posts the
    field blank because the user did not retype it must not wipe a working credential.
    """

    developer_token: str | None = None
    default_login_customer_id: str | None = None
    writes_enabled: bool | None = None


class GoogleAdsAccountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_id: str
    #: ``123-456-7890`` — the form Google's own UI shows. Computed, so no screen re-implements it.
    customer_id_formatted: str
    login_customer_id: str | None = None
    company_id: uuid.UUID | None = None
    company_name: str | None = None
    connection_id: uuid.UUID | None = None
    descriptive_name: str
    currency_code: str | None = None
    time_zone: str | None = None
    is_manager: bool = False
    test_account: bool = False
    conversion_tracking_status: str | None = None
    optimization_score: float | None = None
    active: bool = True
    status: str
    #: Google's own sentence about the last failure, scrubbed of credentials. Not an i18n key —
    #: it is provider text, which is why it lives here and never in the error envelope (§9).
    last_error: str | None = None
    last_verified_at: datetime | None = None
    last_synced_at: datetime | None = None


class GoogleAdsAccountCreate(BaseModel):
    """Link an account the picker offered. ``customer_id`` is normalised on write, so the
    hyphenated form a human pastes and the bare form the picker sends are the same row."""

    customer_id: str = Field(min_length=1, max_length=32)
    company_id: uuid.UUID | None = None
    login_customer_id: str | None = Field(default=None, max_length=32)
    descriptive_name: str = Field(default="", max_length=255)
    currency_code: str | None = Field(default=None, max_length=3)


class GoogleAdsAccountUpdate(BaseModel):
    """Only what schakl *decided* is editable. The name, currency, timezone and manager flag are
    what Google last said, refreshed by verify — typing over them would make the row disagree
    with the account it describes and nothing would ever put it back."""

    company_id: uuid.UUID | None = None
    login_customer_id: str | None = Field(default=None, max_length=32)
    active: bool | None = None


class GoogleAdsAvailableAccount(BaseModel):
    customer_id: str
    customer_id_formatted: str
    descriptive_name: str
    login_customer_id: str | None = None
    currency_code: str | None = None
    hint: str
    #: The picker hides nothing — it *marks*. An account already linked to another client is
    #: exactly what someone needs to see when they are wondering why it is missing.
    already_linked: bool = False


class GoogleAdsPickerRead(BaseModel):
    accounts: list[GoogleAdsAvailableAccount] = Field(default_factory=list)
    #: **Read before drawing conclusions.** A manager whose child list was capped reports it
    #: here; a picker that lists 500 of 900 accounts and says nothing looks like 900 does not
    #: exist (CLAUDE.md §17).
    warnings: list[str] = Field(default_factory=list)
