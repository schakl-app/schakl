"""Per-tenant custom fields (CLAUDE.md §13).

P0 ships only the *seam*: the ``custom`` JSONB column via ``CustomizableMixin`` and a registry
of customizable entity types. The definitions store, dynamic validation, and admin UI arrive in
P1 — until then ``custom`` is persisted and echoed back but not validated against tenant
definitions.
"""

from app.core.customfields.mixin import CustomizableMixin
from app.core.customfields.registry import (
    customizable_entity_types,
    register_customizable,
)

__all__ = [
    "CustomizableMixin",
    "customizable_entity_types",
    "register_customizable",
]
