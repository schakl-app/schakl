"""Pydantic schemas for the companies module (CLAUDE.md §6, §9)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.modules.companies.models import CompanyStatus
from app.schemas import AssigneeRead, AssigneeWrite, CompanyBudgetHours


def _blank_to_none(value: Any) -> Any:
    """Empty string normalises to ``NULL`` — not every client has an invoice address yet."""
    if isinstance(value, str) and value.strip() == "":
        return None
    return value


class CompanyBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    website: str | None = Field(default=None, max_length=512)
    # E.164 (issue #256); the service validates via ``app.core.phone`` on write.
    phone: str | None = Field(default=None, max_length=32)
    notes: str | None = None
    status: CompanyStatus = CompanyStatus.ACTIVE
    # The primary assignee, mirrored from ``assignees``. Read it; on write prefer ``assignees``
    # — sending this alone still works and moves the star (see ``CompanyService.update``).
    responsible_user_id: uuid.UUID | None = None
    # Where invoices go — often a different mailbox than the primary contact (#30, #31).
    invoice_email: EmailStr | None = Field(default=None, max_length=320)
    # Billing identity (issue #11) — optional; document issue judges completeness (#207).
    vat_number: str | None = Field(default=None, max_length=32)
    coc_number: str | None = Field(default=None, max_length=32)
    address_line1: str | None = Field(default=None, max_length=255)
    address_line2: str | None = Field(default=None, max_length=255)
    postal_code: str | None = Field(default=None, max_length=16)
    city: str | None = Field(default=None, max_length=120)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    # Per-tenant custom values (validated against tenant definitions in P1).
    custom: dict[str, Any] = Field(default_factory=dict)

    _normalize_invoice_email = field_validator("invoice_email", mode="before")(_blank_to_none)
    _normalize_billing = field_validator(
        "vat_number", "coc_number", "address_line1", "address_line2",
        "postal_code", "city", "country", "phone",
        mode="before",
    )(_blank_to_none)


class CompanyCreate(CompanyBase):
    # Every employee working this client, one of them starred. ``None`` (not ``[]``) means the
    # caller didn't say, and ``responsible_user_id`` alone decides — the pre-assignees shape.
    assignees: list[AssigneeWrite] | None = None


class CompanyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    website: str | None = Field(default=None, max_length=512)
    phone: str | None = Field(default=None, max_length=32)
    notes: str | None = None
    status: CompanyStatus | None = None
    responsible_user_id: uuid.UUID | None = None
    assignees: list[AssigneeWrite] | None = None
    invoice_email: EmailStr | None = Field(default=None, max_length=320)
    vat_number: str | None = Field(default=None, max_length=32)
    coc_number: str | None = Field(default=None, max_length=32)
    address_line1: str | None = Field(default=None, max_length=255)
    address_line2: str | None = Field(default=None, max_length=255)
    postal_code: str | None = Field(default=None, max_length=16)
    city: str | None = Field(default=None, max_length=120)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    custom: dict[str, Any] | None = None

    _normalize_invoice_email = field_validator("invoice_email", mode="before")(_blank_to_none)
    _normalize_billing = field_validator(
        "vat_number", "coc_number", "address_line1", "address_line2",
        "postal_code", "city", "country", "phone",
        mode="before",
    )(_blank_to_none)


class CompanyRead(CompanyBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    # The client's logo (#196); served tenant+horizon-scoped at /companies/{id}/logo.
    logo_file_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime
    # Primary first, then oldest assignment first.
    assignees: list[AssigneeRead] = Field(default_factory=list)
    # Budget burn rolled up from the client's projects. Only present when the list was asked for
    # it (``?hours=true``) — a hidden column must not pay for an aggregate (#24, #25).
    hours: CompanyBudgetHours | None = None
