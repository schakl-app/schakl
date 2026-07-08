"""``Company`` — the hub every other module attaches to (CLAUDE.md §6).

Customizable (per-tenant custom fields via ``CustomizableMixin``) and org-scoped. Future
attachable types (contacts, websites, hosting, …) carry ``company_id`` + ``org_id`` and
contribute panels — no edits to the company page required.
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.customfields import CustomizableMixin
from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base


class CompanyStatus(StrEnum):
    """Client lifecycle; status transitions drive task-template automation (§6 events)."""

    LEAD = "lead"
    ONBOARDING = "onboarding"
    ACTIVE = "active"
    OFFBOARDING = "offboarding"
    ARCHIVED = "archived"


class Company(
    UUIDPrimaryKeyMixin,
    OrgScopedMixin,
    TimestampMixin,
    CustomizableMixin,
    Base,
):
    __tablename__ = "companies"
    __entity_type__ = "company"  # registers as customizable

    # GIN index on the JSONB custom-fields column (CLAUDE.md §13).
    __table_args__ = (
        Index("ix_companies_custom", "custom", postgresql_using="gin"),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    website: Mapped[str | None] = mapped_column(String(512), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CompanyStatus.ACTIVE.value, index=True
    )
