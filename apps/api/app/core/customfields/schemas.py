"""Pydantic schemas for custom-field definitions (CLAUDE.md §13, §9)."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.customfields.types import CustomFieldType

# Immutable slug: lowercase letters/digits/underscores, must start with a letter.
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class CustomFieldOption(BaseModel):
    value: str = Field(min_length=1, max_length=128)
    label_i18n: dict[str, str] = Field(default_factory=dict)


class CustomFieldDefinitionBase(BaseModel):
    entity_type: str = Field(min_length=1, max_length=64)
    label_i18n: dict[str, str] = Field(default_factory=dict)
    data_type: CustomFieldType
    required: bool = False
    options_json: list[CustomFieldOption] = Field(default_factory=list)
    config_json: dict[str, Any] = Field(default_factory=dict)
    position: int = 0
    active: bool = True


class CustomFieldDefinitionCreate(CustomFieldDefinitionBase):
    key: str = Field(min_length=1, max_length=64)

    @field_validator("key")
    @classmethod
    def _valid_key(cls, v: str) -> str:
        if not _KEY_RE.match(v):
            raise ValueError("invalid_key")
        return v


class CustomFieldDefinitionUpdate(BaseModel):
    """``entity_type`` and ``key`` are immutable; everything else is editable."""

    label_i18n: dict[str, str] | None = None
    data_type: CustomFieldType | None = None
    required: bool | None = None
    options_json: list[CustomFieldOption] | None = None
    config_json: dict[str, Any] | None = None
    position: int | None = None
    active: bool | None = None


class CustomFieldScopeOptionRead(BaseModel):
    value: str
    label_i18n: dict[str, str] = Field(default_factory=dict)
    active: bool = True


class CustomFieldScopeRead(BaseModel):
    """One dimension a definition of this entity type may be narrowed to (``scoping.py``).

    ``key`` is what ``config_json.scope`` is keyed on; ``label_key`` names the dimension through
    i18n; ``options`` are the tenant's current choices, a deactivated one flagged rather than
    dropped so an existing scope keeps its label on the settings screen.
    """

    key: str
    label_key: str
    options: list[CustomFieldScopeOptionRead] = Field(default_factory=list)


class CustomFieldDefinitionRead(CustomFieldDefinitionBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    key: str
    created_at: datetime
    updated_at: datetime
