"""The trash can — a core, cross-cutting capability (docs/TRASH.md).

Import only the descriptive half from here (the mixin and the specs): ``service``, ``router``
and ``jobs`` import ``tenancy``, which imports the mixin, so pulling them in from this package
would cycle.
"""

from app.core.trash.mixin import TrashableMixin, is_trashable, trashable_table
from app.core.trash.spec import (
    TrashableSpec,
    TrashDependent,
    count_by_column,
    dependents_of,
    register_trash_dependent,
)

__all__ = [
    "TrashDependent",
    "TrashableMixin",
    "TrashableSpec",
    "count_by_column",
    "dependents_of",
    "is_trashable",
    "register_trash_dependent",
    "trashable_table",
]
