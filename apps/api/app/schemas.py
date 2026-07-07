"""Shared API schemas (CLAUDE.md §9)."""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


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
