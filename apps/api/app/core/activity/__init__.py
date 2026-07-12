"""Core activity trail (issue #67) — a record's paper trail as a cross-cutting capability.

Public surface: the ``AuditableMixin`` a module opts an entity in with, the registry that lists
auditable types, and the ``ActivityService`` a service records through. See CLAUDE.md §6/§9.
"""

from __future__ import annotations

from app.core.activity.mixin import AuditableMixin
from app.core.activity.registry import (
    auditable_entity_types,
    is_auditable,
    register_auditable,
)
from app.core.activity.service import ActivityService

__all__ = [
    "AuditableMixin",
    "ActivityService",
    "auditable_entity_types",
    "is_auditable",
    "register_auditable",
]
