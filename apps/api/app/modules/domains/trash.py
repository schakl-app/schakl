"""A domain outlives its client (docs/TRASH.md).

The agency still holds it at the registrar and still pays for it whether or not the client
row exists, and a domain with no client is nonsense (§17) — so a client holding one cannot
be deleted. Move the domain to the right client, or archive the client.
"""

from __future__ import annotations

from app.core.trash import TrashDependent, count_by_column

DOMAIN_TRASH_DEPENDENTS = (
    TrashDependent(
        key="domains.domains",
        label_key="trash.dependent.domains.domains",
        blocks=True,
        count=count_by_column("domains", "company_id"),
    ),
)
