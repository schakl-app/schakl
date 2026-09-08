"""A project is work done for a client and the hours on it became invoices (docs/TRASH.md).
``projects.company_id`` is ``SET NULL`` and the service then refuses ever to clear it — a
cascade would leave a row the API cannot legally edit. So it blocks."""

from __future__ import annotations

from app.core.trash import TrashDependent, count_by_column

PROJECT_TRASH_DEPENDENTS = (
    TrashDependent(
        key="projects.projects",
        label_key="trash.dependent.projects.projects",
        blocks=True,
        count=count_by_column("projects", "company_id"),
    ),
)
