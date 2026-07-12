"""Per-entity CSV import/export — a core, cross-cutting capability (issue #77, CLAUDE.md §13
pattern): core owns parsing, validation, dry-run and the routes; a module opts an entity in by
declaring an :class:`ImpexDescriptor` on its ``ModuleDescriptor``."""

from app.core.impex.schemas import ImportReport, ImportRowError
from app.core.impex.spec import ImpexColumn, ImpexDescriptor

__all__ = ["ImpexColumn", "ImpexDescriptor", "ImportReport", "ImportRowError"]
