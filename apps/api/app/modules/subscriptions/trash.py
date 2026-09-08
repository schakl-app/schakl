"""An agreement outlives its client (docs/TRASH.md): it has billing history in invoicing's
claim tables and a cycle the cron reasons about, and an ended agreement is still the record
of what was sold. A client holding one is archived, never deleted."""

from __future__ import annotations

from app.core.trash import TrashDependent, count_by_column

SUBSCRIPTION_TRASH_DEPENDENTS = (
    TrashDependent(
        key="subscriptions.subscriptions",
        label_key="trash.dependent.subscriptions.subscriptions",
        blocks=True,
        count=count_by_column("subscriptions", "company_id"),
    ),
)
