"""Pydantic schemas for the companies module (CLAUDE.md §6, §9)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.modules.companies.models import CompanyStatus


def _blank_to_none(value: Any) -> Any:
    """Empty string normalises to ``NULL`` — not every client has an invoice address yet."""
    if isinstance(value, str) and value.strip() == "":
        return None
    return value


class CompanyBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    website: str | None = Field(default=None, max_length=512)
    notes: str | None = None
    status: CompanyStatus = CompanyStatus.ACTIVE
    # Org member accountable for this client; defaults down onto new projects/tasks.
    responsible_user_id: uuid.UUID | None = None
    # Where invoices go — often a different mailbox than the primary contact (#30, #31).
    invoice_email: EmailStr | None = Field(default=None, max_length=320)
    # Per-tenant custom values (validated against tenant definitions in P1).
    custom: dict[str, Any] = Field(default_factory=dict)

    _normalize_invoice_email = field_validator("invoice_email", mode="before")(_blank_to_none)


class CompanyCreate(CompanyBase):
    pass


class CompanyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    website: str | None = Field(default=None, max_length=512)
    notes: str | None = None
    status: CompanyStatus | None = None
    responsible_user_id: uuid.UUID | None = None
    invoice_email: EmailStr | None = Field(default=None, max_length=320)
    custom: dict[str, Any] | None = None

    _normalize_invoice_email = field_validator("invoice_email", mode="before")(_blank_to_none)


class CompanyRead(CompanyBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
