"""Pydantic schemas for the domains module (issue #90, CLAUDE.md §9).

``DomainRead`` returns the raw ``*_provider_id`` FKs **and** resolved display helpers
(``company_name``, ``*_provider_name``) plus the two parties as labelled ``PartyReadRef``s, so a
client can render a domain row without a second round-trip. ``custom`` carries the tenant's
per-entity custom values (§13).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.party.schemas import PartyReadRef, PartyRef
from app.modules.domains.models import DomainStatus


def normalize_domain_name(value: object) -> object:
    """Reduce whatever the user typed or pasted to the bare root domain.

    People paste full URLs ("https://www.example.nl/pagina") and type "www." out of habit;
    a domain record is the apex, so scheme, credentials, port, path and a leading "www."
    are all stripped, and the host is lowercased. Non-strings pass through for Pydantic to
    reject; a value that strips to nothing fails the field's ``min_length``.
    """
    if not isinstance(value, str):
        return value
    name = value.strip().lower()
    if "://" in name:
        name = name.split("://", 1)[1]
    for sep in ("/", "?", "#"):
        name = name.split(sep, 1)[0]
    if "@" in name:
        name = name.rsplit("@", 1)[1]
    name = name.split(":", 1)[0]
    # Interleaved so "www." reduces to nothing and ".www.example.nl" to "example.nl".
    while True:
        stripped = name.strip(".")
        if stripped == "www":
            stripped = ""
        elif stripped.startswith("www."):
            stripped = stripped[4:]
        if stripped == name:
            return name
        name = stripped


class MxRecord(BaseModel):
    priority: int
    exchange: str


class DomainBase(BaseModel):
    name: str = Field(min_length=1, max_length=253)

    _normalize_name = field_validator("name", mode="before")(normalize_domain_name)
    status: DomainStatus = DomainStatus.ACTIVE
    redirect_url: str | None = Field(default=None, max_length=512)
    registrar_provider_id: uuid.UUID | None = None
    dns_provider_id: uuid.UUID | None = None
    registry_contact: PartyRef | None = None
    email_enabled: bool = False
    email_provider_id: uuid.UUID | None = None
    email_contact: PartyRef | None = None
    custom: dict[str, Any] = Field(default_factory=dict)


class DomainCreate(DomainBase):
    company_id: uuid.UUID


class DomainUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=253)

    _normalize_name = field_validator("name", mode="before")(normalize_domain_name)
    company_id: uuid.UUID | None = None
    status: DomainStatus | None = None
    redirect_url: str | None = Field(default=None, max_length=512)
    registrar_provider_id: uuid.UUID | None = None
    dns_provider_id: uuid.UUID | None = None
    registry_contact: PartyRef | None = None
    email_enabled: bool | None = None
    email_provider_id: uuid.UUID | None = None
    email_contact: PartyRef | None = None
    custom: dict[str, Any] | None = None


class DomainRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    company_id: uuid.UUID
    company_name: str = ""
    status: DomainStatus
    redirect_url: str | None = None
    registrar_provider_id: uuid.UUID | None = None
    registrar_provider_name: str | None = None
    dns_provider_id: uuid.UUID | None = None
    dns_provider_name: str | None = None
    registry_contact: PartyReadRef | None = None
    email_enabled: bool = False
    email_provider_id: uuid.UUID | None = None
    email_provider_name: str | None = None
    email_contact: PartyReadRef | None = None
    # Fetched from public DNS on a schedule (#92, #125); NULL until first checked.
    nameservers: list[str] | None = None
    dnssec: bool | None = None
    mx_records: list[MxRecord] | None = None
    dns_checked_at: datetime | None = None
    custom: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
