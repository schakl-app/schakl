"""Registry of customizable entity types.

Modules opt an entity in (via ``CustomizableMixin``) so the tenant-admin UI can offer custom
fields on it without any per-module code. Exposed through the API so clients can enumerate
which entity types accept custom fields.
"""

from __future__ import annotations

_CUSTOMIZABLE_ENTITY_TYPES: set[str] = set()


def register_customizable(entity_type: str) -> None:
    _CUSTOMIZABLE_ENTITY_TYPES.add(entity_type)


def customizable_entity_types() -> list[str]:
    return sorted(_CUSTOMIZABLE_ENTITY_TYPES)
