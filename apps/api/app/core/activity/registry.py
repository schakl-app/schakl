"""Registry of auditable entity types (issue #67).

A module opts an entity in (via ``AuditableMixin``) so the core activity panel is offered on
its detail page and the activity feed accepts its ``entity_type`` — without any per-module
panel code. Mirrors ``customfields.registry`` (CLAUDE.md §13): the same core-capability shape.
"""

from __future__ import annotations

_AUDITABLE_ENTITY_TYPES: set[str] = set()


def register_auditable(entity_type: str) -> None:
    _AUDITABLE_ENTITY_TYPES.add(entity_type)


def auditable_entity_types() -> list[str]:
    return sorted(_AUDITABLE_ENTITY_TYPES)


def is_auditable(entity_type: str) -> bool:
    return entity_type in _AUDITABLE_ENTITY_TYPES
