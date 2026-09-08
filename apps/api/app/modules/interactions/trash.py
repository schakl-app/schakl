"""A contact moment is a record of something that happened, so it is never destroyed with
the client: ``interactions.company_id`` is ``SET NULL`` and the note survives unattached, the
inbox's ordinary state. It hides while the client is in the trash and is detached when the
client is purged — which the dialog says (docs/TRASH.md)."""

from __future__ import annotations

from app.core.trash import TrashDependent, count_by_column

INTERACTION_TRASH_DEPENDENTS = (
    TrashDependent(
        key="interactions.interactions",
        label_key="trash.dependent.interactions.interactions",
        blocks=False,
        count=count_by_column("interactions", "company_id"),
    ),
)
