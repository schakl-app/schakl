"""The ``providers`` table (issue #89).

One row per outside service a tenant works with, grouped by ``kind``. Org-scoped and RLS-forced
like every domain table (CLAUDE.md §5). ``name`` is tenant free text ("Cloudflare"), so it is not
i18n'd; ``config`` is a per-provider JSONB blob reserved for later integration wiring.
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base


class ProviderKind(StrEnum):
    """The four services a provider can be. A single row is exactly one kind."""

    EMAIL = "email"
    DNS = "dns"
    REGISTRAR = "registrar"
    HOSTING = "hosting"


class Provider(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "providers"

    kind: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Reserved for per-provider structured bits (API endpoints, integration hooks) — no schema
    # change needed to start using it (issue #89).
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
