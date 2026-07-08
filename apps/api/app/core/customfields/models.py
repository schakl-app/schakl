"""``CustomFieldDefinition`` — a tenant's typed attribute on an entity type (CLAUDE.md §13).

Org-scoped (RLS-forced in its migration): each tenant defines its own fields per ``entity_type``.
``key`` is an immutable slug, unique within ``(org_id, entity_type)``. ``label_i18n`` holds the
per-locale labels as **tenant data** (``{"nl": ..., "en": ...}``) — not Paraglide catalog keys.
Stored values live in each customizable entity's ``custom`` JSONB column, keyed by ``key``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base


class CustomFieldDefinition(
    UUIDPrimaryKeyMixin,
    OrgScopedMixin,
    TimestampMixin,
    Base,
):
    __tablename__ = "custom_field_definitions"
    __table_args__ = (
        UniqueConstraint("org_id", "entity_type", "key", name="uq_custom_field_org_entity_key"),
    )

    entity_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    # Per-locale labels, e.g. {"nl": "Sector", "en": "Industry"} — tenant data, not i18n keys.
    label_i18n: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False, default=dict)
    data_type: Mapped[str] = mapped_column(String(32), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # For select / multi_select: [{"value": str, "label_i18n": {locale: str}}].
    options_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    # Per-type rules: {min, max, regex, default, help_i18n:{locale:str}}.
    config_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
