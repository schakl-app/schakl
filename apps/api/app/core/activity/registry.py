"""Registry of auditable entity types (issue #67).

A module opts an entity in (via ``AuditableMixin``) so the core activity panel is offered on
its detail page and the activity feed accepts its ``entity_type`` — without any per-module
panel code. Mirrors ``customfields.registry`` (CLAUDE.md §13): the same core-capability shape.
"""

from __future__ import annotations

_AUDITABLE_ENTITY_TYPES: set[str] = set()
#: entity_type -> the owning module's *read* permission. Reading a record's trail requires being
#: able to read the record itself (audit F7): ``activity.read`` alone is a blanket grant every
#: role holds, so without this a member could read the change history of records in modules they
#: hold no read permission for. Core keeps no module permission list of its own — each module
#: supplies its own key when it opts an entity in (via ``AuditableMixin.__activity_read_permission__``
#: or the ``register_auditable`` argument), so the coupling stays module-owned.
_READ_PERMISSION: dict[str, str] = {}


def register_auditable(entity_type: str, read_permission: str | None = None) -> None:
    _AUDITABLE_ENTITY_TYPES.add(entity_type)
    if read_permission:
        _READ_PERMISSION[entity_type] = read_permission


def auditable_entity_types() -> list[str]:
    return sorted(_AUDITABLE_ENTITY_TYPES)


def is_auditable(entity_type: str) -> bool:
    return entity_type in _AUDITABLE_ENTITY_TYPES


def read_permission_for(entity_type: str) -> str | None:
    """The permission a caller must hold to read this entity type's trail, if the module declared
    one. ``None`` means the type opted in without a read gate (fall back to ``activity.read``)."""
    return _READ_PERMISSION.get(entity_type)
