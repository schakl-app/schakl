"""Pydantic schemas for the companies module (CLAUDE.md §6, §9)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.numbering import format_valid
from app.modules.companies.models import CompanyStatus
from app.schemas import AssigneeRead, AssigneeWrite, CompanyBudgetHours


def _blank_to_none(value: Any) -> Any:
    """Empty string normalises to ``NULL`` — not every client has an invoice address yet."""
    if isinstance(value, str) and value.strip() == "":
        return None
    return value


class CompanyBase(BaseModel):
    #: What this client is called — the label every screen prints.
    name: str = Field(min_length=1, max_length=255)
    #: The entity a document is addressed to (issue #11 follow-up). Blank/``None`` means *the
    #: label is also the legal name*, which is why nothing here is required and why no read ever
    #: touches it directly — ``app.core.naming.document_name`` resolves the pair.
    legal_name: str | None = Field(default=None, max_length=255)
    # Klantnummer. Omit it and the org's numbering allocates one (when ``client_number_auto``);
    # send one and it is taken as given, subject to org-scoped uniqueness.
    client_number: str | None = Field(default=None, max_length=40)
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
    house_number: str | None = Field(default=None, max_length=32)
    address_line2: str | None = Field(default=None, max_length=255)
    postal_code: str | None = Field(default=None, max_length=16)
    city: str | None = Field(default=None, max_length=120)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    # Per-tenant custom values (validated against tenant definitions in P1).
    custom: dict[str, Any] = Field(default_factory=dict)

    _normalize_invoice_email = field_validator("invoice_email", mode="before")(_blank_to_none)
    _normalize_billing = field_validator(
        "vat_number", "coc_number", "address_line1", "house_number", "address_line2",
        "postal_code", "city", "country", "phone", "client_number", "legal_name",
        mode="before",
    )(_blank_to_none)


class CompanyCreate(CompanyBase):
    # Every employee working this client, one of them starred. ``None`` (not ``[]``) means the
    # caller didn't say, and ``responsible_user_id`` alone decides — the pre-assignees shape.
    assignees: list[AssigneeWrite] | None = None


class CompanyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    legal_name: str | None = Field(default=None, max_length=255)
    client_number: str | None = Field(default=None, max_length=40)
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
    house_number: str | None = Field(default=None, max_length=32)
    address_line2: str | None = Field(default=None, max_length=255)
    postal_code: str | None = Field(default=None, max_length=16)
    city: str | None = Field(default=None, max_length=120)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    custom: dict[str, Any] | None = None

    _normalize_invoice_email = field_validator("invoice_email", mode="before")(_blank_to_none)
    _normalize_billing = field_validator(
        "vat_number", "coc_number", "address_line1", "house_number", "address_line2",
        "postal_code", "city", "country", "phone", "client_number", "legal_name",
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


# --------------------------------------------------------------------------- #
# Settings — client numbering
# --------------------------------------------------------------------------- #
class CompanyNumberingWrite(BaseModel):
    """Partial update: every field optional, applied with ``exclude_unset``.

    Named for what it carries rather than for its table (``company_settings``): the marketing
    module already publishes a schema called ``CompanySettingsRead``, and two same-named schemas
    make FastAPI fully-qualify **both** in the OpenAPI spec — which would silently rename the
    other module's type in the generated client for no reason of its own.
    """

    client_number_format: str | None = Field(default=None, max_length=60)
    #: Editable so an instance can align with the numbering it already uses elsewhere. The
    #: allocator guards uniqueness, so a rewind can only collide (and skip), never overwrite.
    client_number_next_seq: int | None = Field(default=None, ge=1)
    client_number_reset_yearly: bool | None = None
    client_number_auto: bool | None = None

    @field_validator("client_number_format")
    @classmethod
    def _format_ok(cls, value: str | None) -> str | None:
        if value is not None and not format_valid(value):
            raise ValueError("errors.companies.invalid_number_format")
        return value


class CompanyNumberingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    client_number_format: str
    client_number_next_seq: int
    client_number_seq_year: int | None
    client_number_reset_yearly: bool
    client_number_auto: bool


class ClientNumberBackfillResult(BaseModel):
    """What the "number existing companies" action did — it only ever fills blanks."""

    numbered: int
