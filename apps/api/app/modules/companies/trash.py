"""A client goes in the trash, not out of the database (docs/TRASH.md).

The spec says which model, which permission and how a row is named; what a delete *means*
for a client — the invoices, domains and agreements that stop it, the tasks and links that
go with it — is contributed by the modules that hold those rows, each from its own
``trash.py``. Companies names none of them (CLAUDE.md §6).
"""

from __future__ import annotations

from app.core.trash import TrashableSpec
from app.modules.companies.models import Company

COMPANY_TRASH = TrashableSpec(
    entity_type=Company.__entity_type__,
    model=Company,
    delete_permission="companies.company.delete",
    label=lambda company: company.name,
)
