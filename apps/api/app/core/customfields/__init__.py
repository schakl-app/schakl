"""Per-tenant custom fields (CLAUDE.md §13).

The **core, cross-cutting** capability: a tenant defines typed, optionally-required attributes on
any customizable entity type. An entity opts in with ``CustomizableMixin`` (adds the ``custom``
JSONB column and registers its ``entity_type``); ``CustomFieldsService`` validates ``custom`` on
every write against the tenant's definitions and CRUDs those definitions. Modules opt their
entities in as they are built (P1: ``company`` + ``contact``).
"""

from app.core.customfields.mixin import CustomizableMixin
from app.core.customfields.models import CustomFieldDefinition
from app.core.customfields.registry import (
    customizable_entity_types,
    register_customizable,
)
from app.core.customfields.scoping import (
    CustomFieldScopeSpec,
    ScopeOption,
    applicable,
    applies,
    register_scopes,
    row_scope_from,
    scopes_for,
)
from app.core.customfields.service import CustomFieldsService
from app.core.customfields.types import CustomFieldType

__all__ = [
    "CustomFieldDefinition",
    "CustomFieldScopeSpec",
    "CustomFieldType",
    "CustomFieldsService",
    "CustomizableMixin",
    "ScopeOption",
    "applicable",
    "applies",
    "customizable_entity_types",
    "register_customizable",
    "register_scopes",
    "row_scope_from",
    "scopes_for",
]
