"""Universal responsible **party** (issue #88, part of #87).

One reusable, typed polymorphic reference for *"who's responsible / who to contact"*, resolving
to one of four things:

* the **agency** — the tenant's own org (its ``org_settings`` brand) — the **default**;
* the **client company** — the record's own company, or an explicitly named one;
* an **employee** — a membership / user; or
* a **contact** — a client person.

It is a *value type*, not a table: a consuming entity stores the pair ``(<name>_party_type,
<name>_party_id)`` (see :func:`party_type_column` / :func:`party_id_column`) and defers validation
and label resolution to :class:`PartyService`. Reused by domains (registry + email contact),
websites (technical owner) and hosting (contact) — the same semantics everywhere, so the picker
and the rules live here in core rather than being copied per module (CLAUDE.md §3, §6).

``agency`` needs no id; ``company`` with a NULL id means *the record's own company*. Everything is
tenant-scoped: :class:`PartyService` rejects an id belonging to another tenant, so a party can
never point across the isolation boundary (Golden Rule 1).
"""

from __future__ import annotations

from app.core.party.models import PartyType, party_id_column, party_type_column
from app.core.party.schemas import PartyReadRef, PartyRef
from app.core.party.service import PartyService

__all__ = [
    "PartyReadRef",
    "PartyRef",
    "PartyService",
    "PartyType",
    "party_id_column",
    "party_type_column",
]
