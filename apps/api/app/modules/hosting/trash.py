"""A hosting account outlives its client (docs/TRASH.md).

The server is still running and still billed. ``hosting.company_id`` is ``SET NULL`` and a
clientless account is a real state (shared infrastructure) — but *becoming* one because
somebody deleted the client is a fact nobody chose, so it blocks: detach or move the
account first, or archive the client.
"""

from __future__ import annotations

from app.core.trash import TrashDependent, count_by_column

HOSTING_TRASH_DEPENDENTS = (
    TrashDependent(
        key="hosting.accounts",
        label_key="trash.dependent.hosting.accounts",
        blocks=True,
        count=count_by_column("hosting", "company_id"),
    ),
)
