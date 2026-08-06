"""Per-entity bulk edit / delete — a core, cross-cutting capability (the CLAUDE.md §13/§17
pattern): core owns loading the selection, resolving the shared values, the per-row savepoint
and the routes; a module opts an entity in by declaring a :class:`BulkDescriptor` on its
``ModuleDescriptor``, borrowing the import shape it already has."""

from app.core.bulk.schemas import BulkActionFailure, BulkActionResult
from app.core.bulk.spec import BulkDescriptor, BulkField

__all__ = ["BulkActionFailure", "BulkActionResult", "BulkDescriptor", "BulkField"]
