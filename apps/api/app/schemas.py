"""Shared API schemas (CLAUDE.md §9)."""

from __future__ import annotations

import uuid
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class AssigneeWrite(BaseModel):
    """An employee assigned to a company or project; ``is_primary`` stars the responsible one.

    A list with no star promotes its first entry — the picker's own default.
    """

    user_id: uuid.UUID
    is_primary: bool = False


class AssigneeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    is_primary: bool


class Page(BaseModel, Generic[T]):
    """page/limit pagination envelope."""

    items: list[T]
    total: int
    limit: int
    offset: int


class PanelData(BaseModel):
    """One composed panel on a host entity's detail view (the "attach to company" hub)."""

    key: str
    title_key: str          # i18n key
    position: int
    data: dict[str, Any]
