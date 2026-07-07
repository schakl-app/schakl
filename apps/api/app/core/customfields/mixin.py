"""``CustomizableMixin`` — opt an entity into per-tenant custom fields (CLAUDE.md §13).

Adds a ``custom`` JSONB column (keyed by definition ``key``) and registers the entity's
``__entity_type__`` so the custom-fields core knows about it. Storage is JSONB, **not EAV**
(indexable, no join fan-out); add a GIN index in the owning module's migration.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.customfields.registry import register_customizable


class CustomizableMixin:
    #: Each customizable model sets this to its stable entity-type slug (e.g. "company").
    __entity_type__: str

    custom: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        entity_type = getattr(cls, "__entity_type__", None)
        if entity_type:
            register_customizable(entity_type)
