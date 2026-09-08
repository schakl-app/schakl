"""Logged hours are a record (docs/TRASH.md): they are what got invoiced, or what still
will be. A client with hours on it is archived, never deleted."""

from __future__ import annotations

from app.core.trash import TrashDependent, count_by_column

TIME_TRASH_DEPENDENTS = (
    TrashDependent(
        key="time.entries",
        label_key="trash.dependent.time.entries",
        blocks=True,
        count=count_by_column("time_entries", "company_id"),
    ),
)
