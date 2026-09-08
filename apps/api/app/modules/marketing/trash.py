"""A marketing link is a pointer into somebody else's account (a GA4 property, a Search
Console site) plus the numbers synced from it — configuration, not a record. It goes with
the client, and is named so the loss of the synced history is stated rather than discovered
(docs/TRASH.md)."""

from __future__ import annotations

from app.core.trash import TrashDependent, count_by_column

MARKETING_TRASH_DEPENDENTS = (
    TrashDependent(
        key="marketing.links",
        label_key="trash.dependent.marketing.links",
        blocks=False,
        count=count_by_column("marketing_links", "company_id"),
    ),
)
